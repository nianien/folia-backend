// 共享工具:Neon 连接、HTML 骨架、转义、卡片。文件名以 _ 开头 → 不作为路由(CF 约定)。
import { neon } from "@neondatabase/serverless";

export function sql(env) {
  if (!env.DATABASE_URL) throw new Error("DATABASE_URL 未配置");
  return neon(env.DATABASE_URL);
}

export function escape(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

const CSS = `
:root{--bg:#faf9f6;--ink:#1a1a1a;--muted:#6b6b6b;--line:#e6e3dc;--cat:#2a9d8f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Noto Serif SC",Georgia,serif;line-height:1.7}
a{color:inherit;text-decoration:none}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 80px}
header.top{display:flex;align-items:baseline;gap:14px;border-bottom:2px solid var(--ink);padding-bottom:12px;margin-bottom:8px}
header.top h1{font-size:30px;margin:0;letter-spacing:1px}
header.top .sub{color:var(--muted);font-size:13px}
nav.cats{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 24px}
nav.cats a{font-size:14px;padding:4px 12px;border:1px solid var(--line);border-radius:999px;color:var(--muted)}
nav.cats a.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.card{display:block;padding:18px 0;border-bottom:1px solid var(--line)}
.card .kick{font-size:12px;font-weight:700;letter-spacing:1px;color:var(--cat)}
.card h2{font-size:21px;margin:6px 0 6px}
.card .dek{color:var(--muted);font-size:15px;margin:0}
.card .meta{color:var(--muted);font-size:12px;margin-top:8px}
article.read{max-width:720px;margin:0 auto}
article.read .kick{font-size:13px;font-weight:700;letter-spacing:1px;color:var(--cat)}
article.read h1{font-size:32px;line-height:1.3;margin:8px 0 10px}
article.read .meta{color:var(--muted);font-size:13px;margin-bottom:22px}
.body{font-size:17px}
.body h1,.body h2{font-family:inherit}
.body h2{font-size:20px;margin:26px 0 10px;border-left:3px solid var(--cat);padding-left:10px}
.tags{margin:22px 0}
.tags span{display:inline-block;padding:2px 11px;margin:0 6px 6px 0;border:1px solid var(--cat);
  border-radius:999px;font-size:13px;color:var(--cat);opacity:.85}
.sources{margin-top:28px;border-top:1px solid var(--line);padding-top:14px;font-size:14px}
.sources h3{font-size:14px;color:var(--muted);margin:0 0 8px}
.sources li{margin:4px 0;color:var(--muted)}
.back{display:inline-block;margin-bottom:18px;color:var(--muted);font-size:14px}
`;

export function layout(title, bodyHtml) {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600;900&display=swap" rel="stylesheet">
<style>${CSS}</style></head><body><div class="wrap">${bodyHtml}</div></body></html>`;
}

export function html(body, status = 200) {
  return new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

export function timeago(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return "";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 3600) return `${Math.max(1, Math.floor(diff / 60))} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return d.toISOString().slice(0, 10);
}
