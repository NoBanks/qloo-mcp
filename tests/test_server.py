"""Tests for qloo-mcp. All HTTP is mocked with respx; no live key or network."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from qloo_mcp.schemas import GetDemographicsInput, GetTasteAnalysisInput, TasteMatchInput
from qloo_mcp.server import call_tool, list_tools
from qloo_mcp.taste_match import extract_keywords

BASE = "https://qloo.test"

EXPECTED_TOOLS = {
    "search_entities",
    "search_tags",
    "list_audiences",
    "get_insights",
    "get_demographics",
    "get_taste_analysis",
    "taste_match",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("QLOO_API_KEY", "test-key")
    monkeypatch.setenv("QLOO_API_URL", BASE)
    monkeypatch.delenv("TASTE_MATCH_LLM_URL", raising=False)


def _payload(result) -> dict:
    assert len(result) == 1
    return json.loads(result[0].text)


async def test_list_tools_registers_seven_tools():
    tools = await list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_every_tool_has_object_schema():
    for t in await list_tools():
        assert t.inputSchema["type"] == "object"
        assert t.description


@respx.mock
async def test_search_entities_params_and_auth_header():
    route = respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, json={"success": True, "results": [{"name": "Nujabes", "entity_id": "E1"}]})
    )
    out = _payload(
        await call_tool("search_entities", {"query": "Nujabes", "types": ["urn:entity:artist", "urn:entity:brand"]})
    )
    assert out["results"][0]["entity_id"] == "E1"
    req = route.calls.last.request
    assert req.headers["X-Api-Key"] == "test-key"
    assert req.url.params["query"] == "Nujabes"
    assert req.url.params["types"] == "urn:entity:artist,urn:entity:brand"


@respx.mock
async def test_search_tags_uses_filter_query():
    route = respx.get(f"{BASE}/v2/tags").mock(
        return_value=httpx.Response(200, json={"results": {"tags": [{"id": "urn:tag:genre:music:lofi", "name": "Lofi"}]}})
    )
    out = _payload(await call_tool("search_tags", {"query": "lofi"}))
    assert out["results"]["tags"][0]["name"] == "Lofi"
    assert route.calls.last.request.url.params["filter.query"] == "lofi"


@respx.mock
async def test_list_audiences_types():
    route = respx.get(f"{BASE}/v2/audiences").mock(return_value=httpx.Response(200, json={"results": {"audiences": []}}))
    await call_tool("list_audiences", {"audience_types": ["urn:audience:hobbies_and_interests"]})
    assert route.calls.last.request.url.params["filter.audience.types"] == "urn:audience:hobbies_and_interests"


@respx.mock
async def test_get_insights_param_mapping():
    route = respx.get(f"{BASE}/v2/insights").mock(return_value=httpx.Response(200, json={"results": {"entities": []}}))
    await call_tool(
        "get_insights",
        {
            "entity_type": "urn:entity:place",
            "entity_ids": ["A", "B"],
            "age": "25_to_29",
            "signal_location": "Los Angeles",
            "explain": True,
        },
    )
    p = route.calls.last.request.url.params
    assert p["filter.type"] == "urn:entity:place"
    assert p["signal.interests.entities"] == "A,B"
    assert p["signal.demographics.age"] == "25_to_29"
    assert p["signal.location.query"] == "Los Angeles"
    assert p["feature.explainability"] == "true"
    assert "signal.interests.tags" not in p
    assert "filter.location.query" not in p


@respx.mock
async def test_get_insights_omits_explainability_by_default():
    route = respx.get(f"{BASE}/v2/insights").mock(return_value=httpx.Response(200, json={"results": {"entities": []}}))
    await call_tool("get_insights", {"entity_type": "urn:entity:artist", "tag_ids": ["T1"]})
    assert "feature.explainability" not in route.calls.last.request.url.params


async def test_get_insights_rejects_bad_type():
    out = _payload(await call_tool("get_insights", {"entity_type": "urn:entity:spaceship"}))
    assert out["error"] == "invalid_input"


@respx.mock
async def test_get_demographics_filter_type():
    route = respx.get(f"{BASE}/v2/insights").mock(return_value=httpx.Response(200, json={"results": {"demographics": []}}))
    await call_tool("get_demographics", {"entity_ids": ["E1"]})
    p = route.calls.last.request.url.params
    assert p["filter.type"] == "urn:demographics"
    assert p["signal.interests.entities"] == "E1"


async def test_get_demographics_requires_signal():
    out = _payload(await call_tool("get_demographics", {}))
    assert out["error"] == "invalid_input"
    with pytest.raises(ValueError):
        GetDemographicsInput()


@respx.mock
async def test_get_taste_analysis_filter_type():
    route = respx.get(f"{BASE}/v2/insights").mock(return_value=httpx.Response(200, json={"results": {"tags": []}}))
    await call_tool("get_taste_analysis", {"location": "Los Angeles"})
    p = route.calls.last.request.url.params
    assert p["filter.type"] == "urn:tag"
    assert p["signal.location.query"] == "Los Angeles"


def test_taste_analysis_requires_signal():
    with pytest.raises(ValueError):
        GetTasteAnalysisInput()


async def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    out = _payload(await call_tool("search_entities", {"query": "x"}))
    assert out["error"] == "missing_api_key"


@respx.mock
async def test_http_401_becomes_structured_error():
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(401, json={"message": "bad key"}))
    out = _payload(await call_tool("search_entities", {"query": "x"}))
    assert out["error"] == "qloo_api_error"
    assert out["status_code"] == 401


@respx.mock
async def test_network_error_becomes_structured_error():
    respx.get(f"{BASE}/search").mock(side_effect=httpx.ConnectError("boom"))
    out = _payload(await call_tool("search_entities", {"query": "x"}))
    assert out["error"] == "qloo_api_error"


async def test_unknown_tool():
    out = _payload(await call_tool("does_not_exist", {}))
    assert out["error"] == "unknown_tool"


def test_extract_keywords_drops_stopwords():
    kws = extract_keywords("A lone astronaut walks across the desert at golden hour", limit=3)
    assert kws == ["lone", "astronaut", "walks"]


def _insights_router(request: httpx.Request) -> httpx.Response:
    ftype = request.url.params["filter.type"]
    if ftype == "urn:demographics":
        return httpx.Response(
            200,
            json={
                "results": {
                    "demographics": [
                        {
                            "entity_id": "E1",
                            "query": {
                                "age": {"24_and_younger": 0.1, "25_to_29": 0.4, "55_and_older": -0.3},
                                "gender": {"male": 0.2, "female": -0.1},
                            },
                        }
                    ]
                }
            },
        )
    if ftype == "urn:tag":
        return httpx.Response(
            200,
            json={"results": {"tags": [{"tag_id": "urn:tag:keyword:media:space", "name": "Space", "query": {"affinity": 0.9}}]}},
        )
    name = ftype.split(":")[-1]
    return httpx.Response(
        200,
        json={
            "results": {
                "entities": [
                    {"name": "Seed Should Be Dropped", "entity_id": "E1"},
                    {
                        "name": f"Top {name}",
                        "entity_id": f"{name}-1",
                        "subtype": ftype,
                        "query": {"affinity": 0.95},
                        "properties": {"address": "123 Main St"} if name == "place" else {},
                    },
                ]
            }
        },
    )


@respx.mock
async def test_taste_match_end_to_end():
    search = respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, json={"results": [{"name": "Interstellar", "entity_id": "E1", "types": ["urn:entity:movie"]}]})
    )
    respx.get(f"{BASE}/v2/tags").mock(
        return_value=httpx.Response(200, json={"results": {"tags": [{"id": "urn:tag:genre:media:science_fiction", "name": "Science Fiction"}]}})
    )
    insights = respx.get(f"{BASE}/v2/insights").mock(side_effect=_insights_router)

    out = _payload(
        await call_tool(
            "taste_match",
            {
                "work_title": "Lone Journey",
                "work_kind": "artwork",
                "description": "A lone astronaut walks across a desert at golden hour.",
                "influences": ["Interstellar"],
                "keywords": ["science fiction"],
                "location": "Los Angeles",
                "take": 5,
            },
        )
    )
    for key in ("seeds", "matches", "audience", "taste_tags", "pitch"):
        assert key in out
    assert out["seeds"]["entities"][0]["entity_id"] == "E1"
    assert out["seeds"]["tags"][0]["tag_id"] == "urn:tag:genre:media:science_fiction"
    assert [m["name"] for m in out["matches"]["artists"]] == ["Top artist"]
    assert out["matches"]["places"][0]["address"] == "123 Main St"
    assert out["audience"]["strongest_age_group"] == "25 to 29"
    assert out["audience"]["gender_lean"] == "male"
    assert out["taste_tags"][0]["name"] == "Space"
    assert "Lone Journey" in out["pitch"] and "Top artist" in out["pitch"]
    assert search.called
    # 5 entity types + demographics + taste tags
    assert insights.call_count == 7
    place_calls = [c for c in insights.calls if c.request.url.params["filter.type"] == "urn:entity:place"]
    assert place_calls[0].request.url.params["filter.location.query"] == "Los Angeles"


@respx.mock
async def test_taste_match_partial_failure_is_a_note():
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json={"results": [{"name": "Nujabes", "entity_id": "E1"}]}))
    respx.get(f"{BASE}/v2/tags").mock(return_value=httpx.Response(200, json={"results": {"tags": []}}))

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.params["filter.type"] == "urn:entity:brand":
            return httpx.Response(500, json={"message": "oops"})
        return _insights_router(request)

    respx.get(f"{BASE}/v2/insights").mock(side_effect=flaky)
    out = _payload(
        await call_tool(
            "taste_match",
            {"work_title": "Late Night Signal", "work_kind": "song", "description": "lofi beat", "influences": ["Nujabes"], "keywords": ["lofi"]},
        )
    )
    assert out["matches"]["brands"] == []
    assert out["matches"]["artists"]
    assert any("brands" in n for n in out["notes"])


@respx.mock
async def test_taste_match_no_seeds():
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(f"{BASE}/v2/tags").mock(return_value=httpx.Response(200, json={"results": {"tags": []}}))
    out = _payload(await call_tool("taste_match", {"work_title": "X", "description": "zzzz qqqq"}))
    assert out["matches"]["artists"] == []
    assert "Not enough taste signal" in out["pitch"]


def test_taste_match_input_limits():
    with pytest.raises(ValueError):
        TasteMatchInput(work_title="x", description="y", influences=["a"] * 11)


@respx.mock
def test_web_demo_routes():
    from starlette.testclient import TestClient

    from demo.web_app import app

    respx.route(host="testserver").pass_through()
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json={"results": [{"name": "Moebius", "entity_id": "E1"}]}))
    respx.get(f"{BASE}/v2/tags").mock(return_value=httpx.Response(200, json={"results": {"tags": []}}))
    respx.get(f"{BASE}/v2/insights").mock(side_effect=_insights_router)

    c = TestClient(app)
    assert c.get("/healthz").json() == {"ok": True, "qloo_key_configured": True}
    assert "Taste" in c.get("/").text
    assert len(c.get("/api/catalog").json()) >= 1
    r = c.post("/api/match", json={"work_title": "Lone Journey", "description": "astronaut", "influences": ["Moebius"]})
    assert r.status_code == 200
    assert r.json()["matches"]["artists"][0]["name"] == "Top artist"
    bad = c.post("/api/match", json={"work_title": ""})
    assert bad.status_code == 400
