# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python backend prototype. `src/folia/pipeline/` contains the pipeline package (incl. the FastAPI control panel under `panel/`); runtime configuration lives in the SQLite DB (`settings`/`feed`/`directory` tables), edited via the control panel; `docker-compose.yml` brings up RSSHub (feeds for sources without native RSS) plus the `panel` service; `docs/` contains product/technical design docs; `tests/` contains standard-library `unittest` coverage; and `data/` holds the local SQLite DB.

The pipeline polls RSS/Atom feeds itself (in-app poller), extracts full text with trafilatura, then clusters, categorizes, and synthesizes. LLM calls route through per-function providers (local Ollama or a remote API) configured in the panel.

Do not commit local environment directories such as `.venv/`, generated caches, or machine-specific editor files.

## Build, Test, and Development Commands

Dependencies are managed with [uv](https://astral.sh/uv) (`uv.lock` pins versions):

```bash
uv sync                          # create .venv, install from uv.lock + the package
# offline editorial layer against a local feed fixture:
uv run folia-pipeline init-db
uv run folia-pipeline ingest-fixture tests/fixtures/sample_feed.xml
# full run (needs RSSHub up for rsshub-backed feeds; see README):
uv run folia-pipeline run-once
```

`uv run <cmd>` runs inside the project venv.

## Coding Style & Naming Conventions

Follow standard Python style: 4-space indentation, snake_case for functions and variables, PascalCase for classes, and UPPER_SNAKE_CASE for constants. Keep modules focused on one responsibility. Use type hints for new public functions and data structures, especially around article, cluster, and layout records.

Prefer clear, boring names that match the domain: `articles`, `clusters`, `poller`, `extractor`, `categorize`, `embeddings`, `dedupe`, `synthesizer`, and `model_client`.

## Testing Guidelines

Tests use Python `unittest` and live under `tests/`. Name files `test_<module>.py` and test methods `test_<behavior>()`.

Run tests with `uv run python -m unittest discover -s tests`. Keep fixtures small and deterministic.

## Commit & Pull Request Guidelines

Use concise imperative commits, optionally with Conventional Commit prefixes, for example `feat: add FastAPI layout endpoint` or `test: cover cross-day progress gate`.

Pull requests should include a short summary, the user-visible behavior changed, tests run, and any configuration or migration notes. Include screenshots only for UI changes.

## Security & Configuration Tips

Keep API keys, feed credentials, and local database paths out of source control. Store runtime configuration in ignored local files or environment variables, and document required variables when they are introduced.
