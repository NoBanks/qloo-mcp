"""Taste Match web demo (the hosted demo for the Qloo hackathon).

Run locally:
    python3.11 -m pip install -e ".[demo]"
    export QLOO_API_KEY=...
    uvicorn demo.web_app:app --host 0.0.0.0 --port ${PORT:-8080}

Routes:
    GET  /             single-page UI
    GET  /api/catalog  example works from demo/catalog.json
    POST /api/match    body = TasteMatchInput JSON, returns the taste_match result
    GET  /healthz      liveness plus whether a Qloo key is configured (never the key)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qloo_mcp.client import QlooAPIError, QlooClient  # noqa: E402
from qloo_mcp.schemas import TasteMatchInput  # noqa: E402
from qloo_mcp.taste_match import run_taste_match  # noqa: E402

HERE = Path(__file__).resolve().parent
RATE_WINDOW_SECONDS = 60
RATE_MAX = int(os.environ.get("TASTE_MATCH_RATE_PER_MIN", "10"))
_hits: dict[str, list[float]] = {}


def _rate_ok(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW_SECONDS]
    if len(recent) >= RATE_MAX:
        _hits[ip] = recent
        return False
    recent.append(now)
    _hits[ip] = recent
    return True


async def index(_: Request) -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


async def catalog(_: Request) -> JSONResponse:
    return JSONResponse(json.loads((HERE / "catalog.json").read_text()))


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "qloo_key_configured": bool(os.environ.get("QLOO_API_KEY"))})


async def match(request: Request) -> JSONResponse:
    ip = request.client.host if request.client else "unknown"
    if not _rate_ok(ip):
        return JSONResponse({"error": "rate_limited", "message": "Slow down, try again in a minute."}, status_code=429)
    try:
        body = await request.json()
        inp = TasteMatchInput(**body)
    except (ValueError, ValidationError) as exc:
        return JSONResponse({"error": "invalid_input", "message": str(exc)}, status_code=400)
    try:
        client = QlooClient()
    except ValueError:
        return JSONResponse({"error": "missing_api_key", "message": "Server has no QLOO_API_KEY set."}, status_code=503)
    try:
        result = await run_taste_match(client, inp)
    except QlooAPIError as exc:
        return JSONResponse({"error": "qloo_api_error", "status_code": exc.status_code, "message": exc.message}, status_code=502)
    return JSONResponse(result)


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/catalog", catalog),
        Route("/api/match", match, methods=["POST"]),
        Route("/healthz", healthz),
    ]
)
