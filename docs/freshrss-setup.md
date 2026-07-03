# 基座层接线说明

`docker compose up -d` 后,一次性手动完成以下配置(状态持久化在 `freshrss_data` 卷里,只做一次)。

## 1. 创建 FreshRSS 账户

浏览器打开 http://localhost:8080,按引导完成安装:
- 数据库选 **SQLite**(零外部依赖)。
- 记下用户名,设登录密码。

## 2. 开启 Google Reader API

WebUI → 设置 → 身份验证:
- 勾选 **允许 API 访问**。
- 设置一个独立的 **API 密码**(不要复用登录密码)。

这个 API 密码就是 `.env` 里的 `FRESHRSS_API_PASSWORD`,用户名是 `FRESHRSS_USER`。

验证:
```bash
curl -s 'http://localhost:8080/api/greader.php/accounts/ClientLogin' \
  -d 'Email=<user>&Passwd=<api_password>'
# 期望输出含一行 Auth=<user>/<hash>
```

## 3. 接全文抽取(藏在 FreshRSS 后面)

目标:FreshRSS 拉回的内容是**全文**而非摘要,pipeline 直接读全文,自己不抓网页。

两条路任选其一:

**A. 内置「获取完整内容」(推荐,无需装扩展)**
每个 feed → 编辑 → 勾选「获取完整内容」(retrieve full content),它会用 feed 的文章 URL 经内置抽取取全文。需要更强抽取时走 B。

**B. Full-Text RSS 扩展指向容器**
安装一个全文扩展(如 `xExtension-Readable`),在扩展设置里把后端 URL 填为容器主机名:
```
http://fulltextrss/makefulltextfeed.php?url=
```
`fulltextrss` 是 compose 服务名,FreshRSS 与它同在 `frontpage_net`,无需公网。

验证 Full-Text RSS 本身可用(注意端点是 `makefulltextfeed.php`):
```bash
curl -s 'http://localhost:8081/makefulltextfeed.php?url=https://blog.python.org/&max=1' | head
```

## 4. 导入订阅 & tier/category(都在控制面板里)

订阅与 tier/category 映射都在**控制面板 `http://localhost:8000` → 数据源**页管理:

- 「导入默认订阅」一键把内置默认订阅(原 OPML,现存 db `feed_seed` 表)加进 FreshRSS,或手动填 URL 添加。
- 「tier / category 映射」按 stream_id(如 `feed/3`)或标题给来源打 `tier`/`category`(存 db `source_map` 表),供聚类与排序用;未匹配默认 `tier="unknown"` / `category="uncategorized"`。

RSSHub 造源的 URL 用容器内主机名 `http://rsshub:1200/...`。

## 6. Embedding(本机 Ollama,不在 compose 里)

```bash
ollama pull bge-m3
ollama serve   # 默认 http://localhost:11434
```
不可达时 pipeline 自动降级为 Jaccard 词重叠去重(质量略低,但不阻塞)。
