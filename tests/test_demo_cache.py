"""Demo mode: cache key, fallback rule, demo flag. All network is mocked."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from qloo_mcp import demo_cache
from qloo_mcp.client import QlooAPIError, QlooClient
from qloo_mcp.schemas import SearchEntitiesInput, TasteMatchInput
from qloo_mcp.taste_match import run_taste_match

BASE = "https://qloo.test"
SHIPPED = Path(__file__).resolve().parents[1] / "fixtures" / "qloo"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("QLOO_API_KEY", "test-key")
    monkeypatch.setenv("QLOO_API_URL", BASE)
    monkeypatch.delenv("TASTE_MATCH_LLM_URL", raising=False)


def _put(path: str, params: dict, response: dict, source: str = "recorded") -> str:
    """Write a fixture file by hand, returning its name."""
    d = demo_cache.fixtures_dir()
    d.mkdir(parents=True, exist_ok=True)
    name = demo_cache.cache_key(path, params)
    doc = {
        "endpoint": path,
        "params": demo_cache.normalize_params(params),
        "source": source,
        "response": response,
    }
    (d / name).write_text(json.dumps(doc))
    return name


# ---- cache key ----------------------------------------------------------------


def test_cache_key_is_stable_across_order_case_and_whitespace():
    a = demo_cache.cache_key("/v2/insights", {"filter.type": "urn:entity:artist", "signal.interests.entities": "B,A", "take": "5"})
    b = demo_cache.cache_key("v2/insights/", {"take": "5", "signal.interests.entities": "a, b", "filter.type": "URN:entity:Artist "})
    assert a == b
    assert a.startswith("v2_insights__") and a.endswith(".json")


def test_cache_key_differs_by_endpoint_and_params():
    base = {"query": "nujabes", "take": "1"}
    keys = {
        demo_cache.cache_key("/search", base),
        demo_cache.cache_key("/v2/tags", base),
        demo_cache.cache_key("/search", {**base, "take": "2"}),
        demo_cache.cache_key("/search", {"query": "dilla", "take": "1"}),
    }
    assert len(keys) == 4


def test_cache_key_does_not_split_commas_in_free_text():
    assert demo_cache.cache_key("/search", {"query": "Earth, Wind"}) != demo_cache.cache_key("/search", {"query": "Wind, Earth"})


def test_cache_key_ignores_the_api_key():
    # The key is a header, never a param, so it cannot leak into a file name or file.
    assert "test-key" not in demo_cache.cache_key("/search", {"query": "x"})


# ---- record flag --------------------------------------------------------------


@respx.mock
async def test_no_record_without_flag():
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json={"results": []}))
    await QlooClient().search_entities(SearchEntitiesInput(query="Nujabes"))
    assert not demo_cache.fixtures_dir().exists()


@respx.mock
async def test_record_writes_fixture_without_key_or_demo_flag(monkeypatch):
    monkeypatch.setenv("QLOO_RECORD", "1")
    body = {"success": True, "results": [{"name": "Nujabes", "entity_id": "E1"}]}
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json=body))
    out = await QlooClient().search_entities(SearchEntitiesInput(query="Nujabes"))
    assert "demo" not in out
    files = list(demo_cache.fixtures_dir().glob("*.json"))
    assert len(files) == 1
    text = files[0].read_text()
    doc = json.loads(text)
    assert doc["response"] == body and doc["source"] == "recorded" and doc["endpoint"] == "/search"
    assert "test-key" not in text and "demo" not in doc["response"]


@respx.mock
async def test_record_skips_failed_calls(monkeypatch):
    monkeypatch.setenv("QLOO_RECORD", "1")
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(500, json={}))
    with pytest.raises(QlooAPIError):
        await QlooClient().search_entities(SearchEntitiesInput(query="x"))
    assert not demo_cache.fixtures_dir().exists()


# ---- fallback on failure ------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("status", [401, 403, 429])
async def test_fallback_on_auth_and_rate_limit_errors(status):
    _put("/search", {"query": "nujabes", "page": "1", "take": "10"}, {"results": [{"name": "Nujabes"}]})
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(status, json={"error": "nope"}))
    out = await QlooClient().search_entities(SearchEntitiesInput(query="Nujabes"))
    assert out["demo"] is True
    assert out["demo_info"]["reason"] == f"http_{status}"
    assert out["demo_info"]["match"] == "exact"
    assert out["results"][0]["name"] == "Nujabes"


@respx.mock
async def test_fallback_on_network_error():
    _put("/search", {"query": "nujabes", "page": "1", "take": "10"}, {"results": []})
    respx.get(f"{BASE}/search").mock(side_effect=httpx.ConnectError("down"))
    out = await QlooClient().search_entities(SearchEntitiesInput(query="Nujabes"))
    assert out["demo"] is True and out["demo_info"]["reason"] == "network_error"


@respx.mock
@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_other_errors_are_not_masked(status):
    _put("/search", {"query": "nujabes", "page": "1", "take": "10"}, {"results": []})
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(status, json={}))
    with pytest.raises(QlooAPIError) as ei:
        await QlooClient().search_entities(SearchEntitiesInput(query="Nujabes"))
    assert ei.value.status_code == status


@respx.mock
async def test_failure_with_empty_cache_raises_original_error():
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(QlooAPIError) as ei:
        await QlooClient().search_entities(SearchEntitiesInput(query="x"))
    assert ei.value.status_code == 401


@respx.mock
async def test_live_success_is_never_flagged():
    _put("/search", {"query": "nujabes", "page": "1", "take": "10"}, {"results": [{"name": "cached"}]})
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json={"results": [{"name": "live"}]}))
    out = await QlooClient().search_entities(SearchEntitiesInput(query="Nujabes"))
    assert out == {"results": [{"name": "live"}]}


# ---- QLOO_DEMO=1 --------------------------------------------------------------


@respx.mock
async def test_demo_env_makes_no_network_call_and_needs_no_key(monkeypatch):
    monkeypatch.setenv("QLOO_DEMO", "1")
    monkeypatch.delenv("QLOO_API_KEY")
    _put("/search", {"query": "nujabes", "page": "1", "take": "10"}, {"results": [{"name": "Nujabes"}]})
    route = respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json={}))
    out = await QlooClient().search_entities(SearchEntitiesInput(query="Nujabes"))
    assert route.call_count == 0
    assert out["demo"] is True and out["demo_info"]["reason"] == "QLOO_DEMO=1"


def test_missing_key_still_errors_without_demo_flag(monkeypatch):
    monkeypatch.delenv("QLOO_API_KEY")
    with pytest.raises(ValueError):
        QlooClient()


async def test_demo_env_with_no_cached_endpoint_raises(monkeypatch):
    monkeypatch.setenv("QLOO_DEMO", "1")
    with pytest.raises(QlooAPIError, match="no cached"):
        await QlooClient().search_entities(SearchEntitiesInput(query="x"))


# ---- fallback rule: exact, then nearest ---------------------------------------


def test_exact_key_beats_nearer_looking_fixtures():
    _put("/search", {"query": "nujabes", "take": "1"}, {"results": ["near"]})
    _put("/search", {"query": "nujabes", "take": "10"}, {"results": ["exact"]})
    got = demo_cache.replay("/search", {"query": "Nujabes", "take": "10"}, "test")
    assert got["results"] == ["exact"] and got["demo_info"]["match"] == "exact"


def test_nearest_picks_most_similar_params_for_same_endpoint():
    _put("/search", {"query": "mark rothko", "take": "1"}, {"results": ["rothko"]})
    _put("/search", {"query": "hans zimmer", "take": "1"}, {"results": ["zimmer"]})
    _put("/v2/tags", {"filter.query": "mark rothko", "take": "1"}, {"results": ["wrong endpoint"]})
    got = demo_cache.replay("/search", {"query": "Hans Zimmerman", "take": "1"}, "test")
    assert got["results"] == ["zimmer"] and got["demo_info"]["match"] == "nearest"


def test_nearest_never_crosses_filter_type():
    _put("/v2/insights", {"filter.type": "urn:entity:artist", "signal.interests.entities": "a"}, {"results": ["artists"]})
    assert demo_cache.replay("/v2/insights", {"filter.type": "urn:entity:place", "signal.interests.entities": "a"}, "t") is None
    got = demo_cache.replay("/v2/insights", {"filter.type": "urn:entity:artist", "signal.interests.entities": "z"}, "t")
    assert got["results"] == ["artists"]


def test_nearest_uses_id_overlap_and_ignores_id_order():
    _put("/v2/insights", {"filter.type": "urn:demographics", "signal.interests.entities": "a,b,c"}, {"results": ["abc"]})
    _put("/v2/insights", {"filter.type": "urn:demographics", "signal.interests.entities": "x,y"}, {"results": ["xy"]})
    got = demo_cache.replay("/v2/insights", {"filter.type": "urn:demographics", "signal.interests.entities": "c,b,q"}, "t")
    assert got["results"] == ["abc"]


def test_recorded_beats_sample_on_a_tie_and_name_breaks_remaining_ties():
    _put("/search", {"query": "aaa"}, {"results": ["sample"]}, source="sample")
    _put("/search", {"query": "bbb"}, {"results": ["recorded"]}, source="recorded")
    got = demo_cache.replay("/search", {"query": "ccc"}, "t")
    assert got["results"] == ["recorded"] and got["demo_info"]["data"] == "recorded"


def test_replay_does_not_mutate_the_stored_response():
    _put("/search", {"query": "a"}, {"results": []})
    first = demo_cache.replay("/search", {"query": "a"}, "t")
    assert first["demo"] is True
    on_disk = json.loads(next(demo_cache.fixtures_dir().glob("*.json")).read_text())
    assert "demo" not in on_disk["response"]


def test_corrupt_fixture_files_are_skipped():
    d = demo_cache.fixtures_dir()
    d.mkdir(parents=True)
    (d / "search__bad.json").write_text("{not json")
    _put("/search", {"query": "ok"}, {"results": ["ok"]})
    assert demo_cache.replay("/search", {"query": "zzz"}, "t")["results"] == ["ok"]


# ---- demo flag on the taste_match result --------------------------------------


@respx.mock
async def test_taste_match_result_carries_demo_flag_with_shipped_samples(monkeypatch):
    monkeypatch.setenv("QLOO_FIXTURES_DIR", str(SHIPPED))
    monkeypatch.setenv("QLOO_DEMO", "1")
    route = respx.route().mock(return_value=httpx.Response(200, json={}))
    inp = TasteMatchInput(
        work_title="Lone Journey",
        description="Astronaut",
        influences=["Interstellar", "Moebius", "Hans Zimmer"],
        keywords=["science fiction", "surrealism", "retro futurism"],
        location="Los Angeles",
    )
    out = await run_taste_match(QlooClient(), inp)
    assert route.call_count == 0
    assert out["demo"] is True and out["demo_data"] == "sample"
    assert any(n.startswith("DEMO MODE") for n in out["notes"])
    assert out["matches"]["artists"] and out["matches"]["places"] and out["audience"]["strongest_age_group"]
    assert out["pitch"]


@respx.mock
async def test_taste_match_result_has_no_demo_flag_when_live():
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(f"{BASE}/v2/tags").mock(return_value=httpx.Response(200, json={"results": {"tags": []}}))
    out = await run_taste_match(QlooClient(), TasteMatchInput(work_title="x", description="zzzz", influences=["a"]))
    assert "demo" not in out


def test_shipped_fixtures_are_valid_samples():
    files = sorted(SHIPPED.glob("*.json"))
    assert 10 <= len(files) <= 20
    for f in files:
        doc = json.loads(f.read_text())
        assert doc["source"] == "sample" and f.name == demo_cache.cache_key(doc["endpoint"], doc["params"])
        assert "demo" not in doc["response"]
