# Frontpage backend (Cloud Run + Neon)

Read-mostly API over the published frontpage. The local pipeline produces
`data/frontpage.json`; this service loads it into Neon Postgres and serves it.

## Data flow

```
pipeline export -> data/frontpage.json -> loader.py -> Neon Postgres <- main.py (FastAPI) <- frontend
```

`stories.story_id` is the aggregated-article id (`clusters.id`), stable by
construction because clusters are never destroyed (accretion model). Re-publishing
upserts by `story_id` and preserves `like_count`. Each load marks the table
inactive then re-activates the current snapshot; the API only serves `active` rows.

## Deploy (local container, DB stays on Neon)

Runs via docker-compose. `DATABASE_URL` comes from `.env` (compose parses it
literally, so the `&` in a Neon URL is safe — unlike shell `source .env`).

```bash
# 1) host: produce the snapshot (needs local SQLite + Ollama)
PYTHONPATH=src python -m frontpage_pipeline.cli export      # -> data/frontpage.json

# 2) load it into Neon (one-shot container)
docker compose run --rm frontpage-loader

# 3) serve the API
docker compose up -d frontpage-api                          # http://localhost:8090
```

`scripts/publish.sh` chains steps 1–2 after a pipeline run.

## Dev run (no container)

```bash
python -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
export DATABASE_URL="$(grep '^DATABASE_URL=' .env | cut -d= -f2-)"   # source-safe
backend/.venv/bin/python backend/loader.py
backend/.venv/bin/uvicorn main:app --app-dir backend --port 8090
```

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET | `/healthz` | DB ping |
| GET | `/stories?category=&limit=&offset=` | list, newest first |
| GET | `/search?q=` | trigram search (CN/EN) over title+body |
| GET | `/story/{story_id}` | full detail incl. synthesis + sources |
| POST | `/story/{key}/like` | global like counter (no auth yet) |

Search uses `pg_trgm` (substring) rather than a tsvector config so it works for
mixed Chinese/English without a language-specific tokenizer.

## Deferred

- Auth (Firebase) and per-user favorites / read-state.
- Caching lead images into GCS (currently hotlinked to source CDNs).
