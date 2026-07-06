# 「头版」(Frontpage) — 私人新闻 App
## 产品方案 + 技术落地 Spec v3

> 产品模型:**每日一期 + 拉模式**。版面只含当天新闻;后台管道持续抓取整理,用户随时打开浏览,像普通新闻 app,接受滞后(小时级),换取干净、去重、按目录归类的阅读体验。
>
> v3 变更(对齐当前实现):基座层从 FreshRSS/FullTextRSS/GReader 换成**应用内自研 poller + trafilatura**;分类从「源标签 tier/category」换成**按内容的 LLM 分类到用户维护的目录**;本地 SQLite 为真相源,**导出入 Neon Postgres** 供未来消费端 app 读取。重要性排序(scorer/ranker)、跨日进展闸门、前台 PWA 仍是**未建的路线图**。

---

# Status vs spec(实现进度对照)

| 区块 | 状态 | 说明 |
|------|------|------|
| 基座:RSSHub 造源 + 应用内 poller | ✅ 已落地 | `docker-compose.yml`(rsshub + panel);`poller.py` 用 feedparser 直接抓,条件请求 |
| extractor(全文落库) | ✅ | `extractor.py`:entry 正文 + `trafilatura` 抓原文页补全 |
| 内容分类(LLM → 目录) | ✅ | `categorize.py` 归到 `directory` 表(面板「目录」页维护) |
| 当日聚类(bge-m3 余弦,两阶段) | ✅ | `embeddings.py` + `dedupe.py`;离线降级 Jaccard |
| 单篇事实抽取 + 多源融合带引用 | ✅ | `facts.py` + `synthesizer.py` + `cluster_sources` 编号 |
| 多 provider 模型(按功能可切换) | ✅ | `model_client.py`;面板「模型」页选 provider+模型、填 key |
| 控制面板(配置/启停/数据源/目录/模型) | ✅ | `panel/`(FastAPI),配置全存 SQLite |
| 导出入 Neon | ✅ | `store/export.py` + `store/loader.py`;面板填 `database.url` |
| 重要性排序(scorer / ranker / layout) | ❌ 未建 | 当前是按目录归类,不做重要性排序 |
| 跨日进展闸门 | ❌ 未建 | 需 `prior_cluster_id` + LLM 判断 |
| 前台应用(三页面 PWA / 已读) | ❌ 未建 | 当前仅 `viewer.py` 本地预览 + 面板预览 |
| cron 调度 / MCP | ❌ 未建 | 循环由面板内后台线程负责,非 cron |

详见 `data-pipeline-technical-design.md`。

---

# Part 1 · 产品方案

## 1.1 一句话定义

一个只为一个人服务的新闻 App:后台管道从可信源持续拉取新闻,全文抽取、去重聚类、按内容归类;前台(未建)是一个安卓可安装的 PWA,打开即浏览,点进即读全文,**不跳出、无广告、无视频、无无限流**。

## 1.2 与商业新闻 App 的差异(产品的全部理由)

| 维度 | 商业 App | 本产品 |
|------|---------|--------|
| 排序目标 | 停留时长 | 重要性/归类(与兴趣解耦)——重要性排序为路线图 |
| 同一事件 | 推 20 遍 | 聚类成 1 条,多源并列 |
| 信息流 | 无限下拉 | 有限列表,刷到底就是底 |
| 正文 | 跳转/广告/视频 | 站内净化全文(reader mode) |
| 时效 | 分钟级 | 小时级(刻意接受,换干净) |

## 1.3 非目标

- ❌ 不做多用户/商业化
- ❌ 不做分钟级实时(突发交给 BBC Live / 澎湃)
- ❌ 不做评论/社交/收藏夹(MVP 只有"读")
- ❌ 不绕过付费墙(付费源只展示摘要 + 原文链接)
- ❌ 不做原生安卓 App(PWA 加主屏即可)

## 1.4 产品形态(前台交互目标,尚未实现)

### 导航:顶部 tab 一排

```text
头版                         更新于 19:05
─────────────────────────────────────────
国际  科技  中国  综合          ← 顶部 tab = directory 表里的目录
─────────────────────────────────────────
● 单行条目 ……………………………… 5 源
● 单行条目 ………………………………… 4 源
○ 单行条目(已读,置灰)…………… 3 源
─────────────────────────────────────────
        — 到底了 · 今日共 N 条 —
```

### 交互规则

1. **tab = 目录**:tab 来自 `directory` 表(面板「目录」页维护,建议 ≤6)。无底部导航、无侧边栏、无搜索、无收藏。
2. **分类由内容决定**:文章归哪个目录由 LLM 按**新闻内容**判,与来自哪个源无关。兜底目录「综合」。
3. **每个 tab 是有限列表**,展示窗口为当天(每日一期,跨日不残留);到底显示"共 N 条"。
4. **点击链固定两层**:列表 → 事件页(本簇多源报道列表)→ 阅读页(站内净化全文)。
5. **已读置灰但不消失**(当天版面是稳定的,不是流)。
6. 下拉刷新 = 读取管道最新结果,不触发实时爬取。

> 路线图:「要闻」重要性 tab、首条大卡、盲区保底、跨日进展闸门(附"此前:…")—— 需要 scorer/ranker,尚未实现。

### 事件页 / 阅读页(目标)

- 事件页:事件标题 + 一句话事实 + 口径分歧(多源不一致时)+ 按源列出的报道列表。
- 阅读页:trafilatura 净化正文,自有排版;顶部固定来源 + 原文链接;抽取失败降级为摘要 + 链接。

### 节奏

- 管道循环由面板控制(启停 + 固定间隔);当天版面随运行逐步充实,次日翻篇。

## 1.5 信源策略

`feed` 表就是订阅真身(面板「数据源」页增删/导入默认)。RSSHub 给没有原生 RSS 的站(公众号/晚点)造源,输出普通 RSS,由 poller 直接抓。默认源见 `config.DEFAULT_FEEDS`(AP News、Al Jazeera、Guardian World、BBC World、Hacker News、LatePost)。**tier 概念已废弃**——归类不挂在源上,由内容决定。

---

# Part 2 · 技术方案

## 2.1 架构总览

```text
┌─────────────────────────────────────────────────┐
│ RSSHub(给没有原生 RSS 的站造源, 纯 URL)          │
└──────────────────┬──────────────────────────────┘
                   │ 普通 RSS/Atom
┌──────────────────▼──────────────────────────────┐
│ 编辑层 pipeline(应用内循环)                       │
│  poll → extract → categorize → cluster           │
│        → facts → synthesize   全部落 SQLite       │
└──────────────────┬──────────────────────────────┘
                   │ export + load(可选)
┌──────────────────▼──────────────────────────────┐
│ Neon Postgres(stories 表)                        │
│  给未来消费端 app 读(查询/检索/点赞)              │
└─────────────────────────────────────────────────┘
   控制面板(FastAPI, :8000)= Web 控制台 + 应用内循环
   部署:docker-compose(rsshub + panel);Ollama 在宿主机
```

## 2.2 技术选型与理由

| 组件 | 选型 | 理由 |
|------|------|------|
| 造源 | RSSHub (Docker) | 公众号/晚点等无 RSS 站的路由现成 |
| 抓取 | 自写 poller + feedparser | 直接抓 RSS/Atom,条件请求(ETag/Last-Modified),无中间订阅服务 |
| 全文抽取 | trafilatura | 抽取质量好,内容不足时抓原文页补全,全文落库 |
| 后端 + 面板 | FastAPI + Jinja2 | 单服务、服务端渲染、无前端构建链 |
| 本地存储 | SQLite(WAL) | 管道写 / 预览读,单机;真相源 |
| 云存储 | Neon Postgres | 入库目标,供未来消费端 app 读 |
| Embedding | bge-m3 via Ollama(本地) | 中英混合去重,免费 |
| LLM(分类/综述/事实) | 多 provider 可切换 | 本地 Ollama 或远程 openai/claude/gemini/deepseek/qwen/xinapi;按功能各选,缺配则走规则 |
| 调度 | 面板内后台线程 | 不引入 cron/任务队列 |

**刻意不用:** React/Vue、向量数据库(窗口内暴力余弦即可)、LangChain、消息队列、ORM(直接 sqlite3/SQL)。

## 2.3 管道详设

`run-once` 编排:`poll → extract-pending → categorize-pending → cluster → facts-pending → synthesize-pending`;配了 `database.url` 时再 export + load。

- **poll**:遍历 `enabled` 的 feed,条件请求 + feedparser 解析,每条 entry → `insert_article` 去重入库(`UNIQUE(external_id/canonical_url/(source_id,guid))`)。entry 自带正文落 `content_html`。
- **extract**:`html_to_text(content_html)`;太短且有 url → `trafilatura` 抓原文页,更长则采用;仍空则降级摘要。状态 `ok/ok_fulltext/fallback_summary/empty`。
- **categorize**:内容 + 目录清单 → 所选 provider LLM,只回一个目录名;不匹配/失败落「综合」。
- **cluster**:`title + 正文前若干字` → bge-m3 embedding → 与 active 簇质心余弦比较,两阶段定向分配(Phase 1 归入现有簇,Phase 2 未认领的聚新簇);质心 running-mean 增量更新。Ollama 不可达降级 Jaccard。
- **facts**:每篇 → 结构化事实包 JSON,带 `source_no`。
- **synthesize**:簇层 `synthesized_text`,关键事实带 `[source_no]` 引用,冲突写「分歧与不确定」,Sources 段列全部来源。

聚合不变量:簇是持久实体,只更新、不丢/不裂/不合;`clusters.id` 自增,天然稳定,作为 Neon 侧 `story_id`。

## 2.4 数据库 Schema(SQLite,WAL)

实际表:`articles` / `clusters` / `cluster_sources` / `feed` / `directory` / `settings` / `sources`。核心两表见 `data-pipeline-technical-design.md` §4。要点:

- `articles.source_id` = 来源 feed 的 url;`category` 由内容分类填;`source_tier` 已废弃。
- `clusters.id` 自增 = 稳定 story 身份;`synthesized_text` 为主产物。
- 配置在 `settings` 表(点分键):`embeddings` / `dedupe` / `model` / `models.<function>` / `providers.<name>` / `database.url` / `loop.*`。

Neon 侧 `stories` 表:`load` 事务内标全表 `active=false`,按 `story_id` upsert 当前批为 `active=true`、覆盖内容、**不动 `like_count`**。

## 2.5 项目结构

见 `data-pipeline-technical-design.md` §2。要点:配置**全部存 SQLite**,面板编辑,无 `.toml`/`.yaml` 配置文件;唯一引导项是 db 路径(`FOLIA_DB_PATH`)。

---

# Part 3 · 执行状态

**已完成(后台数据链路)**:poller、extractor、内容分类、两阶段聚类、事实抽取、多源融合、多 provider 模型、控制面板、导出入 Neon。测试离线(stdlib `unittest`,tempfile SQLite,无网络)。

**未建(路线图)**:

- 重要性评分 scorer(impact/agenda/novelty/blindspot)+ 版面排序 ranker + `layout` 表。
- 跨日进展闸门(`prior_cluster_id` + LLM 判断实质进展,不确定默认放行)。
- 前台应用:FastAPI 三页面 + PWA(manifest/SW)+ 已读状态 + 管道健康警告条。
- cron/外部调度、MCP/对话式查询 API。

---

# Part 4 · 风险与边界

| 风险 | 应对 |
|------|------|
| 版权 | 全文净化展示仅限个人自部署;永远展示来源 + 原文链接;不公开部署、不分发内容 |
| 付费墙源 | 只展示摘要 + 链接,接受信息薄 |
| SQLite 并发 | WAL + 管道单写者,预览只读 |
| feed 失效 | 每源记 `last_status` / `last_fetched_at`,面板可见 |
| 管道挂掉 | 面板显式暴露最近运行状态;最差体验是"看起来正常但内容是旧的" |
| 云密钥/账号 | provider key 从环境变量读(不入库、不在面板配);远程 provider 账号失效不影响本地(可退回规则或本地 Ollama) |
| 合规(国内) | 个人自用、不公开服务 |

---

# Part 5 · 工程约束(给后续开发)

1. 后台链路刻意保持轻量;不引入 React/Vue/前端构建工具、LangChain、向量数据库、消息队列、ORM(直接 sqlite3/SQL)。
2. 所有运行期配置存 SQLite `settings` 表、面板编辑,代码不硬编码阈值;唯一引导项是 db 路径。
3. LLM 调用一律经 `model_client`,按功能选 provider;缺配/失败必须能退回规则,不中断 pipeline。
4. 测试用 `tests/fixtures` 离线样本,不依赖网络。
5. 前台若开工:衬线正文、报纸感、黑白灰 + 单一强调色,无图标库、无 CDN,全部资源本地。
