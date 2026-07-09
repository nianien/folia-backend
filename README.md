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

Standard `src/` layout + `pyproject.toml`; dependencies are managed with
[uv](https://astral.sh/uv) (`uv.lock` pins exact versions).

```bash
uv sync                          # create .venv, install deps from uv.lock + the package (editable)
uv run python scripts/init_db.py # 一次性初始化 DB(建表 + 默认数据)
uv run folia-pipeline start --port 8000
```

`uv run <cmd>` runs inside the project venv. Prefix with `PYTHONPATH=src` only if
running the source directly without `uv`.

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
from the panel (Models tab), choosing from preset model lists per provider
(`config.PROVIDER_MODELS`); `embedding` is fixed to local Ollama. Leaving a function's
provider empty uses the local heuristic fallback (free, fast).

Supported providers: `openai`, `claude`, `gemini`, `deepseek`, `qwen`, `xinapi`,
`ollama` (local). API keys / endpoints are read from environment variables
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`,
`DASHSCOPE_API_KEY`, `XIN_API_KEY`), not configured in the panel.

## Commands

```bash
python scripts/init_db.py          # 一次性初始化: 建表 + 写默认订阅/分类/配置(幂等)
folia-pipeline start --port 8000   # 启动控制面板 + 自检循环(抓取/抽取/分类/聚类/事实/成稿, 每轮只处理未完成项)
```

`start` 是唯一命令:起 Web 控制台并常驻一个自检循环——每轮把各阶段的未完成项处理掉、干完等下一轮
(间隔在面板里改)。其余操作(数据源 / 分类 / 模型 / 云端同步 / 立即跑一轮 / 一键初始化)都在面板 `http://localhost:8000/admin` 里点。

Without the console script:

```bash
uv run python -m folia.pipeline.cli start --port 8000
```

## Tests

```bash
uv run python -m unittest discover -s tests
```

All tests run offline (stdlib `unittest`, tempfile SQLite, no network).
