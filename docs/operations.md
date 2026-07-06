# 运维文档（folia）

folia 是个纯后端数据管道：**抓取 → 清洗 → 聚合 → 入库**。产物是 Neon Postgres 里就绪的
聚合数据；谁去读它（查询/检索/点赞）是另一个消费端应用的事，不在本仓库。

日常操作都在**控制面板**里点：配置、启停循环、改间隔、管数据源、看预览。

包：`folia.pipeline`（`src/folia/pipeline/`，PEP 420 命名空间）。

---

## 1. 架构

```
docker compose 一套:
  rsshub    给没有原生 RSS 的站造源(:1200), 纯 URL 无账号
  panel     控制面板(:8000) = Web 控制台 + 应用内 pipeline 循环(自带抓取/全文/去重)
宿主机: Ollama bge-m3 (ollama pull bge-m3), 经 host.docker.internal 供 panel 调用
```

- **控制面板 = 一个应用**：Web 控制台 + 内部循环。启停/间隔/配置/数据源都在面板里，配置存 SQLite。
- **真相源**：`data/frontpage.sqlite`（清洗文章 + 聚合文章 + settings 配置），bind-mount 到宿主机 `./data`，docker 销毁不丢。
- **入库目标**：Neon `stories` 表，`story_id` = 聚合文章自增 id（`clusters.id`）。

---

## 2. 起停

```bash
export PATH="$HOME/.orbstack/bin:$PATH"
ollama pull bge-m3               # 本机 embedding(一次)

./scripts/folia.sh start         # 构建并拉起 基座层 + 控制面板
./scripts/folia.sh status        # 容器 + 端口探测
./scripts/folia.sh stop          # 停整套(数据在 ./data, 不丢)
```

`start` 后：**控制面板 http://localhost:8000/admin**。首次空配置也能起来。

---

## 3. 首次配置（全在面板里）

1. 面板 **数据源**：「导入默认订阅」或手动加 RSS/Atom 地址(feed 表就是订阅真身)。
2. 面板 **数据同步**：填 `database.url`（Neon，留空则只本地聚合不入库）。
3. 面板 **模型**：给各功能(分类/综述/事实)选 provider + 模型；远程 provider 在此填 API key 与 endpoint。embedding 固定本地 Ollama。留空 provider = 走规则。
4. 面板 **抓取**：设间隔并「启动循环」；也可「立即抓取」跑一轮。

配置存在 SQLite `settings` 表；消费方(poller/embeddings/dedupe/model_client)直接读还原出的嵌套 dict。

---

## 4. 循环与入库语义

- 循环由 panel 应用内的后台线程负责；`loop_enabled` 控制启停、`interval` 控制固定间隔。
- 每轮：`run-once`（抓取→清洗→分类→聚合，写本地 SQLite）；若配了 `database.url` 则顺带 export + load 入库。
- **入库快照**：`load` 事务内标全表 `active=false`，按 `story_id` upsert 当前批为 `active=true`、覆盖内容、**不动 `like_count`**。

---

## 5. 聚合模型（关键不变量）

聚合文章（`clusters`）是**持久实体，只更新、不丢/不裂/不合**。每轮：

- **Phase 1（定向分配）**：新文章归入最近的现有簇（余弦 ≥ 严格阈值 0.85），否则未认领。
- **Phase 2（新簇）**：未认领的新文章彼此聚成新簇。

没有"簇+簇→合并"操作，所以簇永不合并，`story_id` 天然稳定。综述只重算本轮新建/更新过的簇。
详见 `docs/data-pipeline-technical-design.md`。

---

## 6. 排错

| 现象 | 原因 | 解决 |
|------|------|------|
| 某个源抓取失败 | 源地址失效 / rsshub 没起 | 面板 数据源 看 last_status; `folia.sh status` 看 rsshub |
| 分类/综述全落规则或兜底 | 对应功能没选模型 / 远程 provider 缺 key | 面板 模型 选 provider+模型并填 key |
| 聚类全走 Jaccard 降级 | Ollama 不可达 | 本机 `ollama serve` + `ollama pull bge-m3`(容器经 host.docker.internal 调) |
| 面板起不来 / 构建失败 | 拉基础镜像网络瞬断 | 重试 `./scripts/folia.sh start` |

## 7. 本地开发

```bash
./scripts/folia.sh install       # venv + pip install -e .
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
# 也可在宿主机直接起面板: PYTHONPATH=src .venv/bin/python -m folia.pipeline.cli panel
```
