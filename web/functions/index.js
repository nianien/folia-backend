// GET /  头版:列出 active 的 stories(可 ?cat=科技 按一级分类过滤)。
import { sql, layout, html, escape, timeago } from "./_shared.js";

export async function onRequest(context) {
  const db = sql(context.env);
  const url = new URL(context.request.url);
  const cat = url.searchParams.get("cat");

  const rows = cat
    ? await db`SELECT story_id, title, category, category_label, dek, published_at, source_count
               FROM stories WHERE active AND split_part(category,'/',1) = ${cat}
               ORDER BY published_at DESC NULLS LAST LIMIT 120`
    : await db`SELECT story_id, title, category, category_label, dek, published_at, source_count
               FROM stories WHERE active
               ORDER BY published_at DESC NULLS LAST LIMIT 120`;

  // 头部一级分类导航(按现有数据里出现的一级)
  const cats = await db`SELECT DISTINCT split_part(category,'/',1) AS top FROM stories WHERE active ORDER BY top`;
  const nav = ['<nav class="cats">',
    `<a href="/" class="${cat ? "" : "on"}">全部</a>`,
    ...cats.filter((c) => c.top).map((c) =>
      `<a href="/?cat=${encodeURIComponent(c.top)}" class="${cat === c.top ? "on" : ""}">${escape(c.top)}</a>`),
    "</nav>"].join("");

  const cards = rows.map((r) => {
    const label = r.category_label || (r.category || "").split("/").pop() || "综合";
    const meta = [r.source_count > 1 ? `${r.source_count} 个来源` : "", timeago(r.published_at)]
      .filter(Boolean).join(" · ");
    return `<a class="card" href="/story/${r.story_id}">
      <div class="kick">${escape(label)}</div>
      <h2>${escape(r.title)}</h2>
      ${r.dek ? `<p class="dek">${escape(r.dek)}</p>` : ""}
      <div class="meta">${escape(meta)}</div></a>`;
  }).join("");

  const body = `<header class="top"><h1>Folia 头版</h1>
    <span class="sub">${rows.length} 条 · 实时读取 Neon</span></header>
    ${nav}${cards || '<p class="dek">暂无新闻。</p>'}`;
  return html(layout("Folia 头版", body));
}
