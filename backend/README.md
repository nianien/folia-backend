# Frontpage backend (Cloud Run + Neon)

Read-mostly API over the published frontpage. The local pipeline produces
`data/frontpage.json`; this service loads it into Neon Postgres and serves it.

## Data flow

```
pipeline export -> data/frontpage.json -> loader.py -> Neon Postgres <- main.py (FastAPI) <- frontend
```

`stories.key` is a stable per-story hash (first source URL), so re-publishing
preserves `like_count`. Each load marks the table inactive then re-activates the
current snapshot; the API only serves `active` rows.

## Local run

```bash
python -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
set -a; source .env; set +a            # provides DATABASE_URL

PYTHONPATH=src python -m frontpage_pipeline.cli export   # -> data/frontpage.json
backend/.venv/bin/python backend/loader.py               # create tables + load
backend/.venv/bin/uvicorn main:app --app-dir backend --port 8090
```

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET | `/healthz` | DB ping |
| GET | `/stories?category=&limit=&offset=` | list, newest first |
| GET | `/search?q=` | trigram search (CN/EN) over title+body |
| GET | `/story/{key}` | full detail incl. synthesis + sources |
| POST | `/story/{key}/like` | global like counter (no auth yet) |

Search uses `pg_trgm` (substring) rather than a tsvector config so it works for
mixed Chinese/English without a language-specific tokenizer.

## Deferred

- Auth (Firebase) and per-user favorites / read-state.
- Caching lead images into GCS (currently hotlinked to source CDNs).
