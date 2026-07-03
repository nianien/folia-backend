# Frontpage Backend

Backend data pipeline prototype for the private Frontpage news app.

The current milestone has no UI. A base layer of off-the-shelf open source — **RSSHub** (generates feeds for sources without RSS) + **FreshRSS** (subscription management & fetch scheduling, exposes the Google Reader API) + **FiveFilters Full-Text RSS** (full-text extraction behind FreshRSS) — handles transport and extraction. The pipeline is the *editorial layer*: it pulls already-full-text articles via the Google Reader API, stores them in SQLite, clusters similar articles by embedding, creates per-article fact packages, and synthesizes a cluster-level Markdown article with numbered source citations.

The pipeline never fetches web pages or parses RSS itself.

## Base layer (docker-compose)

```bash
cp .env.example .env        # fill in FreshRSS user / API password
docker compose up -d        # rsshub :1200, fulltextrss :8081, freshrss :8080
```

Then do the one-time FreshRSS setup (account, enable Google Reader API, wire full-text extraction) — see `docs/freshrss-setup.md`. Subscriptions and tier/category mapping are managed from the control panel (Data sources page).

Embedding dedupe uses a local Ollama (not in compose):

```bash
ollama pull bge-m3
ollama serve                # http://localhost:11434
```

If Ollama is unreachable, dedupe automatically falls back to Jaccard token overlap.

## Setup (pipeline)

Standard `src/` layout + `pyproject.toml`. No `requirements.txt` — stdlib only.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For direct execution without installation, prefix commands with `PYTHONPATH=src`.

## Configuration

All runtime config now lives in the SQLite DB and is edited from the **control panel**
(`http://localhost:8000`), not in files:

- `settings` table (dotted keys → nested settings dict, built on in-code defaults in `config.py`):
  FreshRSS creds/params, `embeddings` (Ollama), `dedupe` thresholds, `model` provider,
  `database.url` (Neon入库), `loop.enabled`/`loop.interval`.
- `source_map` table: FreshRSS feed (by `stream_id` or title) → `tier`/`category`.
- `feed_seed` table: default subscriptions to bulk-import (seeded from `config.DEFAULT_FEEDS`).

Only the DB path is a bootstrap value (`FOLIA_DB_PATH` env or `data/frontpage.sqlite`).

## Model Providers

- `heuristic`: local fallback, no API key.
- `openai` / `claude` / `gemini` / `xinapi`: set the matching key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XIN_API_KEY`).

Select via the control panel (Config page) → `model.provider` (stored as `model.provider` in the
`settings` table). `heuristic` uses deterministic local fallbacks.

## Commands

```bash
folia-pipeline init-db
folia-pipeline run-once          # pull from FreshRSS -> text -> cluster -> facts -> synthesize
folia-pipeline extract-pending
folia-pipeline facts-pending
folia-pipeline synthesize-pending
folia-pipeline ingest-fixture tests/fixtures/freshrss_reading_list.json
folia-pipeline inspect-cluster 1
folia-pipeline serve --port 8000
```

`run-once` requires FreshRSS to be reachable. `ingest-fixture` reads a recorded Google Reader API JSON response and runs the full editorial layer offline.

Without editable install:

```bash
PYTHONPATH=src python -m folia.pipeline.cli run-once
```

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

All tests run offline (stdlib `unittest`, tempfile SQLite, no network).
