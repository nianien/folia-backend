# 数据链路技术设计

## 1. 目标范围

本阶段只实现后台数据链路，不做 UI、PWA、每日版面、重要性排序、跨日进展闸门和定时调度。

目标是打通：

```text
TOML 数据源订阅 -> Feed 拉取 -> 文章入库去重 -> 原文抓取 -> 正文解析
-> 事件聚类 -> 单篇事实抽取 -> 多源融合重排 -> SQLite 存储
```

核心原则：去重聚合时不丢弃相似报道，而是把同一事件的多篇文章聚成一个 cluster，再融合成一篇信息更完整、带信源引用的压缩完整稿。

## 2. 目录结构

```text
config/
  sources.toml              # RSS/Atom 数据源
  settings.toml             # 阈值、模型、抓取规则
pipeline/
  main.py                   # CLI 入口
  fetcher.py                # 拉取 Feed
  normalizer.py             # 标准化 Feed item
  crawler.py                # 抓取原网页 HTML
  extractor.py              # 正文解析
  dedupe.py                 # 硬去重 + 事件聚类
  facts.py                  # 单篇事实抽取
  synthesizer.py            # 多源融合重排
  db.py                     # SQLite 访问
  models.py                 # 数据结构
  model_client.py           # OpenAI/Claude/Gemini 模型调用
prompts/
  fact_extraction.txt
  cluster_synthesis.txt
data/
  frontpage.sqlite
tests/
  fixtures/
```

## 3. 数据源订阅

第一版直接读取 `config/sources.toml`，暂不接 FreshRSS，减少外部依赖。

```toml
[[sources]]
id = "guardian_world"
name = "Guardian World"
url = "https://www.theguardian.com/world/rss"
tier = "broadsheet"
category_hint = "international"
enabled = true
```

每条 Feed item 统一标准化为：

```json
{
  "source_id": "guardian_world",
  "source_name": "Guardian World",
  "source_tier": "broadsheet",
  "title": "...",
  "url": "...",
  "guid": "...",
  "published_at": "...",
  "summary": "..."
}
```

## 4. 数据库存储

使用 SQLite。后续应用层读库时启用 WAL，保证 pipeline 写入和 app 读取可以并存。

```sql
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  tier TEXT NOT NULL,
  category_hint TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_fetched_at TEXT,
  last_error TEXT
);

CREATE TABLE articles (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_tier TEXT,
  guid TEXT,
  url TEXT NOT NULL,
  canonical_url TEXT,
  title TEXT NOT NULL,
  summary TEXT,
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  html TEXT,
  extracted_text TEXT,
  article_facts TEXT,
  fetch_status TEXT NOT NULL,
  extract_status TEXT,
  fact_status TEXT,
  content_hash TEXT,
  cluster_id INTEGER
);

CREATE TABLE clusters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  representative_article_id TEXT,
  title TEXT,
  source_count INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  synthesized_text TEXT,
  synthesis_status TEXT,
  synthesis_model TEXT,
  synthesis_updated_at TEXT,
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE cluster_articles (
  cluster_id INTEGER NOT NULL,
  article_id TEXT NOT NULL,
  similarity REAL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (cluster_id, article_id)
);

CREATE TABLE cluster_sources (
  cluster_id INTEGER NOT NULL,
  source_no INTEGER NOT NULL,
  article_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  published_at TEXT,
  PRIMARY KEY (cluster_id, source_no)
);
```

字段含义：

- `articles.extracted_text`：单篇文章解析出的正文。
- `articles.article_facts`：单篇文章抽取出的结构化事实 JSON。
- `clusters.synthesized_text`：同一事件多源融合后的 Markdown 完整稿。
- `cluster_sources.source_no`：融合稿中的 `[1]`、`[2]` 等引用编号。

## 5. 去重与事件聚类

去重分三层。

第一层：硬去重，避免重复入库。

- `guid` 相同
- canonical URL 相同
- URL 归一化后相同
- `source_id + title + published_date` 相同

第二层：内容 hash，识别同源改稿、转载或重复条目。

```text
hash(normalized_title + normalized_summary_or_text_prefix)
```

第三层：语义聚类，把多源相似报道归为同一事件。

```text
title + summary/extracted_text 前 500 字
-> embedding
-> 与 lookback 窗口内 cluster centroid 比较
-> 相似度超过阈值则归入该 cluster
```

初始配置：

```toml
[dedupe]
same_event_threshold = 0.82
exact_content_threshold = 0.96
lookback_hours = 48
```

如果 embedding 暂不可用，可以临时用标题相似度和关键词重合降级，但目标实现应使用 embedding。

## 6. 抓取与正文解析

对每篇新增文章执行：

1. 抓取原文 HTML。
2. 使用正文抽取器生成 `extracted_text`。
3. 写入 HTML、正文和状态。
4. 失败不阻塞整条 pipeline。

正文解析优先级：

1. `trafilatura`
2. `readability-lxml`
3. RSS summary fallback

状态枚举：

```text
fetch_status: pending | ok | failed | skipped
extract_status: pending | ok | failed | paywalled | fallback_summary
```

付费墙域名不绕过，只保留摘要、来源和原文链接。

## 7. 单篇事实抽取

在多源融合前，先把每篇文章转换成结构化事实包，避免直接把多篇全文塞给模型。

```json
{
  "article_id": "abc123",
  "source_no": 1,
  "source_name": "Reuters",
  "title": "...",
  "facts": [
    {
      "text": "公司宣布裁员 10%。",
      "type": "core_fact"
    }
  ],
  "numbers": ["约 1200 名员工受影响"],
  "quotes": [
    {
      "speaker": "CEO",
      "text": "..."
    }
  ],
  "background": ["..."],
  "uncertainties": ["价格尚未披露"]
}
```

事实抽取规则：

- 不补写原文没有的信息。
- 保留时间、地点、人物、机构、数字、因果关系和明确后续影响。
- 删除广告语、重复段落和低价值引述。
- 信息不足时省略字段，或标记为未知。

## 8. 多源融合重排

cluster 层的 `synthesized_text` 是本阶段的主产物。它不是摘要，而是基于多篇报道生成的压缩完整稿。

模型 provider 通过 `config/settings.toml` 选择：

```toml
[model]
provider = "openai" # heuristic | openai | claude | gemini | xinapi

[model.openai]
model = "gpt-4.1-mini"
api_key_env = "OPENAI_API_KEY"

[model.claude]
model = "claude-3-5-haiku-latest"
api_key_env = "ANTHROPIC_API_KEY"

[model.gemini]
model = "gemini-1.5-flash"
api_key_env = "GEMINI_API_KEY"

[model.xinapi]
model = "deepseek-ai/DeepSeek-R1"
api_key_env = "XIN_API_KEY"
endpoint = "https://airouter.xincache.cn/v1/chat/completions"
```

默认 `heuristic` 不访问网络，便于离线测试。配置为 `openai`、`claude`、`gemini` 或 `xinapi` 后，`facts-pending` 和 `synthesize-pending` 会调用对应模型；调用失败时回退到启发式结果，避免整条 pipeline 中断。

输出 Markdown：

```markdown
# 事件标题

## 核心事实

事件的核心事实，后面必须带信源编号。[1][2]

## 关键细节

Reuters 报道了某个数字，AP 给出了另一个数字。[1][3]

## 背景

理解该事件所需的背景信息。[2]

## 分歧与不确定

目前各来源均未披露具体价格。[1][2][3]

---

## Sources

[1] Reuters · Article title · https://...
[2] AP · Article title · https://...
```

引用规则：

- 每个关键事实必须标注 `[source_no]`。
- 多个来源支持同一事实时，合并表述并标多个引用，如 `[1][3]`。
- 来源说法冲突时，不强行合并，写入“分歧与不确定”。
- 不允许加入无来源支持的背景、推论、数字或因果关系。

## 9. CLI 命令

第一阶段提供可重复执行、可局部重试的命令：

```bash
python -m pipeline.main run-once
python -m pipeline.main crawl-pending
python -m pipeline.main extract-pending
python -m pipeline.main facts-pending
python -m pipeline.main synthesize-pending
python -m pipeline.main inspect-cluster 123
```

`run-once` 执行完整链路；其他命令用于调试和重跑失败阶段。

## 10. 验收标准

数据链路完成时应满足：

- 配置 5-10 个 RSS/Atom 源后，单次运行能成功入库文章。
- 重复运行不会产生重复文章。
- 同一事件的多篇报道能进入同一 cluster。
- 非付费墙文章正文解析成功率达到 70% 以上。
- 已解析正文生成 `article_facts` 的成功率达到 90% 以上。
- 包含两篇以上有效文章的 cluster 能生成 `synthesized_text`。
- `synthesized_text` 中的关键事实带编号引用。
- 每个引用编号都能在 `cluster_sources` 中追溯到具体文章。
- 失败有明确状态记录，不导致整条 pipeline 崩溃。

## 11. 后续阶段

以下内容不在本阶段实现：

- UI/PWA
- 每日头版 layout
- 重要性评分
- 跨日进展闸门
- 用户已读状态
- FreshRSS 集成
- cron 调度
- MCP 或对话式查询 API
