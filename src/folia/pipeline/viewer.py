from __future__ import annotations

import html
import re
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


URL_RE = re.compile(r"https?://[^\s<>()\"']+")
IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
CITE_RE = re.compile(r"\[(\d+)\]")
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

# 两级分类由 db 的 directory 表驱动(每次请求刷新): 一级顺序 + 各一级的二级 + 一级颜色。
# article.category 存 "一级/二级" 路径; 颜色统一取一级的。
_DIR_COLORS: dict[str, str] = {}   # 一级名 → 颜色
_DIR_TOPS: list[str] = []          # 一级顺序
_DIR_SUBS: dict[str, list[str]] = {}  # 一级 → [二级...]
_DEFAULT_COLOR = "#7a6f5c"


def _load_directories(conn: sqlite3.Connection) -> None:
    global _DIR_COLORS, _DIR_TOPS, _DIR_SUBS
    rows = list(conn.execute("SELECT name, parent, color FROM directory ORDER BY sort_order, name"))
    _DIR_TOPS = [r[0] for r in rows if not r[1]]
    _DIR_COLORS = {r[0]: (r[2] or _DEFAULT_COLOR) for r in rows if not r[1]}
    _DIR_SUBS = {}
    for name, parent, _color in rows:
        if parent:
            _DIR_SUBS.setdefault(parent, []).append(name)

FONTS = (
    "https://fonts.googleapis.com/css2?"
    "family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,900"
    "&family=Newsreader:opsz,wght@6..72,400;6..72,500"
    "&family=Archivo:wght@500;600;700;800"
    "&family=Noto+Serif+SC:wght@400;600;900"
    "&family=Noto+Sans+SC:wght@400;500;700"
    "&display=swap"
)


def serve(database: Path, host: str = "127.0.0.1", port: int = 8000) -> None:
    handler = create_handler(database)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"serving {database} at http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


def create_handler(database: Path) -> type[BaseHTTPRequestHandler]:
    class ViewerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, body = route_request(database, self.path)
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}")

    return ViewerHandler


def route_request(database: Path, path: str) -> tuple[int, str]:
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query)
    category = query.get("cat", [None])[0]
    segments = [segment for segment in parsed.path.split("/") if segment]
    conn = connect_viewer(database)
    try:
        _load_directories(conn)  # 目录表驱动分类颜色与 tab
        if not segments:
            return int(HTTPStatus.OK), render_dashboard(conn, category)
        if len(segments) == 2 and segments[0] == "cluster":
            return render_cluster_response(conn, segments[1])
        if len(segments) == 2 and segments[0] == "article":
            return render_article_response(conn, segments[1])
        return int(HTTPStatus.NOT_FOUND), shell("未找到", '<p class="empty">页面不存在。</p>')
    finally:
        conn.close()


def connect_viewer(database: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------- dashboard

def render_dashboard(conn: sqlite3.Connection, category: str | None) -> str:
    stats = fetch_stats(conn)
    image_map = build_image_map(conn)
    stories = collect_stories(conn, image_map)

    stats_line = f"{stats['clusters']} 条要闻 · {stats['articles']} 篇来稿 · {stats['sources']} 个信源"
    head = masthead(category, stats_line)

    if category is not None:
        label, color = cat_meta(category)
        if "/" in category:  # 具体二级: 精确匹配路径
            scoped = [s for s in stories if s["cat"] == category]
        else:  # 一级: 匹配该一级下所有二级
            scoped = [s for s in stories if s["cat"].split("/")[0] == category]
        if not scoped:
            return shell("头版", head + '<p class="empty">该版块暂无内容。</p>')
        cards = "".join(story_card(s, i, "standard") for i, s in enumerate(scoped))
        body = (
            f'{head}{section_head(label, color)}<section class="stream">{cards}</section>'
        )
        return shell(f"头版 · {label}", body)

    if not stories:
        return shell("头版", head + '<p class="empty">尚无综述内容，先运行 run-once。</p>')

    used: set[int] = set()
    hero = next((s for s in stories if s["image"]), stories[0])
    used.add(hero["id"])
    featured = [s for s in stories if s["id"] not in used and s["image"]][:3]
    for story in featured:
        used.add(story["id"])

    parts = [head, hero_block(hero)]
    if featured:
        cards = "".join(story_card(s, i, "standard") for i, s in enumerate(featured))
        parts.append(f'<section class="featured">{cards}</section>')

    groups: dict[str, list[dict]] = {}
    for story in stories:
        if story["id"] in used:
            continue
        top = (story["cat"] or "综合").split("/")[0]  # 首页按一级分组
        groups.setdefault(top, []).append(story)
    ordered = [c for c in _DIR_TOPS if c in groups] + [
        c for c in groups if c not in _DIR_TOPS
    ]
    for cat in ordered:
        label, color = cat_meta(cat)
        cards = "".join(story_card(s, i, "standard") for i, s in enumerate(groups[cat]))
        parts.append(section_head(label, color))
        parts.append(f'<section class="stream">{cards}</section>')

    return shell("头版 · 每日要闻", "".join(parts))


def collect_stories(conn: sqlite3.Connection, image_map: dict[int, str]) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id, c.title, c.source_count, c.synthesized_text, c.updated_at,
               a.category AS category, a.source_tier AS tier,
               a.source_name AS source_name, a.published_at AS published_at
        FROM clusters c
        LEFT JOIN articles a ON a.id = c.representative_article_id
        WHERE c.synthesis_status = 'ok'
        """
    ).fetchall()
    stories = []
    for row in rows:
        title = row["title"] or ""
        synth = row["synthesized_text"] or ""
        stories.append(
            {
                "id": row["id"],
                "title": title,
                "cat": row["category"] or "综合",
                "tier": row["tier"] or "",
                "source_name": row["source_name"] or "",
                "source_count": row["source_count"] or 1,
                "published_at": row["published_at"] or row["updated_at"] or "",
                "dek": dek_from_synthesis(synth),
                "image": image_map.get(row["id"]),
            }
        )
    stories.sort(key=lambda s: s["published_at"], reverse=True)
    return stories


def hero_block(story: dict) -> str:
    label, color = cat_meta(story["cat"])
    media = card_media(story["image"])
    meta = source_line(story)
    return f"""
    <a class="hero" href="/cluster/{story['id']}" style="--cat:{color}">
      {media}
      <div class="card-body">
        <span class="kicker">{escape(label)}</span>
        <h2 class="card-title">{escape(story['title'])}</h2>
        <p class="card-dek">{escape(dek_from_synthesis_long(story))}</p>
        <div class="card-meta">{meta}</div>
      </div>
    </a>
    """


def story_card(story: dict, index: int, variant: str) -> str:
    label, color = cat_meta(story["cat"])
    media = card_media(story["image"])
    delay = min(index, 12) * 45
    return f"""
    <a class="card {variant}" href="/cluster/{story['id']}" style="--cat:{color};animation-delay:{delay}ms">
      {media}
      <span class="kicker">{escape(label)}</span>
      <h3 class="card-title">{escape(story['title'])}</h3>
      <p class="card-dek">{escape(story['dek'])}</p>
      <div class="card-meta">{source_line(story)}</div>
    </a>
    """


def card_media(image: str | None) -> str:
    if not image:
        return ""
    src = escape_attr(image)
    return (
        '<div class="card-media">'
        f'<img src="{src}" alt="" loading="lazy" referrerpolicy="no-referrer" '
        "onerror=\"this.closest('.card-media').remove()\"></div>"
    )


def source_line(story: dict) -> str:
    if story["source_count"] > 1:
        label = f"{story['source_count']} 个来源"
    else:
        label = story["source_name"]
    when = humanize(story["published_at"])
    pieces = [f"<span>{escape(label)}</span>"]
    if when:
        pieces.append('<span class="dot">·</span>')
        pieces.append(f"<span>{escape(when)}</span>")
    return "".join(pieces)


def section_head(label: str, color: str) -> str:
    return (
        f'<div class="section-head" style="--cat:{color}">'
        f'<span class="bar"></span><h2>{escape(label)}</h2></div>'
    )


def masthead(active: str | None, stats_line: str) -> str:
    active_top = active.split("/")[0] if active else None
    items = [("/", "全部", None)]
    for name in _DIR_TOPS:  # 一级 tab
        items.append((f"/?cat={urllib.parse.quote(name)}", name, name))
    nav = "".join(  # 一级高亮: 看该一级或其任一二级时都亮
        f'<a href="{href}" class="{"active" if active_top == key else ""}">{escape(label)}</a>'
        for href, label, key in items
    )
    subnav = ""
    if active_top and _DIR_SUBS.get(active_top):  # 二级子栏
        subitems = [(f"/?cat={urllib.parse.quote(active_top)}", "全部", active_top)]
        for sub in _DIR_SUBS[active_top]:
            path = f"{active_top}/{sub}"
            subitems.append((f"/?cat={urllib.parse.quote(path)}", sub, path))
        links = "".join(
            f'<a href="{href}" class="{"active" if active == key else ""}">{escape(label)}</a>'
            for href, label, key in subitems
        )
        subnav = f'<nav class="topnav subnav">{links}</nav>'
    return f"""
    <header class="masthead">
      <div class="edition">{escape(date_line())} · 私人头版</div>
      <div class="wordmark">头版</div>
      <div class="tagline">Frontpage Daily Briefing</div>
      <hr class="rule-d">
      <nav class="topnav">{nav}</nav>{subnav}
      <div class="stats">{escape(stats_line)}</div>
    </header>
    """


# ---------------------------------------------------------------- detail

def render_cluster_response(conn: sqlite3.Connection, raw_id: str) -> tuple[int, str]:
    try:
        cluster_id = int(raw_id)
    except ValueError:
        return int(HTTPStatus.BAD_REQUEST), shell("无效请求", '<p class="empty">聚类 ID 必须是整数。</p>')
    body = render_cluster_detail(conn, cluster_id)
    if body is None:
        return int(HTTPStatus.NOT_FOUND), shell("未找到", '<p class="empty">聚类不存在。</p>')
    return int(HTTPStatus.OK), body


def render_article_response(conn: sqlite3.Connection, article_id: str) -> tuple[int, str]:
    body = render_article_detail(conn, article_id)
    if body is None:
        return int(HTTPStatus.NOT_FOUND), shell("未找到", '<p class="empty">文章不存在。</p>')
    return int(HTTPStatus.OK), body


def render_cluster_detail(conn: sqlite3.Connection, cluster_id: int) -> str | None:
    cluster = conn.execute("SELECT * FROM clusters WHERE id=?", (cluster_id,)).fetchone()
    if cluster is None:
        return None
    rep = conn.execute(
        "SELECT category, source_name, published_at FROM articles WHERE id=?",
        (cluster["representative_article_id"],),
    ).fetchone()
    category = (rep["category"] if rep else None) or "uncategorized"
    label, color = cat_meta(category)
    image = cluster_image(conn, cluster_id)
    published = (rep["published_at"] if rep else None) or cluster["updated_at"]

    keys = cluster.keys()
    zh = cluster["synthesis_zh"] if "synthesis_zh" in keys else None
    en = cluster["synthesis_en"] if "synthesis_en" in keys else None
    if zh and en:  # 双语: 中/EN 切换
        body_html = (
            "<style>.langbar{display:flex;gap:6px;margin-bottom:12px}"
            ".langbtn{padding:3px 12px;border:1px solid #ccc;background:#fff;border-radius:6px;cursor:pointer}"
            ".langbtn.on{background:#333;color:#fff;border-color:#333}</style>"
            '<div class="langbar">'
            '<button type="button" class="langbtn on" data-lang="zh" onclick="foliaLang(\'zh\')">中文</button>'
            '<button type="button" class="langbtn" data-lang="en" onclick="foliaLang(\'en\')">EN</button>'
            "</div>"
            f'<div class="langbody" data-lang="zh">{render_synthesis(zh)}</div>'
            f'<div class="langbody" data-lang="en" style="display:none">{render_synthesis(en)}</div>'
            "<script>function foliaLang(l){"
            "document.querySelectorAll('.langbody').forEach(function(b){b.style.display=(b.dataset.lang===l)?'':'none';});"
            "document.querySelectorAll('.langbtn').forEach(function(x){x.classList.toggle('on',x.dataset.lang===l);});"
            "}</script>"
        )
    else:
        body_html = render_synthesis(cluster["synthesized_text"] or "") or (
            '<p class="empty">尚未生成综述。</p>'
        )
    lead = lead_media(image)
    if cluster["source_count"] and cluster["source_count"] > 1:
        source_label = f"{cluster['source_count']} 个来源"
    else:
        source_label = rep["source_name"] if rep else ""

    meta = meta_line([source_label, humanize(published), f"综述 {cluster['synthesis_model'] or '—'}"])
    inner = (
        '<a class="backlink" href="/">← 返回头版</a>'
        f'<article class="read" style="--cat:{color}">'
        f'<span class="kicker">{escape(label)}</span>'
        f'<h1>{escape(cluster["title"] or "未命名聚类")}</h1>'
        f"{meta}{lead}"
        f'<div class="article-body">{body_html}</div>'
        f"{render_sources(conn, cluster_id)}"
        "</article>"
    )
    return shell(cluster["title"] or "聚类", inner)


def render_article_detail(conn: sqlite3.Connection, article_id: str) -> str | None:
    article = conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
    if article is None:
        return None
    label, color = cat_meta(article["category"] or "uncategorized")
    lead = lead_media(first_image(article["content_html"]))
    text = article["extracted_text"] or ""
    paragraphs = "".join(
        f"<p>{escape(block.strip())}</p>" for block in re.split(r"\n{2,}", text) if block.strip()
    ) or '<p class="empty">无正文。</p>'
    facts = article["article_facts"] or ""
    facts_block = (
        f'<details class="facts"><summary>facts JSON</summary><pre>{escape(facts)}</pre></details>'
        if facts
        else ""
    )
    meta = meta_line([article["source_name"], humanize(article["published_at"]), external_link(article["url"])])
    inner = (
        f'<a class="backlink" href="/cluster/{article["cluster_id"]}">← 返回聚合</a>'
        f'<article class="read" style="--cat:{color}">'
        f'<span class="kicker">{escape(label)}</span>'
        f'<h1>{escape(article["title"])}</h1>'
        f"{meta}{lead}"
        f'<div class="article-body">{paragraphs}</div>{facts_block}'
        "</article>"
    )
    return shell(article["title"], inner)


def render_sources(conn: sqlite3.Connection, cluster_id: int) -> str:
    rows = conn.execute(
        "SELECT source_no, source_name, title, url FROM cluster_sources WHERE cluster_id=? ORDER BY source_no",
        (cluster_id,),
    ).fetchall()
    if not rows:
        return ""
    items = []
    for row in rows:
        items.append(
            '<li class="src">'
            f'<span class="src-no">{row["source_no"]}</span>'
            "<div>"
            f'<div class="src-name">{escape(row["source_name"])}</div>'
            f'<a class="src-title" href="{escape_attr(row["url"])}" target="_blank" rel="noreferrer">{escape(row["title"])}</a>'
            "</div></li>"
        )
    return (
        '<section class="sources"><div class="sec"><span>来源</span></div>'
        f'<ol class="src-list">{"".join(items)}</ol></section>'
    )


def render_synthesis(markdown: str) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(f"<p>{' '.join(paragraph)}</p>")
            paragraph.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading.lower().startswith("source") or heading.startswith("来源"):
                break
            flush()
            blocks.append(f'<div class="sec"><span>{escape(heading)}</span></div>')
            continue
        if line.startswith("### "):
            flush()
            blocks.append(f"<h3>{escape(line[4:])}</h3>")
            continue
        if line == "---":
            flush()
            continue
        paragraph.append(format_inline(line))
    flush()
    return "\n".join(blocks)


def format_inline(line: str) -> str:
    escaped = escape(line.strip())
    return CITE_RE.sub(lambda m: f'<sup class="cite">{m.group(1)}</sup>', escaped)


def lead_media(image: str | None) -> str:
    if not image:
        return ""
    return (
        '<div class="lead-media">'
        f'<img src="{escape_attr(image)}" alt="" referrerpolicy="no-referrer" '
        "onerror=\"this.closest('.lead-media').remove()\"></div>"
    )


def meta_line(parts: list[str]) -> str:
    cells = [p for p in parts if p]
    joined = '<span class="dot">·</span>'.join(f"<span>{c}</span>" for c in cells)
    return f'<div class="meta">{joined}</div>'


# ---------------------------------------------------------------- data helpers

def fetch_stats(conn: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "articles": "SELECT COUNT(*) FROM articles",
        "clusters": "SELECT COUNT(*) FROM clusters WHERE synthesis_status='ok'",
        "sources": "SELECT COUNT(*) FROM sources",
    }
    return {name: int(conn.execute(query).fetchone()[0]) for name, query in queries.items()}


def build_image_map(conn: sqlite3.Connection) -> dict[int, str]:
    mapping: dict[int, str] = {}
    rows = conn.execute(
        "SELECT cluster_id, content_html FROM articles "
        "WHERE cluster_id IS NOT NULL AND content_html LIKE '%<img%'"
    )
    for row in rows:
        cid = row["cluster_id"]
        if cid in mapping:
            continue
        image = first_image(row["content_html"])
        if image:
            mapping[cid] = image
    return mapping


def cluster_image(conn: sqlite3.Connection, cluster_id: int) -> str | None:
    rows = conn.execute(
        "SELECT content_html FROM articles WHERE cluster_id=? AND content_html LIKE '%<img%'",
        (cluster_id,),
    )
    for row in rows:
        image = first_image(row["content_html"])
        if image:
            return image
    return None


def first_image(content_html: str | None) -> str | None:
    if not content_html:
        return None
    match = IMG_RE.search(content_html)
    if not match:
        return None
    src = html.unescape(match.group(1)).strip()
    if src.startswith("//"):
        src = "https:" + src
    if not src.startswith("http"):
        return None
    return src


def dek_from_synthesis(markdown: str, limit: int = 105) -> str:
    return _first_paragraph(markdown, limit)


def dek_from_synthesis_long(story: dict, limit: int = 190) -> str:
    return story["dek"] if len(story["dek"]) >= limit - 20 else story["dek"]


def _first_paragraph(markdown: str, limit: int) -> str:
    if not markdown:
        return ""
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line == "---":
            continue
        line = CITE_RE.sub("", line)
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 12:
            continue
        if len(line) > limit:
            line = line[:limit].rstrip() + "…"
        return line
    return ""


def humanize(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return value[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    seconds = max((now - dt).total_seconds(), 0)
    minutes = seconds / 60
    hours = minutes / 60
    days = hours / 24
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{int(minutes)} 分钟前"
    if hours < 24:
        return f"{int(hours)} 小时前"
    if days < 30:
        return f"{int(days)} 天前"
    return dt.strftime("%Y-%m-%d")


def date_line() -> str:
    now = datetime.now()
    return f"{now.year}年{now.month}月{now.day}日 · 星期{WEEKDAYS[now.weekday()]}"


def cat_meta(category: str | None) -> tuple[str, str]:
    """(标签, 颜色) —— category 可能是 "一级/二级" 路径或纯一级; 标签取叶子, 颜色取一级。"""
    name = category or "综合"
    top, _, _sub = name.partition("/")
    label = name.rsplit("/", 1)[-1]
    return label, _DIR_COLORS.get(top, _DEFAULT_COLOR)


# ---------------------------------------------------------------- shell

def shell(title: str, inner: str) -> str:
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} · 头版</title>"
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link href="{FONTS}" rel="stylesheet">'
        f"<style>{CSS}</style></head><body>"
        '<a href="/admin" title="设置" style="position:fixed;top:16px;right:20px;z-index:50;'
        "text-decoration:none;font-size:13px;color:#8a7d68;background:rgba(255,250,240,.92);"
        'border:1px solid #d8cdb8;border-radius:999px;padding:6px 14px;">⚙ 设置</a>'
        f"<main>{inner}</main>"
        '<footer class="foot">头版 · 由本地数据管道生成 · SQLite-backed</footer>'
        "</body></html>"
    )


def linkify(value: str) -> str:
    escaped = escape(value)
    return URL_RE.sub(lambda match: external_link(match.group(0)), escaped)


def external_link(url: str | None) -> str:
    if not url:
        return ""
    return f'<a class="ext" href="{escape_attr(url)}" target="_blank" rel="noreferrer">原文 ↗</a>'


FULLWIDTH_AMP_ENTITY = re.compile(r"＆(#x?[0-9A-Fa-f]+;)")


def _unescape_full(value: object) -> str:
    text = "" if value is None else str(value)
    text = FULLWIDTH_AMP_ENTITY.sub(r"&\1", text)
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def escape(value: object) -> str:
    return html.escape(_unescape_full(value))


def escape_attr(value: object) -> str:
    return html.escape(_unescape_full(value), quote=True)


CSS = """
*{box-sizing:border-box}
html{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
body{
  margin:0;color:#1b1714;
  background-color:#f3ecdc;
  background-image:
    radial-gradient(1100px 540px at 72% -8%, #fdf9f0 0%, rgba(253,249,240,0) 62%),
    linear-gradient(180deg,#f6f0e3 0%, #efe7d6 100%);
  background-attachment:fixed;
  font-family:"Newsreader","Noto Serif SC",Georgia,serif;
  font-size:17px;line-height:1.62;
}
main{width:min(1200px,calc(100vw - 40px));margin:0 auto}
a{color:inherit;text-decoration:none}
.empty{color:#8a7d68;text-align:center;padding:90px 0;font-size:18px}

/* masthead */
.masthead{text-align:center;padding:38px 0 0}
.edition{font-family:"Archivo","Noto Sans SC",sans-serif;text-transform:uppercase;
  letter-spacing:.3em;font-size:11px;font-weight:600;color:#9a7a4f}
.wordmark{font-family:"Noto Serif SC","Fraunces",serif;font-weight:900;
  font-size:clamp(52px,9vw,112px);line-height:.9;letter-spacing:.06em;margin:12px 0 8px}
.tagline{font-family:"Archivo",sans-serif;text-transform:uppercase;
  letter-spacing:.42em;font-size:11px;color:#5f574b}
.rule-d{border:0;border-top:3px solid #1b1714;border-bottom:1px solid #1b1714;height:5px;margin:24px 0 0}
.topnav{display:flex;justify-content:center;gap:6px;flex-wrap:wrap;padding:14px 0;
  border-bottom:1px solid #d8cdb8}
.topnav a{font-family:"Archivo","Noto Sans SC",sans-serif;font-size:13px;font-weight:600;
  letter-spacing:.12em;text-transform:uppercase;padding:6px 16px;border-radius:999px;color:#5f574b;
  transition:background .2s,color .2s}
.topnav a.active,.topnav a:hover{background:#1b1714;color:#f3ecdc}
.stats{font-family:"Archivo","Noto Sans SC",sans-serif;font-size:12px;letter-spacing:.08em;
  color:#9a8d76;padding:12px 0 6px}

/* section head */
.section-head{display:flex;align-items:center;gap:16px;margin:56px 0 26px}
.section-head .bar{width:28px;height:4px;background:var(--cat,#c2371d);border-radius:2px;flex:none}
.section-head h2{margin:0;font-family:"Archivo","Noto Sans SC",sans-serif;font-size:15px;
  font-weight:700;letter-spacing:.22em;text-transform:uppercase;white-space:nowrap}
.section-head::after{content:"";flex:1;height:1px;background:#d8cdb8}

/* kicker */
.kicker{font-family:"Archivo","Noto Sans SC",sans-serif;font-size:12px;font-weight:700;
  letter-spacing:.18em;text-transform:uppercase;color:var(--cat,#c2371d);display:inline-block}

/* hero */
.hero{display:grid;grid-template-columns:1.05fr .95fr;gap:44px;align-items:center;
  padding:44px 0 46px;border-bottom:3px double #1b1714}
.hero .card-media{aspect-ratio:4/3;margin:0}
.hero .card-title{font-family:"Noto Serif SC","Fraunces",serif;font-weight:900;
  font-size:clamp(32px,4.4vw,58px);line-height:1.07;letter-spacing:-.01em;margin:.2em 0 .32em}
.hero .card-dek{font-size:20px;line-height:1.6;color:#43392e;margin:0 0 18px;
  display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.hero:hover .card-title{color:var(--cat,#c2371d)}

/* media */
.card-media{position:relative;overflow:hidden;background:#e7ddc9;border-radius:5px;margin-bottom:15px}
.card-media::after{content:"";position:absolute;inset:0;border-radius:5px;
  box-shadow:inset 0 0 0 1px rgba(27,23,20,.10), inset 0 -50px 60px -40px rgba(27,23,20,.22)}
.card-media img{display:block;width:100%;height:100%;object-fit:cover;transition:transform .7s ease}

/* card */
.card{display:flex;flex-direction:column;animation:rise .6s both}
.card .card-media img{aspect-ratio:16/10}
.card:hover .card-media img{transform:scale(1.05)}
.card-title{font-family:"Noto Serif SC","Fraunces",serif;font-weight:700;font-size:22px;
  line-height:1.2;margin:.34em 0 .36em;transition:color .2s}
.card:hover .card-title{color:var(--cat,#c2371d)}
.card-dek{margin:0 0 14px;color:#5f574b;font-size:15.5px;line-height:1.55;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.card-meta{margin-top:auto;font-family:"Archivo","Noto Sans SC",sans-serif;font-size:12.5px;
  letter-spacing:.04em;color:#9a8d76;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.card-meta .dot{opacity:.5}

/* layouts */
.featured{display:grid;grid-template-columns:repeat(3,1fr);gap:36px;padding:38px 0;
  border-bottom:1px solid #d8cdb8}
.stream{display:grid;grid-template-columns:repeat(auto-fill,minmax(288px,1fr));gap:40px 36px}

/* reading */
.backlink{display:inline-block;font-family:"Archivo","Noto Sans SC",sans-serif;font-size:12px;
  letter-spacing:.14em;text-transform:uppercase;color:#9a8d76;margin:30px 0 4px}
.backlink:hover{color:#c2371d}
.read{max-width:744px;margin:0 auto;padding-top:10px}
.read .kicker{font-size:13px;margin-bottom:6px}
.read h1{font-family:"Noto Serif SC","Fraunces",serif;font-weight:900;
  font-size:clamp(31px,5vw,50px);line-height:1.12;letter-spacing:-.01em;margin:.18em 0 .42em}
.meta{font-family:"Archivo","Noto Sans SC",sans-serif;font-size:13px;letter-spacing:.05em;
  color:#9a8d76;display:flex;gap:10px;flex-wrap:wrap;align-items:center;
  padding-bottom:22px;border-bottom:1px solid #d8cdb8}
.meta .dot{opacity:.5}
.meta .ext{color:#c2371d;font-weight:600}
.lead-media{margin:28px 0 6px;border-radius:8px;overflow:hidden;background:#e7ddc9;
  box-shadow:0 18px 40px -28px rgba(27,23,20,.5)}
.lead-media img{width:100%;display:block}
.article-body{margin-top:10px}
.article-body p{font-size:19px;line-height:1.82;margin:0 0 20px;color:#2a241e}
.article-body h3{font-family:"Noto Serif SC",serif;font-size:21px;margin:30px 0 12px}
.sec{display:flex;align-items:center;gap:14px;margin:42px 0 18px}
.sec span{font-family:"Archivo","Noto Sans SC",sans-serif;font-size:13px;font-weight:700;
  letter-spacing:.2em;text-transform:uppercase;color:var(--cat,#c2371d);white-space:nowrap}
.sec::before{content:"";width:24px;height:4px;background:var(--cat,#c2371d);border-radius:2px;flex:none}
.sec::after{content:"";flex:1;height:1px;background:#e0d6c2}
.cite{font-family:"Archivo",sans-serif;font-size:11px;font-weight:700;color:var(--cat,#c2371d);
  vertical-align:super;line-height:0;padding:0 1px}

/* sources */
.sources{margin-top:46px}
.src-list{list-style:none;margin:14px 0 0;padding:0}
.src{display:grid;grid-template-columns:34px 1fr;gap:14px;padding:17px 0;border-top:1px solid #e0d6c2}
.src-no{font-family:"Archivo",sans-serif;font-weight:800;font-size:16px;color:var(--cat,#c2371d)}
.src-name{font-family:"Archivo","Noto Sans SC",sans-serif;font-size:12px;letter-spacing:.08em;
  text-transform:uppercase;color:#9a8d76;margin-bottom:3px}
.src-title{font-family:"Noto Serif SC","Fraunces",serif;font-size:17px;line-height:1.4}
.src-title:hover{color:#c2371d;text-decoration:underline}

.facts{margin-top:36px}
.facts summary{font-family:"Archivo",sans-serif;font-size:12px;letter-spacing:.1em;
  text-transform:uppercase;color:#9a8d76;cursor:pointer}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1f1b16;color:#f3ecdc;
  padding:18px;border-radius:10px;font-size:13.5px;line-height:1.6;margin-top:12px}

.foot{text-align:center;font-family:"Archivo","Noto Sans SC",sans-serif;font-size:11px;
  letter-spacing:.16em;text-transform:uppercase;color:#a89a82;padding:60px 0 50px}

@keyframes rise{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}

@media(max-width:860px){
  .hero{grid-template-columns:1fr;gap:22px;padding:30px 0 34px}
  .featured{grid-template-columns:1fr;gap:34px}
}
"""
