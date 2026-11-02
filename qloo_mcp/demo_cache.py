"""Record and replay cache for Qloo responses (demo mode).

Qloo API access ends for most entrants after the hackathon, so every Qloo
response can be saved to disk and served back later.

Env flags:
    QLOO_RECORD=1  save each successful live response under the fixtures dir.
    QLOO_DEMO=1    never call Qloo; serve cached responses only. Also used as
                   the fallback when a live call fails with 401, 403, 429 or a
                   network error (no flag needed for that).
    QLOO_FIXTURES_DIR  override the fixtures directory (default fixtures/qloo).

Fallback rule (see fixtures/README.md):
    1. Exact key: sha256 of endpoint + normalized params.
    2. Otherwise the nearest fixture for the same endpoint. A fixture is only a
       candidate if it has the same filter.type as the request (so a places
       query never gets artists back). Candidates are ranked by parameter
       similarity, recorded fixtures beat sample fixtures on ties, then file
       name decides. No candidate means no fallback and the original error
       is raised.

A replayed response is a shallow copy with `demo: true` and a `demo_info`
object added. Fixtures on disk never contain those keys.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("qloo_mcp.demo_cache")

DEMO_ENV = "QLOO_DEMO"
RECORD_ENV = "QLOO_RECORD"
FIXTURES_DIR_ENV = "QLOO_FIXTURES_DIR"

# HTTP statuses that trigger the fallback (network errors do too, see client).
FALLBACK_STATUSES = frozenset({401, 403, 429})

# A cached response is only a candidate if these params are equal to the request's.
DISCRIMINATORS = ("filter.type",)

# Free text params: compared case-insensitively and never split on commas.
TEXT_PARAMS = frozenset({"query", "filter.query", "filter.location", "filter.location.query", "signal.location.query"})


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def demo_enabled() -> bool:
    return _flag(DEMO_ENV)


def record_enabled() -> bool:
    return _flag(RECORD_ENV)


def fixtures_dir() -> Path:
    override = os.environ.get(FIXTURES_DIR_ENV, "").strip()
    if override:
        return Path(override)
    from_cwd = Path.cwd() / "fixtures" / "qloo"
    if from_cwd.is_dir():
        return from_cwd
    return Path(__file__).resolve().parents[1] / "fixtures" / "qloo"


def _norm_path(path: str) -> str:
    return "/" + path.strip().strip("/").lower()


def normalize_params(params: dict[str, Any]) -> dict[str, str]:
    """Stable form of a query: stripped, casefolded, list values sorted."""
    out: dict[str, str] = {}
    for key, value in params.items():
        text = str(value).strip().casefold()
        if not text:
            continue
        if key not in TEXT_PARAMS and "," in text:
            text = ",".join(sorted(p.strip() for p in text.split(",") if p.strip()))
        out[key] = text
    return dict(sorted(out.items()))


def cache_key(path: str, params: dict[str, Any]) -> str:
    """File name (without directory) for an endpoint plus params."""
    blob = json.dumps(
        {"endpoint": _norm_path(path), "params": normalize_params(params)},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "_", _norm_path(path)).strip("_") or "root"
    return f"{slug}__{digest}.json"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("response"), dict) and isinstance(data.get("params"), dict):
        return data
    return None


def record(path: str, params: dict[str, Any], response: dict[str, Any]) -> Path:
    """Write one live response to the fixtures dir. The API key is never stored."""
    target_dir = fixtures_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / cache_key(path, params)
    doc = {
        "endpoint": _norm_path(path),
        "params": normalize_params(params),
        "source": "recorded",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "response": response,
    }
    fd, tmp = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def _similarity(want: dict[str, str], have: dict[str, str]) -> float:
    keys = set(want) | set(have)
    if not keys:
        return 1.0
    total = 0.0
    for key in keys:
        a, b = want.get(key), have.get(key)
        if a is None or b is None:
            continue
        if a == b:
            total += 1.0
        elif key in TEXT_PARAMS:
            total += 0.5 * difflib.SequenceMatcher(None, a, b).ratio()
        else:
            sa, sb = set(a.split(",")), set(b.split(","))
            total += len(sa & sb) / len(sa | sb)
    return total / len(keys)


def find_nearest(path: str, params: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Best same-endpoint fixture under the fallback rule, as (document, file name)."""
    directory = fixtures_dir()
    if not directory.is_dir():
        return None
    endpoint = _norm_path(path)
    want = normalize_params(params)
    ranked: list[tuple[float, bool, str, dict[str, Any]]] = []
    for file in sorted(directory.glob("*.json")):
        doc = _load(file)
        if doc is None or doc.get("endpoint") != endpoint:
            continue
        have = doc["params"]
        if any(k in want and want[k] != have.get(k) for k in DISCRIMINATORS):
            continue
        ranked.append((_similarity(want, have), doc.get("source") == "sample", file.name, doc))
    if not ranked:
        return None
    ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
    best = ranked[0]
    return best[3], best[2]


def replay(path: str, params: dict[str, Any], reason: str) -> dict[str, Any] | None:
    """Cached response flagged as demo data, or None when nothing usable is cached."""
    name = cache_key(path, params)
    doc = _load(fixtures_dir() / name)
    match = "exact"
    if doc is None:
        found = find_nearest(path, params)
        if found is None:
            return None
        doc, name = found
        match = "nearest"
    level = logging.INFO if reason == DEMO_ENV + "=1" else logging.WARNING
    logger.log(level, "Qloo demo mode (%s): serving %s fixture %s for %s", reason, match, name, path)
    payload = dict(doc["response"])
    payload["demo"] = True
    payload["demo_info"] = {
        "reason": reason,
        "match": match,
        "fixture": name,
        "data": "sample" if doc.get("source") == "sample" else "recorded",
    }
    return payload
