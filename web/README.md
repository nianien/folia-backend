# Folia Web(Cloudflare Pages)

只读新闻前端:实时读 Neon 的 `stories` 表并渲染。用 **Cloudflare Pages Functions**
(SSR)+ Neon serverless 驱动,所以数据是活的(流水线每往 Neon 灌一轮,刷新页面就能看到)。

## 结构

```
web/
  package.json              @neondatabase/serverless + marked
  functions/
    _shared.js              Neon 连接 / HTML 骨架 / 工具(_ 开头 = 不路由)
    index.js                GET /            头版列表(?cat=科技 按一级分类过滤)
    story/[id].js           GET /story/:id   详情:synthesis_md + 来源
  public/                   静态输出目录(robots.txt)
```

## 部署(Cloudflare Pages 关联 GitHub,和现有方式一致)

1. Cloudflare 控制台 → Workers & Pages → Create → Pages → Connect to Git → 选本仓库。
2. 构建设置:
   - **Root directory(项目根)**:`web`
   - **Build command**:`npm install`
   - **Build output directory**:`public`
   - Framework preset:None
3. 环境变量(Settings → Environment variables)加密文:
   - `DATABASE_URL` = Neon 连接串(和后端 `settings.database.url` 同一个,建议用 Neon 的
     **pooled** 连接串,serverless 驱动最佳)。
4. 保存并部署。之后每次 push 到该分支,Pages 自动重建上线。

本地调试(可选,需 Node):

```bash
cd web && npm install
DATABASE_URL='postgresql://...' npx wrangler pages dev public
```

## 说明

- 页面实时查询 Neon,`cache-control: no-store`,方便随时检查新闻质量。
- 目前渲染:分类、标题、dek、正文(synthesis_md)、来源。`tags` 待后端把标签同步进
  `stories` 表后再显示(见后端 export/loader)。
