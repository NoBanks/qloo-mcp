# Qloo fixtures

Cached Qloo API responses used by demo mode (see "Demo mode" in the root README). One JSON file per request in `fixtures/qloo/`, named `<endpoint>__<hash>.json`.

## What is in here today

**All 18 files currently committed are SAMPLES.** They were written by hand to match the response shapes `qloo-mcp` parses. They are not real Qloo output: the affinity numbers, popularity scores and demographic splits are invented, and every entity ID starts with `SAMPLE_`. Each file says `"source": "sample"` and carries a note. The UI badge and the CLI both say when sample data is on screen.

| Endpoint | Files | Covers |
|---|---|---|
| `/search` | 5 | Interstellar, Moebius, Hans Zimmer, Nujabes, Mark Rothko |
| `/v2/tags` | 4 | science fiction, surrealism, retro futurism, lofi |
| `/v2/audiences` | 1 | hobbies and interests |
| `/v2/insights` | 8 | artists, places (Los Angeles), brands, books, movies, demographics, taste tags, plus places for San Francisco |

The insights samples are keyed to the "Lone Journey" example from `demo/catalog.json`, so that one work replays exactly. Everything else is served by the nearest-match rule below.

## File format

```json
{
  "endpoint": "/v2/insights",
  "params": {"filter.type": "urn:entity:artist", "...": "..."},
  "source": "recorded",
  "recorded_at": "2026-11-04T18:02:11+00:00",
  "response": {"success": true, "results": {"...": "..."}}
}
```

`source` is `recorded` (live Qloo response) or `sample`. The API key is never written.

## Cache key

`sha256` of the endpoint plus normalized params, first 16 hex chars. Normalization: values are stripped and casefolded, and comma lists (entity IDs, tag IDs, types) are sorted, so `Nujabes` and `nujabes`, or IDs in a different order, share a key. Free text params (`query`, `filter.query`, location params) are never split on commas.

## Fallback rule

When a live call fails with 401, 403, 429 or a network error, or when `QLOO_DEMO=1` is set:

1. Serve the file with the exact key if it exists.
2. Otherwise serve the nearest fixture for the same endpoint. A fixture is a candidate only if its `filter.type` equals the request's, so a places query never returns artists. Candidates are ranked by parameter similarity (equal values score 1, text params score half their string similarity, comma lists score their overlap). On a tie, `recorded` beats `sample`, then the file name decides.
3. No candidate means no fallback, and the original error is raised (or, under `QLOO_DEMO=1`, a "no cached response" error).

Other errors (400, 404, 5xx) are not masked.

Replayed payloads get `"demo": true` and a `demo_info` object (`reason`, `match` exact or nearest, `fixture`, `data` sample or recorded). The Taste Match result gets `"demo": true` and `"demo_data"` (`sample`, `recorded` or `mixed`) when any payload behind it was cached. These keys are added at read time and never stored in the files.

## Recording real fixtures

Run on a machine with a working `QLOO_API_KEY`, before API access ends. Every successful call is saved when `QLOO_RECORD=1` is set; normal runs never write.

```bash
export QLOO_API_KEY=...
export QLOO_RECORD=1
python3.11 demo/taste_match_agent.py                      # the 3 catalog works, full call chain each
python3.11 demo/taste_match_agent.py --title "Neon Koi" --kind artwork \
  --description "Glowing koi in a night pond, ukiyo-e meets cyberpunk" \
  --influences "Hokusai,Blade Runner" --keywords "cyberpunk,ukiyo-e" --location "Seattle"
```

Repeat the second form for more works (a mix of artwork and song, a few cities: Los Angeles, San Francisco, New York, Seattle, Chicago, Austin). Also record the seed lookups people are most likely to type:

```bash
python3.11 demo/taste_match_agent.py --title "Seeds" --description "seed lookups" \
  --influences "Nujabes,J Dilla,Mark Rothko,James Turrell,Moebius,Hans Zimmer" \
  --keywords "lofi,hip hop,abstract,minimalism,surrealism,synthwave"
```

Then review `git status fixtures/`, spot check a file or two, and commit. Recorded files never replace samples with different keys; they win ties when the nearest-match rule picks between them. Once the recorded set is broad, delete the samples with `grep -l '"source": "sample"' fixtures/qloo/*.json | xargs git rm`.
