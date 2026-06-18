# 数据链路技术设计

## 1. 目标范围

本阶段实现后台数据链路,不做 UI、PWA、每日版面、重要性排序、跨日进展闸门和定时调度。

传输层与全文抽取层不自研,直接复用成熟开源组件(对应 `daily-frontpage-spec.md` 2.1/2.2 的「零开发基座层」决策):

```text
RSSHub(造源) ─┐
官方 RSS ──────┴─► FreshRSS(订阅管理/拉取调度,内置全文抽取经 FiveFilters)
                        │ Google Reader API(已是全文)
                        ▼
   入库去重 -> 正文文本化 -> 事件聚类(bge-m3 embedding) -> 单篇事实抽取 -> 多源融合重排 -> SQLite
```

核心原则:去重聚合时不丢弃相似报道,而是把同一事件的多篇文章聚成一个 cluster,再融合成一篇信息更完整、带信源引用的压缩完整稿。

pipeline 自身**不抓网页、不解析 RSS**——这两件事分别由 FreshRSS + FiveFilters、FreshRSS 完成。pipeline 只通过 Google Reader API 读取已经是全文的内容。

## 2. 目录结构

```text
docker-compose.yml          # 基座层:rsshub / fulltextrss / freshrss
.env.example                # FreshRSS / Ollama / 模型 key(占位)
config/
  sources.toml              # 源元数据映射(tier/category),不再驱动拉取
  settings.toml             # FreshRSS/embeddings/dedupe/model 配置
  freshrss/
    subscriptions.opml      # 可复现订阅清单
    README.md               # 基座接线说明
src/frontpage_pipeline/
  cli.py                    # CLI 入口
  config.py                 # 配置 + load_source_map
  freshrss_client.py        # FreshRSS Google Reader API 客户端
  extractor.py              # html_to_text(content HTML -> 文本)
  embeddings.py             # Ollama bge-m3 + 余弦 + 质心
  dedupe.py                 # 事件聚类(embedding 主 / Jaccard 降级)
  facts.py                  # 单篇事实抽取
  synthesizer.py            # 多源融合重排
  model_client.py           # OpenAI/Claude/Gemini/XinAPI/heuristic
  db.py / models.py / text.py / viewer.py
tests/
  fixtures/freshrss_reading_list.json
```

## 3. 数据源与订阅

订阅、拉取调度、全文抽取全部由 FreshRSS 拥有。RSSHub 给没有 RSS 的站(公众号/微博/晚点)造源,作为 FreshRSS 的上游订阅。

`config/sources.toml` 不再驱动拉取,而是**源元数据映射**:把 FreshRSS feed 关联到 `tier`/`category`,供聚类与后续重要性排序使用。匹配优先级 `stream_id`(形如 `feed/3`)> `match`(对 `origin.title`),未匹配默认 `tier="unknown"` / `category="uncategorized"`。

```toml
[[sources]]
match = "AP News"        # 对 FreshRSS origin.title
stream_id = "feed/3"     # 可选,导入后才知道,优先级更高
name = "AP"
tier = "wire"
category = "international"
```

FreshRSS 连接参数:非密钥项在 `settings.toml [freshrss]`,密钥从环境变量读取(`FRESHRSS_API_URL` / `FRESHRSS_USER` / `FRESHRSS_API_PASSWORD`)。

### Google Reader API 契约(已核实)

base `/api/greader.php`:
- 认证:POST `/accounts/ClientLogin`,body `Email=<user>&Passwd=<api_password>` → 响应含 `Auth=<user>/<hash>`;后续请求头 `Authorization: GoogleLogin auth=<user>/<hash>`。
- 拉未读:GET `/reader/api/0/stream/contents/user/-/state/com.google/reading-list?output=json&xt=user/-/state/com.google/read&n=<batch>&c=<continuation>`。响应顶层 `continuation`(翻页),`items[]` 每项含 `id`/`title`/`canonical[].href`(或 `alternate`)/`published`(unix 秒)/`summary.content`(全文 HTML)/`origin.streamId`/`origin.title`。
- 标已读(可选):POST `/reader/api/0/edit-tag`,body `i=<id>&a=user/-/state/com.google/read&T=<token>`,token 来自 GET `/reader/api/0/token`。

## 4. 数据库存储

SQLite + WAL(pipeline 写、app 读可并存)。

```sql
CREATE TABLE articles (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,            -- FreshRSS streamId
  source_name TEXT NOT NULL,
  source_tier TEXT,
  category TEXT,                      -- 由 sources.toml 映射解析
  external_id TEXT,                   -- FreshRSS item id,用于幂等与 mark-read
  guid TEXT,
  url TEXT NOT NULL,
  canonical_url TEXT,
  title TEXT NOT NULL,
  summary TEXT,
  content_html TEXT,                  -- 来自 summary.content 的全文 HTML
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  extracted_text TEXT,                -- content_html 文本化结果
  article_facts TEXT,
  extract_status TEXT,                -- pending | ok | fallback_summary | empty
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

-- cluster_articles / cluster_sources 与上一版相同(引用编号机器)。
```

- 不再有 `html` 列和 fetch/crawl 概念。
- `cluster_sources.source_no`:融合稿中的 `[1]`、`[2]` 引用编号,机制不变。

## 5. 去重与事件聚类

第一层硬去重(避免重复入库):`guid`(= FreshRSS item id)相同、canonical URL 相同、`UNIQUE(external_id)`。靠 `insert_article` 的 `IntegrityError` 兜底。

第二层语义聚类:

```text
title + summary/extracted_text 前 500 字
-> bge-m3 embedding(via Ollama)
-> 与各 active cluster 的 centroid 余弦比较
-> 超过阈值归入,否则新建簇;质心增量更新(running mean)
```

```toml
[dedupe]
same_event_threshold = 0.82   # embedding 余弦
jaccard_threshold = 0.42      # 无 embedding 时降级
```

Ollama 不可达时**自动降级**为 Jaccard 词重叠(对比代表文章文本),保证离线/无 GPU 环境与单元测试的确定性。降级在一次 run 开始时探测一次(`is_available`),整轮一致。

## 6. 正文文本化

不抓网页。`content_html`(FreshRSS 经 FiveFilters 取回的全文 HTML)→ `extractor.html_to_text` → `extracted_text`:

1. `html_to_text(content_html)` 非空 → `extract_status='ok'`。
2. 否则有 `summary` → 降级 `fallback_summary`。
3. 都没有 → `empty`。

付费墙策略归 FreshRSS / 未来 app 层(只展示摘要+链接),pipeline 不处理。

## 7. 单篇事实抽取

与上一版相同:每篇文章 → 结构化事实包 JSON(`facts`/`numbers`/`quotes`/`background`/`uncertainties`),带 `source_no`。`heuristic` 离线兜底,或选模型 provider。不补写原文没有的信息。

## 8. 多源融合重排

cluster 层 `synthesized_text` 为主产物:基于多源事实包生成的压缩完整稿,每个关键事实带 `[source_no]` 引用,冲突写入「分歧与不确定」,Sources 段列全部来源。引用编号始终从 `cluster_sources` 取权威值(即使事实在聚类前抽取也正确)。

模型 provider 经 `config/settings.toml [model]` 选择(heuristic/openai/claude/gemini/xinapi),调用失败回退启发式,不中断 pipeline。

## 9. CLI 命令

```bash
frontpage-pipeline init-db
frontpage-pipeline run-once          # 从 FreshRSS 拉未读 -> 文本化 -> 聚类 -> 事实 -> 合成
frontpage-pipeline extract-pending
frontpage-pipeline facts-pending
frontpage-pipeline synthesize-pending
frontpage-pipeline ingest-fixture tests/fixtures/freshrss_reading_list.json
frontpage-pipeline inspect-cluster 1
frontpage-pipeline serve --port 8000
```

`run-once` 拉取依赖 FreshRSS 可达;`ingest-fixture` 吃录制的 Reader API JSON,离线可跑完整编辑层。

## 10. 验收标准

- 基座层 `docker compose up` 后,FreshRSS 全源更新,curl 能拉到带全文的未读项。
- `run-once` 单次成功入库;重复运行靠 `UNIQUE(external_id)` 不产生重复。
- 同一事件多源报道进入同一 cluster(embedding 路径;离线降级 Jaccard)。
- 已解析正文生成 `article_facts` 成功率 ≥90%。
- ≥2 篇有效文章的 cluster 生成 `synthesized_text`,关键事实带编号引用,每个编号能在 `cluster_sources` 追溯。
- 失败有状态记录,不导致 pipeline 崩溃。

## 11. 后续阶段(不在本阶段)

- UI/PWA、每日头版 layout、重要性评分(scorer)、版面排序(ranker)
- 跨日进展闸门、用户已读状态
- cron 调度、MCP/对话式查询 API

(FreshRSS 集成、embedding 去重已在本阶段完成,从原「后续」列表移出。)
