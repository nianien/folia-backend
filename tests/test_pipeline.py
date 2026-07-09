from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from folia.pipeline.analyze import analyze_pending
from folia.pipeline.db import connect, init_db, insert_article, insert_directory
from folia.pipeline.dedupe import assign_pending_articles
from folia.pipeline.extractor import html_to_text
from folia.pipeline.models import FeedArticle
from folia.pipeline.synthesizer import synthesize_pending

# No [embeddings] reachable -> is_available() returns False offline -> deterministic Jaccard.
JACCARD_SETTINGS = {"dedupe": {"jaccard_threshold": 0.25}, "embeddings": {"url": "http://127.0.0.1:1"}}


class FakeModelClient:
    """纯 LLM 设计后, 分析(分类+标签+提炼)和合成都需要模型驱动。"""
    enabled = True
    model_name = "fake:test"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if system_prompt.startswith("你是新闻分析器"):
            return ('{"category": "国际", "tags": ["利率", "央行"], '
                    '"summary": "The central bank raised interest rates by 25 basis points on Tuesday, '
                    'citing inflation.", "key_points": ["25 basis points", "Tuesday"]}')
        return ("# Central bank raises interest rates\n\n"
                "The central bank raised interest rates by 25 basis points on Tuesday, "
                "citing inflation. [1][2]\n")


def make_articles() -> list[FeedArticle]:
    body_a = "The central bank raised interest rates by 25 basis points on Tuesday citing inflation."
    body_b = "Central bank raised interest rates 25 basis points Tuesday, citing inflation pressure."
    return [
        FeedArticle(
            source_id="feed/3", source_name="AP", source_tier="wire", category="",
            title="Central bank raises interest rates", url="https://ap.example/a", guid="a",
            published_at="2026-01-01T00:00:00+00:00", summary=body_a, content_html=f"<p>{body_a}</p>",
        ),
        FeedArticle(
            source_id="feed/4", source_name="Reuters", source_tier="wire", category="",
            title="Central bank raises rates 25bps", url="https://reuters.example/b", guid="b",
            published_at="2026-01-01T01:00:00+00:00", summary=body_b, content_html=f"<p>{body_b}</p>",
        ),
    ]


class PipelineTest(unittest.TestCase):
    def test_articles_analyze_cluster_and_synthesize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "frontpage.sqlite")
            init_db(conn)
            insert_directory(conn, "国际", "", "国际", "#000", 1)
            insert_directory(conn, "综合", "", "综合", "#111", 99)
            conn.commit()
            for article in make_articles():
                self.assertIsNotNone(insert_article(conn, article))
            for row in conn.execute("SELECT id, content_html FROM articles"):
                conn.execute(
                    "UPDATE articles SET extracted_text=?, extract_status='ok' WHERE id=?",
                    (html_to_text(row["content_html"]), row["id"]),
                )
            conn.commit()

            client = FakeModelClient()
            # 分析(分类+标签+提炼)一次搞定, 再聚合(用 summary + 同分类 + 24h), 再合成。
            self.assertEqual(analyze_pending(conn, client, limit=10), 2)
            self.assertEqual(scalar(conn, "SELECT COUNT(*) FROM articles WHERE category='国际'"), 2)
            self.assertEqual(scalar(conn, "SELECT COUNT(*) FROM articles WHERE tags='利率,央行'"), 2)

            self.assertEqual(assign_pending_articles(conn, JACCARD_SETTINGS), 2)
            self.assertEqual(scalar(conn, "SELECT COUNT(*) FROM clusters"), 1)  # 同事件同分类 → 一簇

            self.assertEqual(synthesize_pending(conn, client), 1)
            text = scalar(conn, "SELECT synthesized_text FROM clusters LIMIT 1")
            self.assertIn("## Sources", text)
            self.assertIn("[1]", text)
            self.assertIn("[2]", text)
            self.assertEqual(scalar(conn, "SELECT COUNT(*) FROM cluster_sources"), 2)


def scalar(conn: sqlite3.Connection, query: str):
    row = conn.execute(query).fetchone()
    return row[0]


if __name__ == "__main__":
    unittest.main()
