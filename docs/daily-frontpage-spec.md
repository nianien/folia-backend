# 「头版」(Frontpage) — 私人新闻 App
## 产品方案 + 技术落地 Spec v2(可直接交给 Claude Code 执行)

> v2.2 变更:改为「每日一期」模型——版面只含当天新闻,移除「有更新」角标,代之以跨日进展闸门(有实质进展才作为新条目进入当天版面,否则抑制)。
> 拉模式,不是推模式:后台管道持续爬取整理,用户随时打开浏览,像普通新闻 app,
> 接受滞后(小时级),换取干净、去重、按重要性排序的阅读体验。

---

# Status vs spec(实现进度对照)

| spec 区块 | 状态 | 说明 |
|-----------|------|------|
| 2.1/2.2 基座层(RSSHub+FreshRSS+Full-Text RSS) | ✅ 已落地 | `docker-compose.yml` + `config/freshrss/`,`docker compose up` 即起 |
| 2.3 M1 fetcher(FreshRSS API 拉未读) | ✅ | `freshrss_client.py`,Google Reader API |
| 2.3 M2 extractor(全文落库) | ✅ | 全文经 FreshRSS/Full-Text RSS 取回,`content_html` 落库 + `html_to_text` |
| 2.3 M3 当日聚类(bge-m3 余弦 0.82) | ✅ | `embeddings.py` + `dedupe.py`;离线降级 Jaccard |
| 单篇事实抽取 + 多源融合带引用 | ✅ | `facts.py` + `synthesizer.py` + `cluster_sources` 编号 |
| 2.3 M3 跨日进展闸门 | ❌ 未建 | 需 `prior_cluster_id` + LLM 判断 |
| 2.3 M4 scorer / M5 ranker / `layout` 表 | ❌ 未建 | 重要性排序、版面 |
| 2.4 应用层(FastAPI 三页面/PWA/已读) | ❌ 未建 | 当前仅 `viewer.py` 本地调试视图 |
| 2.x cron 调度 / MCP | ❌ 未建 | |

详见 `data-pipeline-technical-design.md`。

---

# Part 1 · 产品方案

## 1.1 一句话定义

一个只为一个人服务的新闻 App:后台管道从可信源持续拉取新闻,全文抽取、去重聚类、按"重要性而非兴趣"排序;前台是一个安卓可安装的 PWA,打开即浏览,点进即读全文,**不跳出、无广告、无视频、无无限流**。

## 1.2 与商业新闻 App 的差异(产品的全部理由)

| 维度 | 商业 App | 本产品 |
|------|---------|--------|
| 排序目标 | 停留时长 | 重要性(与兴趣解耦) |
| 同一事件 | 推 20 遍 | 聚类成 1 条,多源并列 |
| 信息流 | 无限下拉 | 有限列表,刷到底就是底 |
| 正文 | 跳转/广告/视频 | 站内净化全文(reader mode) |
| 时效 | 分钟级 | 小时级(刻意接受,换干净) |

## 1.3 非目标

- ❌ 不做多用户/商业化(版权、牌照、激励三重死路)
- ❌ 不做分钟级实时(突发交给 BBC Live / 澎湃)
- ❌ 不做评论/社交/收藏夹等周边功能(MVP 只有"读")
- ❌ 不绕过付费墙(付费源只展示 feed 摘要 + 原文链接)
- ❌ 不做原生安卓 App(PWA 加主屏即可,不值得上 Flutter——除非 V3 你想练手)

## 1.4 产品形态(交互已锁定,不再变更)

### 导航:顶部 tab 一排,仅此而已

```text
头版                         更新于 19:05
─────────────────────────────────────────
[要闻] 国际  财经  科技  中国  澳洲   ← 顶部 tab,要闻为默认
─────────────────────────────────────────
█ 首条大卡(仅要闻 tab)
   标题(衬线)
   一句话事实 / 口径分歧
   [7 源] Reuters · AP · Guardian +4
─────────────────────────────────────────
● 单行条目 ……………………………… 5 源
● 单行条目(续:附"此前"一行)…… 4 源
○ 单行条目(已读,置灰)…………… 3 源
─────────────────────────────────────────
        — 到底了 · 今日要闻共 12 条 —
```

### 交互规则全集

1. **tab 即全部导航**:要闻 + 分类(国际/财经/科技/中国/澳洲,settings.yaml 可配,建议 ≤6)。无底部导航、无侧边栏、无"我的"、无搜索、无收藏、无设置页(字号调节内嵌在阅读页)。
2. **要闻 tab = 编辑职能的全部落点**:跨分类、按重要性排序、与兴趣解耦,头版逻辑(首条大卡)和盲区逻辑(低关注领域高议程事件必须入列)都收在这里。**分类 tab = 纯分类**,不做任何反茧房动作。
3. **每个 tab 是有限列表**:要闻 ≤12 条、分类 ≤15 条,**展示窗口为当天**(每日一期,跨日不残留);列表到底显示"到底了 · 共 N 条"。
4. **点击链固定两层**:列表 → 事件页(本簇多源报道列表)→ 阅读页(站内净化全文)。
5. **已读置灰但不消失**(当天版面是稳定的,不是流)。
6. **每天的新闻都是新的**:昨日已报道的事件,只有出现实质进展才会作为新条目进入今天的版面(附一行"此前:…"做上下文);无进展的跟进稿/综述稿被跨日闸门抑制,不再出现。
7. 下拉刷新 = 读取管道最新结果,不触发实时爬取。

### 事件页

```text
事件标题
一句话事实(≤40 字)
口径分歧(仅多源不一致时显示,标注各源)
─────────────────
▸ 报道列表(按来源层级排序)
   AP · 标题 ……          [站内阅读]
   Guardian · 标题 ……     [站内阅读]
   财新 · 标题 ……         [摘要+原文链接](付费墙源)
```

### 阅读页

- trafilatura 净化正文,自有排版(衬线、合理行距、深色模式跟随系统)。
- 顶部固定:来源 + 原文链接(永远可跳源站,默认不跳)。
- 抽取失败降级为摘要+链接,不报错。

### 节奏

- 管道每小时一次(07:00–23:00),夜间停;当天版面随管道运行逐步充实,次日 00:00 翻篇。
- 各 tab 排序每次管道运行后重算;任意时刻版面有限。

## 1.5 信源策略(sources.yaml 驱动)

| 层 tier | 源 | 接入 | 角色 |
|----|----|---------|------|
| agenda | Google News Top Stories | 官方 RSS 端点 | 头版候选,反茧房锚点 |
| wire | AP News, Reuters | 官方 RSS | 头版+要闻基准线 |
| broadsheet | Guardian (AU edition), BBC World | 官方 RSS | 要闻+澳洲议程 |
| cn | 财新快讯、澎湃、晚点 LatePost | RSSHub | 中文侧 |
| interest | Hacker News, TechCrunch, CNBC | 官方 RSS | "你的版面" |

规则:头版只能由 agenda/wire/broadsheet 层产生;interest 层永远进不了头版。

---

# Part 2 · 技术方案

## 2.1 架构总览

```text
┌─────────────────────────────────────────────────┐
│ 基座层(现成开源,零开发)                          │
│  RSSHub ──造源──┐                                │
│  官方 RSS ──────┴──► FreshRSS(订阅管理/拉取调度) │
└──────────────────┬──────────────────────────────┘
                   │ Google Reader API
┌──────────────────▼──────────────────────────────┐
│ 编辑层 pipeline(cron 每小时,~500 行 Python)      │
│  fetcher → extractor → dedupe → scorer → ranker │
│  全部结果落 SQLite                                │
└──────────────────┬──────────────────────────────┘
                   │ 同一个 SQLite 文件
┌──────────────────▼──────────────────────────────┐
│ 应用层 app(本次新增,FastAPI 单服务)              │
│  REST API + 服务端渲染页面(PWA)                  │
│  /        首页四区                                │
│  /event/{cluster_id}   事件页                     │
│  /read/{article_id}    站内阅读页                 │
└─────────────────────────────────────────────────┘
   部署:docker-compose 四容器(freshrss/rsshub/pipeline/app)
   访问:局域网直连;外网用 Tailscale,不暴露公网
```

## 2.2 技术选型与理由

| 组件 | 选型 | 理由 |
|------|------|------|
| 订阅基座 | FreshRSS (Docker) | Google Reader API、拉取调度现成 |
| 中文造源 | RSSHub (Docker) | 公众号(晚点)路由现成 |
| 全文抽取 | trafilatura | 抽取质量目前最好,直接进管道 |
| 后端+前端 | FastAPI + Jinja2 + HTMX | 单服务、服务端渲染、无前端构建链;HTMX 足够做下拉刷新和局部更新 |
| PWA | manifest.json + Service Worker(仅缓存壳) | 安卓"添加到主屏"获得全屏原生感;离线读已抽取正文可作 V2 |
| 存储 | SQLite(WAL 模式) | pipeline 写 / app 读,单机单用户,WAL 解决并发;不用 Neon,零云依赖 |
| Embedding | bge-m3 via Ollama(本地) | 中英混合去重,免费 |
| 打分 LLM | claude-haiku-4-5(API) | 每小时一批,约 $2-4/月;失败降级 gemma 12B 本地 |
| 调度 | cron | 不引入任务队列 |
| 远程访问 | Tailscale | 出门也能看,不开公网端口 |

**刻意不用:** React/Vue(无构建必要)、向量数据库(48h 窗口内暴力余弦即可)、LangChain、消息队列、Flutter(V3 再说)。

## 2.3 管道详设(与 v1 相同的部分从简)

### M1 fetcher
FreshRSS API 拉未读 → 写 `articles` 表 → 标已读。每小时增量。

### M2 extractor
摘要 <500 字符且非付费墙域名 → trafilatura 抓全文,**全文落库**(阅读页直接读库,打开零延迟)。付费墙白名单(caixin.com/bloomberg.com/ft.com…)跳过。失败打标降级,不阻塞。

### M3 dedupe(当日聚类 + 跨日进展闸门)
**当日内:**`title+前300字` → bge-m3 embedding → 与当天已有簇质心余弦 >0.82 归簇,否则新建。代表文章:wire > broadsheet > 其他,同级取最早。guid 相同但内容 hash 变化的原地改稿,重抽取后自然落回原簇。

**跨日:**新建簇与**过去 7 天历史簇质心**做相似度匹配(同阈值):
1. 无历史匹配 → 新事件,正常进入当天流程。
2. 有匹配 → **进展闸门**(LLM 判断,与 scorer 合并批量调用):"该簇是否包含历史 one_liner 之外的实质新事实(数字变化、新表态、事态转折)?"
   - 是 → 作为**新条目**进入当天,簇记录 `prior_cluster_id`,前端附一行"此前:{历史 one_liner}"
   - 否 → 簇标 `suppressed`,当天版面不出现(迟到跟进稿、评论稿、综述稿都走这条路)
   - **判断不确定时默认放行**——抑制错误会永久错过新闻,放行错误只是多看一条,代价不对称。

### M4 scorer
批量 LLM call,对当批新建/更新的簇打分,JSON 输出:
- `impact` 0-10:影响人群规模与持续时间
- `agenda` 0-10:多源报道密度
- `novelty` 0-10:实质新进展程度
- `blindspot` bool:用户低关注领域 × agenda 高
- `category`:固定枚举之一(international/finance/tech/china/au),sources.yaml 源标签作先验,LLM 终判
- `one_liner`:一句话事实(≤40 字)
- `divergence`:口径分歧描述(无则 null)

Prompt 核心句(不可删):"重要性的定义与用户兴趣无关。优先选择影响广泛但用户可能不感兴趣的事件。你是编辑,不是推荐系统。"

### M5 ranker
计算各 tab 版面,写 `layout` 表(带时间戳,历史版面可回溯):
- **要闻 tab**:重要性分 = impact×0.5 + agenda×0.5(无兴趣项),取 top12;仅 agenda/wire/broadsheet 层;blindspot==true 的簇保底入列(≥1 条,若存在);首条标记为大卡。
- **分类 tab**:按 category 分组,组内按总分排序,各取 top15;所有层的源均可进入。
- 同一簇可同时出现在要闻和其所属分类 tab(这是正常的,报纸头版新闻也会出现在对应版面)。

## 2.4 应用层 API 与页面

```text
GET /?tab=headlines      首页(SSR,读最新 layout;tab 切换为同页参数,HTMX 局部替换列表)
GET /event/{cluster_id}  事件页(簇详情+报道列表)
GET /read/{article_id}   阅读页(净化正文)
POST /api/seen/{cluster_id}   标记已读(HTMX 触发)
GET /api/layout          JSON 版面(给 V2 的 Butler/MCP 用)
GET /healthz             管道最近运行时间,>3h 未跑则首页顶部黄条警告
```

阅读体验要求(验收标准的一部分):
- 首页 → 阅读页 ≤2 次点击;阅读页打开 <300ms(正文已在库)
- 深色模式跟随系统;字号可调(localStorage)
- 全站无任何第三方资源加载(字体本地化)——这是"无广告"的技术兜底

## 2.5 数据库 Schema(SQLite,WAL)

```sql
CREATE TABLE articles (
  id TEXT PRIMARY KEY, cluster_id INTEGER,
  source TEXT, source_tier TEXT,
  title TEXT, url TEXT,
  published_at TEXT, fetched_at TEXT,
  summary TEXT, fulltext TEXT,        -- 全文落库
  extract_status TEXT                 -- ok/failed/paywalled
);
CREATE TABLE clusters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  centroid BLOB, representative_id TEXT,
  source_count INTEGER,
  created_at TEXT,
  prior_cluster_id INTEGER,           -- 跨日续报指向的历史簇
  status TEXT,                        -- active/suppressed
  scores TEXT,                        -- JSON: impact/agenda/novelty/blindspot
  category TEXT,                      -- international/finance/tech/china/au
  one_liner TEXT, divergence TEXT,
  seen_at TEXT                        -- 用户已读时间,NULL=未读
);
CREATE TABLE layout (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  computed_at TEXT,
  tab TEXT,                           -- headlines/international/finance/tech/china/au
  cluster_ids TEXT                    -- JSON array,有序,首元素为大卡(仅要闻)
);
```

## 2.6 项目结构

```text
frontpage/
├── docker-compose.yml            # freshrss/rsshub/pipeline/app
├── config/
│   ├── sources.yaml
│   └── settings.yaml             # 阈值/上限/窗口/付费墙白名单/模型
├── pipeline/
│   ├── main.py                   # M1→M5 串联,cron 入口
│   ├── fetcher.py / extractor.py / dedupe.py / scorer.py / ranker.py
│   └── db.py
├── app/
│   ├── main.py                   # FastAPI
│   ├── templates/                # Jinja2: index/event/read/base
│   └── static/                   # css、本地字体、manifest.json、sw.js
├── prompts/scorer.txt
├── tests/fixtures/               # 录制的真实 feed 样本
├── .env.example
└── README.md
```

---

# Part 3 · 执行计划

> 顺序执行,每个里程碑跑通验收再继续。预估:一个周末 + 一周晚间调优。

## M0 基座(~1h)
- [ ] compose 拉起 FreshRSS+RSSHub,导入 sources.yaml,开 Reader API
- **验收:** WebUI 全源正常更新,curl 能拉未读

## M1 管道:fetcher+extractor+schema
- **验收:** 跑一次入库 ≥50 条;非付费墙源全文抽取成功率 ≥80%;重复运行无重复入库

## M2 去重聚类
- **验收:** 热点日真实数据,同事件归簇 ≥90%,误合并 <5%(人工抽查 20 簇);阈值入 settings.yaml

## M3 打分+排版(管道完成线)
- [ ] scorer/ranker,prompt 先出初稿 review
- **验收:** layout 表产出全部 tab 版面;要闻 top3 与当日 Google News Top Stories 重合 ≥2/3;分类抽查 20 簇分错 ≤2;cron 每小时稳定运行 24h 无崩溃

## M4 应用层(产品完成线)
- [ ] FastAPI 三页面 + PWA manifest + 已读状态 + 健康警告条
- **验收:** 安卓 Chrome 加主屏,全屏打开;tab 切换无整页刷新;首页到读完一篇全文不离开 app 且点击链 ≤2 层;阅读页 <300ms;断网时已加载页面不白屏

## M5 调优(1-2 周真实使用)
- [ ] 盲区保底逻辑校准(基于 seen_at 统计低频领域,驱动 scorer 的 blindspot 判定)
- [ ] 跨日进展闸门校准(抑制率统计:被抑制簇占比应在 20-50% 区间,过低说明闸门失效,过高说明误杀)、分歧点质量
- [ ] API 失败 → gemma 本地降级链路
- **验收(主观):** 连续一周,它成为你打开的第一个新闻入口

## M6(V2,可选)
- [ ] /api/layout 接 MCP → Private Butler "编辑" role(对话式追问某事件)
- [ ] Service Worker 离线正文缓存(地铁场景)
- [ ] 周回顾页;开源准备(MIT,剥离个人配置)

---

# Part 4 · 风险与边界

| 风险 | 应对 |
|------|------|
| 版权 | 全文净化展示仅限个人自部署使用;永远展示来源+原文链接;不公开部署、不分发内容;开源只开代码 |
| 付费墙源 | 只展示摘要+链接,接受信息薄,这是边界不是缺陷 |
| SQLite 并发 | WAL 模式 + pipeline 单写者,app 只读,不会锁冲突 |
| feed 失效 | 各源记录最近成功时间,>48h 失效的源在首页底部灰字提示 |
| 管道挂掉 | /healthz 驱动首页黄条;新闻 app 最差的体验是"看起来正常但内容是旧的",必须显式暴露 |
| 合规(国内) | 个人自用、不公开服务、Tailscale 内网访问,不碰牌照红线 |

---

# Part 5 · 给 Claude Code 的执行指令

```text
你将实现上述 spec。规则:
1. 严格按 M0→M4 顺序,每个里程碑跑通验收标准后再继续。
2. 技术选型不得替换。明确禁止引入:React/Vue/前端构建工具、LangChain、
   向量数据库、消息队列、ORM(直接用 sqlite3/SQL)。
   本项目刻意保持 pipeline ~500 行 + app ~400 行量级。
3. prompts/scorer.txt 先写初稿给我 review,确认后再接入。
4. 所有阈值/上限/窗口/白名单写 config/settings.yaml,代码不许硬编码。
5. 页面样式:衬线正文、报纸感、黑白灰+单一强调色,无图标库,无 CDN,
   全部资源本地。先出首页静态原型给我看视觉方向,再接数据。
6. 测试用 tests/fixtures 离线样本,不依赖网络。
7. 遇到 spec 未覆盖的决策点,停下来问,不要自行发挥。
```
