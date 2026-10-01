"""Taste Match CLI agent.

Usage:
    export QLOO_API_KEY=...            (from the Qloo hackathon key form)
    python3.11 demo/taste_match_agent.py                  # runs every work in demo/catalog.json
    python3.11 demo/taste_match_agent.py --index 0        # one work from the catalog
    python3.11 demo/taste_match_agent.py --title "My Print" --kind artwork \
        --description "..." --influences "Moebius,Interstellar" --location "Los Angeles"
    add --json to print the raw result

Optional planner LLM (any OpenAI-compatible server):
    export TASTE_MATCH_LLM_URL=http://localhost:1233/v1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qloo_mcp.client import QlooAPIError, QlooClient  # noqa: E402
from qloo_mcp.schemas import TasteMatchInput  # noqa: E402
from qloo_mcp.taste_match import run_taste_match  # noqa: E402

CATALOG = Path(__file__).with_name("catalog.json")


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def render(result: dict) -> str:
    w = result["work"]
    lines = [f"=== {w['title']} ({w['kind']}) ===", f"planner: {result.get('planner')}"]
    seeds = [e.get("name") or e.get("query") for e in result["seeds"]["entities"]]
    seeds += [t.get("name") or t.get("query") for t in result["seeds"]["tags"]]
    lines.append("seeds resolved in Qloo: " + (", ".join(s for s in seeds if s) or "none"))
    aud = result.get("audience") or {}
    if aud.get("strongest_age_group"):
        lines.append(f"strongest age group: {aud['strongest_age_group']}  gender lean: {aud.get('gender_lean')}")
    for key, items in result["matches"].items():
        if items:
            lines.append(f"{key}: " + ", ".join(i["name"] for i in items if i.get("name")))
    if result.get("taste_tags"):
        lines.append("taste tags: " + ", ".join(t["name"] for t in result["taste_tags"] if t.get("name")))
    lines.append("")
    lines.append("PITCH: " + result.get("pitch", ""))
    for n in result.get("notes", []):
        lines.append(f"note: {n}")
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Taste Match: find who would love a work from your catalog.")
    ap.add_argument("--index", type=int)
    ap.add_argument("--title")
    ap.add_argument("--kind", choices=["artwork", "song"], default="artwork")
    ap.add_argument("--description")
    ap.add_argument("--influences")
    ap.add_argument("--keywords")
    ap.add_argument("--location")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.title and args.description:
        works = [
            {
                "work_title": args.title,
                "work_kind": args.kind,
                "description": args.description,
                "influences": _split(args.influences),
                "keywords": _split(args.keywords),
                "location": args.location,
            }
        ]
    else:
        works = json.loads(CATALOG.read_text())
        if args.index is not None:
            works = [works[args.index]]

    try:
        client = QlooClient()
    except ValueError:
        print("Set QLOO_API_KEY first (request a key: https://docs.qloo.com/reference/qloo-llm-hackathon-developer-guide)")
        return 2

    for w in works:
        try:
            result = await run_taste_match(client, TasteMatchInput(**w))
        except QlooAPIError as exc:
            print(f"Qloo API error {exc.status_code}: {exc.message}")
            return 1
        print(json.dumps(result, indent=2) if args.json else render(result))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
