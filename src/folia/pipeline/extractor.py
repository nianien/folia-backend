from __future__ import annotations

from html.parser import HTMLParser

from .text import clean_text


class ArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._capture = False
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"}:
            self._skip_depth += 1
        if tag in {"article", "main", "p", "h1", "h2", "h3", "li", "blockquote"}:
            self._capture = True

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth and tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"}:
            self._skip_depth -= 1
        if tag in {"article", "main", "p", "h1", "h2", "h3", "li", "blockquote"}:
            self._chunks.append("\n")
            self._capture = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = clean_text(data)
        if text and (self._capture or len(text) > 80):
            self._chunks.append(text)

    def text(self) -> str:
        lines = [clean_text(line) for line in "".join(self._chunks).splitlines()]
        return "\n\n".join(line for line in lines if line)


def html_to_text(content_html: str | None) -> str:
    """Convert feed entry content HTML to plain text. No fetching."""
    parser = ArticleTextParser()
    parser.feed(content_html or "")
    return parser.text()


def fetch_fulltext(url: str) -> str:
    """抓文章 URL, 用 trafilatura 抽净化正文。取代 fulltextrss。失败返回空串。"""
    import trafilatura

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    return (text or "").strip()
