// GET /story/:id  详情:渲染 synthesis_md(markdown)+ 来源列表。
import { marked } from "marked";
import { sql, layout, html, escape, timeago } from "../_shared.js";

// 模块级配置一次。安全:markdown 里的链接/图片只允许 http(s), 挡 javascript:/data: 等危险 scheme。
marked.setOptions({ breaks: false, gfm: true });
marked.use({
  walkTokens(t) {
    if ((t.type === "link" || t.type === "image") && !/^https?:\/\//i.test(t.href || "")) {
      t.href = "#";
    }
  },
});

function renderBody(md) {
  if (!md) return '<p class="dek">尚未生成综述。</p>';
  // synthesis_md 是 LLM 产出且公开渲染 → 先中和原始 HTML(< >),防 <script> 等注入;
  // marked 再从 markdown 语法生成安全 HTML(标题/段落/引用等)。
  const safe = md.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return marked.parse(safe);
}

function safeUrl(u) {
  return /^https?:\/\//i.test(u || "") ? u : "#";
}

export async function onRequest(context) {
  const id = parseInt(context.params.id, 10);
  if (!Number.isInteger(id)) return html(layout("未找到", "<p>无效的 id</p>"), 404);

  const db = sql(context.env);
  const rows = await db`SELECT story_id, title, category, category_label, synthesis_md,
                               synthesis_model, published_at, source_count, sources
                        FROM stories WHERE story_id = ${id} AND active LIMIT 1`;
  if (!rows.length) return html(layout("未找到", '<a class="back" href="/">← 返回头版</a><p>该新闻不存在或已下线。</p>'), 404);

  const s = rows[0];
  const label = s.category_label || (s.category || "").split("/").pop() || "综合";
  const meta = [s.source_count > 1 ? `${s.source_count} 个来源` : "", timeago(s.published_at),
    s.synthesis_model ? `综述 ${s.synthesis_model}` : ""].filter(Boolean).join(" · ");

  const bodyMd = renderBody(s.synthesis_md);

  const sources = Array.isArray(s.sources) ? s.sources : [];
  const srcHtml = sources.length
    ? `<div class="sources"><h3>来源</h3><ol>${sources.map((x) =>
        `<li>${escape(x.source_name || "")} · <a href="${escape(safeUrl(x.url))}" target="_blank" rel="noopener">${escape(x.title || x.url || "")}</a></li>`
      ).join("")}</ol></div>`
    : "";

  const body = `<a class="back" href="/">← 返回头版</a>
    <article class="read"><div class="kick">${escape(label)}</div>
    <h1>${escape(s.title)}</h1>
    <div class="meta">${escape(meta)}</div>
    <div class="body">${bodyMd}</div>
    ${srcHtml}</article>`;
  return html(layout(s.title || "新闻", body));
}
