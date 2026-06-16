# Frontpage Backend

Backend data pipeline prototype for the private Frontpage news app.

The current milestone has no UI. It ingests configured RSS/Atom feeds, stores articles in SQLite, extracts readable text, clusters similar articles, creates per-article fact packages, and synthesizes a cluster-level Markdown article with numbered source citations.

## Setup

This project uses a standard `src/` Python package layout and `pyproject.toml`. There is no `requirements.txt`; the current implementation uses only the Python standard library.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For direct execution without installation, prefix commands with `PYTHONPATH=src`.

## Configuration

- `config/sources.toml`: RSS/Atom subscriptions.
- `config/settings.toml`: database path, network timeout, dedupe threshold, extraction settings, and model provider.

## Model Providers

The pipeline supports four model modes:

- `heuristic`: local fallback, no API key, lower quality.
- `openai`: uses `OPENAI_API_KEY`.
- `claude`: uses `ANTHROPIC_API_KEY`.
- `gemini`: uses `GEMINI_API_KEY`.
- `xinapi`: uses `XIN_API_KEY` through the XinAPI OpenAI-compatible gateway.

Select the provider in `config/settings.toml`:

```toml
[model]
provider = "openai"
```

Then set the matching key:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export GEMINI_API_KEY="..."
export XIN_API_KEY="..."
```

`facts-pending` uses the selected model to extract per-article fact JSON. `synthesize-pending` uses the same model to generate cluster Markdown with numbered source citations. If the provider is `heuristic`, both stages use local deterministic fallbacks.

## Commands

```bash
frontpage-pipeline init-db
frontpage-pipeline run-once
frontpage-pipeline crawl-pending
frontpage-pipeline extract-pending
frontpage-pipeline facts-pending
frontpage-pipeline synthesize-pending
frontpage-pipeline inspect-cluster 1
```

Without editable install:

```bash
PYTHONPATH=src python -m frontpage_pipeline.cli run-once
```

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```
