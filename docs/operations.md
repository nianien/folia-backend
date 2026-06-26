# 运维文档（folia-pipeline）

`folia-pipeline` 是个纯后端数据管道：**抓取 → 清洗 → 聚合 → 入库**。产物是 Neon
Postgres 里就绪的聚合数据；谁去读它（查询/检索/点赞）是另一个消费端应用的事，不在本仓库。

包：`folia.pipeline`（`src/folia/pipeline/`，PEP 420 命名空间）。

---

## 1. 流程

```
基座层(docker compose)                宿主机管道(folia.pipeline)
─────────────────────                 ──────────────────────────────
RSSHub + fulltextrss + FreshRSS  ──▶  run-once: 抓取→清洗→聚合(SQLite)
  (抓取/全文/调度, :8080 GReader)        export: → data/frontpage.json
Ollama bge-m3 (本机, 不入 compose)       load:   → Neon Postgres(入库)
```

- **真相源**：`data/frontpage.sqlite`（清洗文章 + 聚合文章），在宿主机，不入 docker 卷。
- **入库目标**：Neon `stories` 表，主键 `story_id` = 聚合文章自增 id（`clusters.id`），稳定。

---

## 2. 前置

```bash
export PATH="$HOME/.orbstack/bin:$PATH"   # OrbStack docker CLI
cp .env.example .env                       # 填 FRESHRSS_* / DATABASE_URL
pip install -e .                           # 安装本包(含 psycopg, 入库要用)
ollama pull bge-m3                          # 本机 embedding
```

`.env` 需要：`FRESHRSS_API_URL` / `FRESHRSS_USER` / `FRESHRSS_API_PASSWORD`（基座层接入）、
`DATABASE_URL`（Neon 入库）。

> ⚠️ `.env` 的 `DATABASE_URL` 含 `&`，**不能 `source .env`**（shell 会把 `&` 当后台符）。
> 脚本里这样取：`export DATABASE_URL="$(grep '^DATABASE_URL=' .env | cut -d= -f2-)"`。
> docker-compose 按字面解析 `.env`，`${...}` 安全。

---

## 3. 启动基座层

```bash
./scripts/base-up.sh        # 拉起 rsshub/fulltextrss/freshrss 并等待就绪
./scripts/base-status.sh    # 状态
./scripts/base-down.sh      # 停
```

首次需在 `http://localhost:8080` 完成 FreshRSS 一次性配置（建账号 → 开 Google Reader API →
接全文 → 导入 OPML），详见 `config/freshrss/README.md`。FreshRSS 数据落宿主机
`./data/freshrss`（bind-mount，docker 销毁不丢）。

---

## 4. 跑管道 + 入库

```bash
export PYTHONPATH=src      # 或已 pip install -e . 则免

# 全链路(抓取→清洗→聚合, 写本地 SQLite)
python -m folia.pipeline.cli run-once

# 入库到 Neon(导出 + 写库)
./scripts/publish.sh
#   等价于:
#   python -m folia.pipeline.cli export      # → data/frontpage.json
#   python -m folia.pipeline.cli load        # → Neon(需 DATABASE_URL + psycopg)
```

其它子命令：`init-db`、`extract-pending`、`facts-pending`、`synthesize-pending`、
`inspect-cluster <id>`、`ingest-fixture <json>`、`serve`（本地预览 UI）。

**入库语义（快照）**：`load` 在事务内把全表标记 `active=false`，再按 `story_id` upsert
当前批为 `active=true`、覆盖内容、**不动 `like_count`** → 重跑保留计数；消失的 story 留着但
`active=false`。

---

## 5. 聚合模型（关键不变量）

聚合文章（`clusters`）是**持久实体，只更新、不丢/不裂/不合**。每轮：

- **Phase 1（定向分配）**：新文章归入最近的现有簇（余弦 ≥ 严格阈值 0.85），否则未认领。
- **Phase 2（新簇）**：未认领的新文章彼此聚成新簇。

没有"簇+簇→合并"操作，所以簇永不合并，`story_id` 由此天然稳定，likes 安全。详见
`docs/data-pipeline-technical-design.md`。

---

## 6. 排错

| 现象 | 原因 | 解决 |
|------|------|------|
| `source .env` 报 `&` / `command not found` | DATABASE_URL 含 `&` | 用 `grep ... \| cut -d= -f2-` 取值 |
| `load` 报 `ModuleNotFoundError: psycopg` | 环境没装 psycopg | `pip install -e .` 或 `pip install 'psycopg[binary]'` |
| `DATABASE_URL is not set` | `.env` 没该行 / 没注入 | 确认 `.env` 有 `DATABASE_URL` |
| FreshRSS 拉取失败 | 基座层没起 / OPML 没导 | `./scripts/base-status.sh`，查 `config/freshrss/README.md` |
| 聚类全走 Jaccard 降级 | Ollama 不可达 | `ollama serve` + `ollama pull bge-m3` |

查 Neon 数据：
```bash
export DATABASE_URL="$(grep '^DATABASE_URL=' .env | cut -d= -f2-)"
python -c "import os,psycopg;print(psycopg.connect(os.environ['DATABASE_URL']).execute('SELECT count(*) FROM stories WHERE active').fetchone())"
```
