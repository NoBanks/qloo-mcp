# qloo-mcp

An MCP server that gives any AI agent the Qloo Taste AI graph as tools, plus **Taste Match**, an agent that tells an independent artist exactly who would love a piece from their catalog.

Built for the Qloo Agentic Hackathon: https://qloo.devpost.com/

## Why

Independent artists sell to "people who like this kind of thing" and have no way to say who those people are. A generic LLM can guess. Qloo knows: its taste graph links 250M+ artists, films, books, brands and places to real affinity data, demographics and locations. Taste Match turns one description of a print or a song into:

- the artists, films, books and brands its audience already loves
- the places in a chosen city where those people go (galleries, cafes, shops, venues)
- the age and gender affinity of that audience
- the words that describe that taste
- a ready-to-use plain-text pitch

## Tools

| Tool | Qloo endpoint | What it does |
|---|---|---|
| `search_entities` | GET /search | Resolve names to Qloo entity IDs across artists, books, brands, destinations, movies, people, places, podcasts, TV shows, video games |
| `search_tags` | GET /v2/tags | Resolve genre, style or keyword names to Qloo tag IDs |
| `list_audiences` | GET /v2/audiences | List audience segments by audience type |
| `get_insights` | GET /v2/insights | Taste-based recommendations of one entity type from entity, tag, age, gender, audience and location signals |
| `get_demographics` | GET /v2/insights (urn:demographics) | Age and gender affinity of the audience for entities or tags |
| `get_taste_analysis` | GET /v2/insights (urn:tag) | Tags that describe the taste of an entity set, audience or location |
| `taste_match` | all of the above | Composite agent: description in, audience report and pitch out |

Every tool validates input with Pydantic and returns JSON. Failures come back as structured errors (`invalid_input`, `missing_api_key`, `qloo_api_error`, `unknown_tool`), never tracebacks.

## How Taste Match works

1. **Plan.** The artist gives a title, a description, and optionally influences and style keywords. If an OpenAI-compatible LLM is configured (`TASTE_MATCH_LLM_URL`), the agent asks it for more influences and keywords. Without one, a deterministic keyword extractor fills in. Artist-supplied seeds always win.
2. **Resolve.** Every seed is looked up in Qloo in parallel (`/search` for entities, `/v2/tags` for tags) to get real Qloo IDs.
3. **Fan out.** Seven parallel Qloo insight calls: artists, places (restricted to the chosen city), brands, books, movies, demographics, taste tags.
4. **Synthesize.** Seeds are removed from results, demographics are averaged into an audience profile, and a plain-text pitch is written from the top matches.

One failing branch becomes a note in the result instead of failing the whole match.

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/NoBanks/qloo-mcp
cd qloo-mcp
python3.11 -m pip install -e .
```

## Configuration

| Env var | Required | Default |
|---|---|---|
| `QLOO_API_KEY` | yes | none. Request one: https://docs.qloo.com/reference/qloo-llm-hackathon-developer-guide |
| `QLOO_API_URL` | no | `https://hackathon.api.qloo.com` |
| `QLOO_TIMEOUT_SECONDS` | no | `30` |
| `TASTE_MATCH_LLM_URL` | no | unset (keyword planner). Any OpenAI-compatible `/v1` base URL |
| `TASTE_MATCH_LLM_MODEL` | no | first model from `/v1/models` |
| `TASTE_MATCH_LLM_API_KEY` | no | unset |

The key is sent only as the `X-Api-Key` header and is never logged.

### Claude Desktop / any MCP client

```json
{
  "mcpServers": {
    "qloo": {
      "command": "qloo-mcp"
    }
  }
}
```

Add `QLOO_API_KEY` to the environment the client launches the server with (in Claude Desktop, an `env` object inside the `qloo` entry).

## Hackathon demo

The demo is a small web app plus a CLI, both driving the same `taste_match` agent the MCP tool uses.

### Web app (the hosted demo)

```bash
python3.11 -m pip install -e ".[demo]"
export QLOO_API_KEY=...        # your key
uvicorn demo.web_app:app --host 0.0.0.0 --port 8080
```

Open http://localhost:8080, pick an example work (or describe your own), and press **Find my audience**.

Routes: `GET /` (UI), `GET /api/catalog` (example works), `POST /api/match` (runs Taste Match), `GET /healthz`. `POST /api/match` is rate limited per IP (`TASTE_MATCH_RATE_PER_MIN`, default 10).

Deploy anywhere that runs a container. The included `Dockerfile` serves the app on `$PORT`:

```bash
docker build -t taste-match .
docker run -p 8080:8080 -e QLOO_API_KEY=... taste-match
```

### CLI agent

```bash
export QLOO_API_KEY=...
python3.11 demo/taste_match_agent.py              # every work in demo/catalog.json
python3.11 demo/taste_match_agent.py --index 2    # one work
python3.11 demo/taste_match_agent.py --title "Neon Koi" --kind artwork \
  --description "Glowing koi in a night pond, ukiyo-e meets cyberpunk" \
  --influences "Hokusai,Blade Runner" --location "Seattle"
```

Add `--json` for the raw result.

## Tests

```bash
python3.11 -m pip install -e ".[dev,demo]"
python3.11 -m pytest tests/ -v
```

All HTTP is mocked with respx. No key or network is needed to run the suite.

## License

MIT. See LICENSE.

Built by Ryan Hammer (NoBanks): https://github.com/NoBanks
