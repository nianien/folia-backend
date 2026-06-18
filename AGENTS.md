# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python backend prototype. `src/frontpage_pipeline/` contains the editorial-layer pipeline package, `config/*.toml` contains runtime configuration, `config/freshrss/` holds base-layer wiring (OPML + setup notes), `docker-compose.yml` brings up the base layer (RSSHub + FreshRSS + FiveFilters Full-Text RSS), `docs/` contains product and technical design documents, `tests/` contains standard-library `unittest` coverage, and `data/` is reserved for local SQLite files.

The pipeline reads already-full-text articles from FreshRSS via the Google Reader API; it does not fetch web pages or parse RSS itself.

Do not commit local environment directories such as `.venv/`, generated caches, or machine-specific editor files.

## Build, Test, and Development Commands

Use Python 3 from a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
# offline editorial layer against a recorded FreshRSS response:
frontpage-pipeline init-db
frontpage-pipeline ingest-fixture tests/fixtures/freshrss_reading_list.json
# full run (requires the docker-compose base layer up; see README):
frontpage-pipeline run-once
```

Without editable install, run commands with `PYTHONPATH=src`, for example `PYTHONPATH=src python -m frontpage_pipeline.cli init-db`.

## Coding Style & Naming Conventions

Follow standard Python style: 4-space indentation, snake_case for functions and variables, PascalCase for classes, and UPPER_SNAKE_CASE for constants. Keep modules focused on one responsibility. Use type hints for new public functions and data structures, especially around article, cluster, and layout records.

Prefer clear, boring names that match the spec: `articles`, `clusters`, `layout`, `freshrss_client`, `extractor`, `embeddings`, `dedupe`, `scorer`, and `ranker`.

## Testing Guidelines

Tests use Python `unittest` and live under `tests/`. Name files `test_<module>.py` and test methods `test_<behavior>()`.

Run tests with `PYTHONPATH=src python -m unittest discover -s tests`. Keep fixtures small and deterministic.

## Commit & Pull Request Guidelines

This directory is not currently initialized as a Git repository, so no local commit convention is available. Use concise imperative commits, optionally with Conventional Commit prefixes, for example `feat: add FastAPI layout endpoint` or `test: cover cross-day progress gate`.

Pull requests should include a short summary, the user-visible behavior changed, tests run, and any configuration or migration notes. Include screenshots only for UI changes.

## Security & Configuration Tips

Keep API keys, feed credentials, and local database paths out of source control. Store runtime configuration in ignored local files or environment variables, and document required variables when they are introduced.
