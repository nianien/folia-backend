# Frontpage Backend

Backend data pipeline prototype for the private Frontpage news app.

The pipeline is the *editorial layer*: it polls RSS/Atom feeds, extracts full text,
categorizes each article by content, clusters similar articles by embedding, builds
per-article fact packages, and synthesizes a cluster-level Markdown article with
numbered source citations. Fetching, extraction, dedupe and the control panel all run
in-app — no external subscription service.

## Base layer (docker-compose)

Only **RSSHub** runs alongside the app, to generate feeds for sources without native
RSS (e.g. 公众号/微博/晚点). The panel container runs the web console + in-app pipeline loop.

```bash
docker compose up -d        # rsshub :1200, panel :8000
```

Embedding/dedupe and all LLM calls default to a local **Ollama** (not in compose):

```bash
ollama pull bge-m3
ollama serve                # http://localhost:11434
```

If Ollama is unreachable, dedupe falls back to Jaccard token overlap and the LLM
functions fall back to deterministic heuristics.

## Setup (pipeline)

Standard `src/` layout + `pyproject.toml`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For direct execution without installation, prefix commands with `PYTHONPATH=src`.

## Configuration

All runtime config lives in the SQLite DB and is edited from the **control panel**
(`http://localhost:8000/admin`), not in files:

- `settings` table (dotted keys → nested dict, over in-code defaults in `config.py`):
  `embeddings` (Ollama url), `dedupe` thresholds, `model` (shared LLM params),
  `models.<function>` (per-function provider + model), `providers.<name>`
  (endpoint + api_key), `database.url` (Neon 入库), `loop.enabled`/`loop.interval`.
- `feed` table: subscriptions (the local feed list is the source of truth).
- `directory` table: categories that drive content-based classification and the preview tabs.

Only the DB path is a bootstrap value (`FOLIA_DB_PATH` env or `data/frontpage.sqlite`).

## Model providers

Each function (`categorize` / `synthesis` / `facts`) picks its own provider and model
from the panel (Models tab); `embedding` is fixed to local Ollama. Leaving a function's
provider empty uses the local heuristic fallback (free, fast).

Supported providers: `openai`, `claude`, `gemini`, `deepseek`, `qwen`, `xinapi`,
`ollama` (local). API keys and endpoints are entered in the panel and stored in the DB;
they also fall back to the matching env var (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `XIN_API_KEY`) if unset.

## Commands

```bash
folia-pipeline init-db
folia-pipeline run-once            # poll → extract → categorize → cluster → facts → synthesize
folia-pipeline extract-pending
folia-pipeline categorize-pending
folia-pipeline facts-pending
folia-pipeline synthesize-pending
folia-pipeline export --out data/frontpage.json
folia-pipeline load --in data/frontpage.json      # push snapshot to Neon (uses database.url)
folia-pipeline panel --port 8000
folia-pipeline inspect-cluster 1
folia-pipeline ingest-fixture tests/fixtures/sample_feed.xml
```

`ingest-fixture` treats a local feed file as one source and runs it through the
poller + editorial layer offline (no network).

Without editable install:

```bash
PYTHONPATH=src python -m folia.pipeline.cli run-once
```

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

All tests run offline (stdlib `unittest`, tempfile SQLite, no network).
