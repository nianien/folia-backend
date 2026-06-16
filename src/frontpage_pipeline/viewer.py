from __future__ import annotations

import html
import re
import sqlite3
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


URL_RE = re.compile(r"https?://[^\s<>()]+")


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
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}")

    return ViewerHandler


def route_request(database: Path, path: str) -> tuple[int, str]:
    parsed = urllib.parse.urlsplit(path)
    segments = [segment for segment in parsed.path.split("/") if segment]
    conn = connect_viewer(database)
    try:
        if not segments:
            return int(HTTPStatus.OK), render_dashboard(conn)
        if len(segments) == 2 and segments[0] == "cluster":
            return render_cluster_response(conn, segments[1])
        if len(segments) == 2 and segments[0] == "article":
            return render_article_response(conn, segments[1])
        return int(HTTPStatus.NOT_FOUND), page("Not found", "<p>Route not found.</p>")
    finally:
        conn.close()


def connect_viewer(database: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    return conn


def render_cluster_response(conn: sqlite3.Connection, raw_cluster_id: str) -> tuple[int, str]:
    try:
        cluster_id = int(raw_cluster_id)
    except ValueError:
        return int(HTTPStatus.BAD_REQUEST), page("Bad request", "<p>Cluster id must be an integer.</p>")
    body = render_cluster_detail(conn, cluster_id)
    if body is None:
        return int(HTTPStatus.NOT_FOUND), page("Cluster not found", "<p>Cluster not found.</p>")
    return int(HTTPStatus.OK), body


def render_article_response(conn: sqlite3.Connection, article_id: str) -> tuple[int, str]:
    body = render_article_detail(conn, article_id)
    if body is None:
        return int(HTTPStatus.NOT_FOUND), page("Article not found", "<p>Article not found.</p>")
    return int(HTTPStatus.OK), body


def render_dashboard(conn: sqlite3.Connection) -> str:
    stats = fetch_stats(conn)
    clusters = list(
        conn.execute(
            """
            SELECT c.id, c.title, c.source_count, c.synthesis_status, c.synthesis_model,
                   c.updated_at, COUNT(a.id) AS article_count
            FROM clusters c
            LEFT JOIN articles a ON a.cluster_id=c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC, c.id DESC
            """
        )
    )
    sources = list(
        conn.execute(
            """
            SELECT s.id, s.name, s.enabled, s.last_error, COUNT(a.id) AS article_count
            FROM sources s
            LEFT JOIN articles a ON a.source_id=s.id
            GROUP BY s.id
            ORDER BY s.enabled DESC, s.name
            """
        )
    )

    cluster_rows = []
    for cluster in clusters:
        status = cluster["synthesis_status"] or "pending"
        cluster_rows.append(
            f"""
            <tr>
              <td><a href="/cluster/{cluster['id']}">#{cluster['id']}</a></td>
              <td><a href="/cluster/{cluster['id']}">{escape(cluster['title'])}</a></td>
              <td>{cluster['article_count']}</td>
              <td>{cluster['source_count']}</td>
              <td><span class="badge">{escape(status)}</span></td>
              <td>{escape(cluster['synthesis_model'])}</td>
            </tr>
            """
        )

    source_rows = []
    for source in sources:
        enabled = "enabled" if source["enabled"] else "disabled"
        error = source["last_error"] or ""
        source_rows.append(
            f"""
            <tr>
              <td>{escape(source['name'])}</td>
              <td><span class="badge">{enabled}</span></td>
              <td>{source['article_count']}</td>
              <td class="muted">{escape(error)}</td>
            </tr>
            """
        )

    body = f"""
    <section class="cards">
      {metric_card("Articles", stats["articles"])}
      {metric_card("Fetch OK", stats["fetch_ok"])}
      {metric_card("Extract OK", stats["extract_ok"])}
      {metric_card("Clusters", stats["clusters"])}
      {metric_card("Synthesized", stats["synth_ok"])}
    </section>
    <section class="panel">
      <h2>Clusters</h2>
      {table(["ID", "Title", "Articles", "Sources", "Status", "Model"], "".join(cluster_rows))}
    </section>
    <section class="panel">
      <h2>Sources</h2>
      {table(["Source", "State", "Articles", "Last error"], "".join(source_rows))}
    </section>
    """
    return page("Frontpage Pipeline Viewer", body)


def render_cluster_detail(conn: sqlite3.Connection, cluster_id: int) -> str | None:
    cluster = conn.execute("SELECT * FROM clusters WHERE id=?", (cluster_id,)).fetchone()
    if cluster is None:
        return None
    articles = list(
        conn.execute(
            """
            SELECT a.id, a.title, a.source_name, a.url, a.published_at, a.extract_status,
                   LENGTH(a.extracted_text) AS text_len, cs.source_no
            FROM articles a
            LEFT JOIN cluster_sources cs ON cs.article_id=a.id
            WHERE a.cluster_id=?
            ORDER BY cs.source_no, a.published_at DESC
            """,
            (cluster_id,),
        )
    )
    article_cards = []
    for article in articles:
        article_cards.append(
            f"""
            <article class="source-card">
              <div class="source-no">[{article['source_no']}]</div>
              <div>
                <h3><a href="/article/{escape_attr(article['id'])}">{escape(article['title'])}</a></h3>
                <p class="muted">{escape(article['source_name'])} · {escape(article['published_at'])}</p>
                <p>
                  <span class="badge">{escape(article['extract_status'])}</span>
                  <span class="muted">{article['text_len'] or 0} chars</span>
                </p>
                <p>{external_link(article['url'])}</p>
              </div>
            </article>
            """
        )
    synthesized = cluster["synthesized_text"] or "Not synthesized yet."
    body = f"""
    <p><a href="/">← Back to dashboard</a></p>
    <section class="panel">
      <h1>{escape(cluster['title'])}</h1>
      <p class="muted">
        Cluster #{cluster_id} · {cluster['source_count']} sources ·
        status {escape(cluster['synthesis_status'])} · model {escape(cluster['synthesis_model'])}
      </p>
    </section>
    <section class="panel article-body">
      {markdown_to_html(synthesized)}
    </section>
    <section class="panel">
      <h2>Source Articles</h2>
      {''.join(article_cards) or '<p>No articles.</p>'}
    </section>
    """
    return page(f"Cluster #{cluster_id}", body)


def render_article_detail(conn: sqlite3.Connection, article_id: str) -> str | None:
    article = conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
    if article is None:
        return None
    facts = article["article_facts"] or ""
    body = f"""
    <p><a href="/cluster/{article['cluster_id']}">← Back to cluster</a></p>
    <section class="panel">
      <h1>{escape(article['title'])}</h1>
      <p class="muted">{escape(article['source_name'])} · {escape(article['published_at'])}</p>
      <p>{external_link(article['url'])}</p>
      <p>
        <span class="badge">fetch {escape(article['fetch_status'])}</span>
        <span class="badge">extract {escape(article['extract_status'])}</span>
        <span class="badge">facts {escape(article['fact_status'])}</span>
      </p>
    </section>
    <section class="panel">
      <h2>Extracted Text</h2>
      <pre>{escape(article['extracted_text'])}</pre>
    </section>
    <section class="panel">
      <h2>Facts JSON</h2>
      <pre>{escape(facts)}</pre>
    </section>
    """
    return page(article["title"], body)


def fetch_stats(conn: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "articles": "SELECT COUNT(*) FROM articles",
        "fetch_ok": "SELECT COUNT(*) FROM articles WHERE fetch_status='ok'",
        "extract_ok": "SELECT COUNT(*) FROM articles WHERE extract_status='ok'",
        "clusters": "SELECT COUNT(*) FROM clusters",
        "synth_ok": "SELECT COUNT(*) FROM clusters WHERE synthesis_status='ok'",
    }
    return {name: int(conn.execute(query).fetchone()[0]) for name, query in queries.items()}


def markdown_to_html(markdown: str) -> str:
    blocks = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{'<br>'.join(paragraph)}</p>")
            paragraph.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue
        if line == "---":
            flush_paragraph()
            blocks.append("<hr>")
            continue
        if line.startswith("# "):
            flush_paragraph()
            blocks.append(f"<h1>{linkify(line[2:])}</h1>")
            continue
        if line.startswith("## "):
            flush_paragraph()
            blocks.append(f"<h2>{linkify(line[3:])}</h2>")
            continue
        if line.startswith("### "):
            flush_paragraph()
            blocks.append(f"<h3>{linkify(line[4:])}</h3>")
            continue
        paragraph.append(linkify(line))
    flush_paragraph()
    return "\n".join(blocks)


def metric_card(label: str, value: int) -> str:
    return f"""
    <div class="card">
      <div class="metric">{value}</div>
      <div class="muted">{escape(label)}</div>
    </div>
    """


def table(headers: list[str], rows: str) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    if not rows:
        rows = f"<tr><td colspan=\"{len(headers)}\" class=\"muted\">No rows.</td></tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f1e8;
      --panel: #fffaf0;
      --ink: #1f2933;
      --muted: #667085;
      --line: #e4d8c7;
      --accent: #7c3aed;
      --accent-soft: #ede9fe;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ width: min(1180px, calc(100vw - 32px)); margin: 32px auto; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    h1, h2, h3 {{ line-height: 1.2; margin: 0 0 14px; }}
    .topbar {{ display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 18px; }}
    .topbar p {{ margin: 0; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 18px; }}
    .card, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 8px 24px rgba(31, 41, 51, 0.06);
    }}
    .card {{ padding: 18px; }}
    .panel {{ padding: 22px; margin-bottom: 18px; }}
    .metric {{ font-size: 34px; font-weight: 750; letter-spacing: -0.04em; }}
    .muted {{ color: var(--muted); }}
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: #4c1d95;
      font-size: 12px;
      font-weight: 650;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px 8px; text-align: left; vertical-align: top; }}
    th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #111827;
      color: #f9fafb;
      padding: 16px;
      border-radius: 14px;
      overflow-x: auto;
    }}
    .article-body p {{ margin: 0 0 16px; }}
    .article-body h1 {{ font-size: 36px; }}
    .article-body h2 {{ margin-top: 26px; padding-top: 18px; border-top: 1px solid var(--line); }}
    .source-card {{ display: grid; grid-template-columns: 48px 1fr; gap: 12px; padding: 14px 0; border-top: 1px solid var(--line); }}
    .source-card:first-of-type {{ border-top: 0; }}
    .source-no {{ color: var(--accent); font-weight: 800; }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>{escape(title)}</h1>
        <p class="muted">SQLite-backed local viewer for pipeline output.</p>
      </div>
      <p><a href="/">Dashboard</a></p>
    </header>
    {body}
  </main>
</body>
</html>
"""


def linkify(value: str) -> str:
    escaped = escape(value)
    return URL_RE.sub(lambda match: external_link(match.group(0)), escaped)


def external_link(url: str | None) -> str:
    if not url:
        return ""
    safe_url = escape_attr(url)
    label = escape(url)
    return f'<a href="{safe_url}" target="_blank" rel="noreferrer">{label}</a>'


def escape(value: object) -> str:
    return html.escape("" if value is None else str(value))


def escape_attr(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)
