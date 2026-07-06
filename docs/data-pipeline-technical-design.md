# 数据链路技术设计

## 1. 目标范围

本仓库实现后台数据链路:**抓取 → 清洗 → 分类 → 聚合 → 合成 → 入库**。不做 UI、PWA、每日版面、重要性排序、跨日进展闸门。谁去读入库结果(查询/检索/点赞)是另一个消费端应用的事。

抓取、全文抽取、去重、控制面板全部在应用内自研,不再依赖外部订阅服务:

```text
RSSHub(给没有原生 RSS 的站造源) ─┐
官方 RSS/Atom ────────────────────┴─► 内置 poller(feedparser 解析)
                                         │
   去重入库 -> trafilatura 抽全文 -> 内容分类(LLM) -> 事件聚类(bge-m3 embedding)
        -> 单篇事实抽取 -> 多源融合重排 -> 本地 SQLite -> (可选) 导出入 Neon
```

核心原则:去重聚合时不丢弃相似报道,而是把同一事件的多篇文章聚成一个 cluster,再融合成一篇信息更完整、带信源引用的压缩完整稿。

pipeline **自己抓取、自己解析 RSS、自己抽全文**——没有中间订阅服务。RSSHub 只作为「给没有 RSS 的站造源」的上游,输出仍是普通 RSS,由 poller 直接抓。

## 2. 目录结构

```text
docker-compose.yml          # rsshub(:1200) + panel(:8000)
Dockerfile                  # panel 镜像
src/folia/pipeline/
  cli.py                    # CLI 入口 + run-once 编排
  config.py                 # 内置默认 + DB settings 还原成嵌套 dict; PROVIDERS 表
  poller.py                 # 自写轮询抓取器(feedparser, 条件请求, 去重入库)
  extractor.py              # html_to_text + trafilatura 抓全文
  categorize.py             # 按内容给文章定目录(走 model_client)
  embeddings.py             # Ollama bge-m3 + 余弦 + 质心
  dedupe.py                 # 事件聚类(embedding 主 / Jaccard 降级)
  facts.py                  # 单篇事实抽取
  synthesizer.py            # 多源融合重排
  model_client.py           # 多 provider LLM 客户端(见 §8)
  db.py / models.py / text.py / viewer.py
  store/export.py           # SQLite -> frontpage.json
  store/loader.py           # frontpage.json -> Neon Postgres
  panel/                    # FastAPI 控制面板 + 应用内 pipeline 循环
tests/
  fixtures/sample_feed.xml
```

配置**全部存 SQLite**(`settings`/`feed`/`directory` 表),在控制面板里编辑;没有 `.toml` 配置文件。唯一引导项是 db 路径(`FOLIA_DB_PATH` env 或默认 `data/frontpage.sqlite`)。

## 3. 数据源与订阅

`feed` 表就是订阅真身(本地即真身),无账号/无密码/无外部 API。poller 每轮遍历 `enabled` 的源,直接抓取。RSSHub 给没有原生 RSS 的站(公众号/微博/晚点)造源,作为上游订阅地址(如 `http://rsshub:1200/latepost`)。

分类**不再挂在源上**:文章的 `category` 由 `categorize.py` 按新闻**内容**用 LLM 归到 `directory` 表(用户在面板「目录」页维护)里的某个目录。`source_tier` 列保留但已废弃(重要性以后从内容算,不挂在源上)。

`feed` 表字段:`url`(主键,即订阅地址)、`name`、`description`、`etag`/`modified`(条件请求)、`last_fetched_at`/`last_status`、`enabled`。

## 4. 数据库存储

SQLite + WAL(pipeline 写、app 读可并存)。

```sql
CREATE TABLE articles (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,            -- 来源 feed 的 url(feed 表即真身)
  source_name TEXT NOT NULL,
  source_tier TEXT,                   -- 已废弃(留空)
  category TEXT,                      -- 由 categorize(内容 LLM)填, 初始空
  external_id TEXT,                   -- entry id/guid, 幂等去重用
  guid TEXT,
  url TEXT NOT NULL,
  canonical_url TEXT,
  title TEXT NOT NULL,
  summary TEXT,
  content_html TEXT,                  -- entry 自带正文(content/summary)
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  extracted_text TEXT,                -- content_html 文本化 + trafilatura 补全
  article_facts TEXT,
  extract_status TEXT,                -- ok | ok_fulltext | fallback_summary | empty
  fact_status TEXT,
  content_hash TEXT,
  cluster_id INTEGER,
  UNIQUE(source_id, guid),
  UNIQUE(canonical_url),
  UNIQUE(external_id)
);

CREATE TABLE clusters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  representative_article_id TEXT,
  title TEXT,
  centroid BLOB,                      -- array('f') 打包的 embedding 质心
  source_count INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  synthesized_text TEXT,
  synthesis_status TEXT,
  synthesis_model TEXT,
  synthesis_updated_at TEXT,
  status TEXT NOT NULL DEFAULT 'active'
);

-- 簇成员由 articles.cluster_id 指向; cluster_sources 存融合稿引用编号机器。
```

- `cluster_sources.source_no`:融合稿中的 `[1]`、`[2]` 引用编号。

## 5. 去重与事件聚类

第一层硬去重(避免重复入库):`guid` 相同、canonical URL 相同、`UNIQUE(external_id)`。靠 `insert_article` 的 `IntegrityError` 兜底。

第二层语义聚类:

```text
title + summary/extracted_text 前若干字
-> bge-m3 embedding(via Ollama)
-> 与各 active cluster 的 centroid 余弦比较
-> 超过阈值归入, 否则新建簇; 质心增量更新(running mean)
```

聚类是**两阶段定向分配**(聚合不变量,见 §6):

- **Phase 1(定向分配)**:新文章归入最近的现有簇(余弦 ≥ 严格阈值),否则未认领。
- **Phase 2(新簇)**:未认领的新文章彼此聚成新簇。

阈值在 `settings` 表 `dedupe`(`same_event_threshold` / `jaccard_threshold` / `lookback_hours`)。Ollama 不可达时**自动降级**为 Jaccard 词重叠,保证离线/无 GPU 与单测确定性;降级在一次 run 开始探测一次,整轮一致。

## 6. 聚合不变量(关键设计)

聚合文章(`clusters`)是**持久实体,只更新、不丢/不裂/不合**:

- 没有「簇 + 簇 → 合并」操作,所以簇永不合并,`story_id`(= `clusters.id` 自增)天然稳定,可作为下游 upsert 键。
- 综述只重算本轮新建/更新过的簇。

这条不变量是入库语义(§9 的快照 upsert 不动 `like_count`)成立的前提。

## 7. 正文文本化

`content_html`(entry 自带正文)→ `extractor.html_to_text` → `extracted_text`,并在内容太短时**用 trafilatura 抓原文页**补全:

1. `html_to_text(content_html)` → `extract_status='ok'`。
2. 文本过短且有 url → `fetch_fulltext(url)`(trafilatura),更长则采用,`ok_fulltext`。
3. 仍为空 → 降级 `summary`(`fallback_summary`)或 `empty`。

## 8. 分类 · 事实 · 融合 · 模型

- **分类**(`categorize.py`):把文章内容 + 目录清单交给所选 provider 的 LLM,只回一个目录名;不匹配/失败落兜底目录「综合」。
- **单篇事实**(`facts.py`):每篇 → 结构化事实包 JSON(`facts`/`numbers`/`quotes`/`background`/`uncertainties`),带 `source_no`。不补写原文没有的信息。
- **多源融合**(`synthesizer.py`):cluster 层 `synthesized_text` 为主产物,每个关键事实带 `[source_no]` 引用,冲突写「分歧与不确定」,Sources 段列全部来源。引用编号始终从 `cluster_sources` 取权威值。
- **模型客户端**(`model_client.py`):按功能选 provider + 模型。`create_model_client(settings, function)` 读 `models.<function> = {provider, model}` 与 `providers.<provider> = {endpoint, api_key}`。provider 为空或 model 为空 → `enabled=False`,消费方(facts/synthesizer/categorize)退回规则。协议四类:
  - `ollama`(本地):`/api/chat`,无 key;
  - `openai` / `deepseek` / `qwen` / `xinapi`:OpenAI 兼容 `/chat/completions`,Bearer;
  - `claude`:Anthropic `/v1/messages`,x-api-key;
  - `gemini`:`/models/{model}:generateContent?key=`。
  远程 provider 缺 key / 调用失败 → 抛 `ModelError`,消费方 catch 后退回规则,不中断 pipeline。API key / endpoint 从环境变量读(`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `XIN_API_KEY`),不在面板配置;面板只按功能选 provider + 预置模型。

## 9. 导出与入库

- `export`:SQLite → `data/frontpage.json` 快照。
- `load`:快照 → Neon。事务内标全表 `active=false`,按 `story_id` upsert 当前批为 `active=true`、覆盖内容、**不动 `like_count`**。dsn 从 `settings.database.url` 取并传入 `loader.load(path, dsn)`。
- 面板循环每轮跑 `run-once`;若配了 `database.url` 则顺带 export + load。

## 10. CLI 命令

```bash
folia-pipeline init-db
folia-pipeline run-once          # poll -> extract -> categorize -> cluster -> facts -> synthesize
folia-pipeline extract-pending
folia-pipeline categorize-pending
folia-pipeline facts-pending
folia-pipeline synthesize-pending
folia-pipeline export --out data/frontpage.json
folia-pipeline load --in data/frontpage.json
folia-pipeline inspect-cluster 1
folia-pipeline ingest-fixture tests/fixtures/sample_feed.xml
folia-pipeline panel --port 8000
```

`ingest-fixture` 把本地 feed 文件当成一个源,走 poller 解析,离线可跑完整编辑层。

## 11. 验收标准

- `run-once` 单次成功入库;重复运行靠 `UNIQUE(external_id)` 不产生重复。
- 同一事件多源报道进入同一 cluster(embedding 路径;离线降级 Jaccard)。
- 已解析正文生成 `article_facts` 成功率高;失败有状态记录,不导致 pipeline 崩溃。
- ≥2 篇有效文章的 cluster 生成 `synthesized_text`,关键事实带编号引用,每个编号能在 `cluster_sources` 追溯。
- 配了 `database.url` 时,导出快照 load 进 Neon,`active` 翻新且 `like_count` 不丢。

## 12. 后续阶段(不在本仓库)

- UI/PWA、每日头版 layout、重要性评分与版面排序。
- 跨日进展闸门、用户已读/点赞状态。
- cron 调度、MCP/对话式查询 API。
