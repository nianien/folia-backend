from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from folia.pipeline.db import connect, init_db, insert_article
from folia.pipeline.dedupe import assign_pending_articles
from folia.pipeline.extractor import html_to_text
from folia.pipeline.facts import facts_pending
from folia.pipeline.models import FeedArticle
from folia.pipeline.synthesizer import synthesize_pending

# No [embeddings] reachable -> is_available() returns False offline -> deterministic Jaccard.
JACCARD_SETTINGS = {"dedupe": {"jaccard_threshold": 0.25}, "embeddings": {"url": "http://127.0.0.1:1"}}


def make_articles() -> list[FeedArticle]:
    """两篇讲同一事件(加息)的稿, token 重叠高 → Jaccard 应归一簇。"""
    body_a = "The central bank raised interest rates by 25 basis points on Tuesday citing inflation."
    body_b = "Central bank raised interest rates 25 basis points Tuesday, citing inflation pressure."
    return [
        FeedArticle(
            source_id="feed/3", source_name="AP", source_tier="wire", category="international",
            title="Central bank raises interest rates", url="https://ap.example/a", guid="a",
            published_at="2026-01-01T00:00:00+00:00", summary=body_a, content_html=f"<p>{body_a}</p>",
        ),
        FeedArticle(
            source_id="feed/4", source_name="Reuters", source_tier="wire", category="international",
            title="Central bank raises rates 25bps", url="https://reuters.example/b", guid="b",
            published_at="2026-01-01T01:00:00+00:00", summary=body_b, content_html=f"<p>{body_b}</p>",
        ),
    ]


class FakeModelClient:
    """纯 LLM 设计后, facts/synthesis 都需要模型驱动(不再有规则兜底)。"""
    enabled = True
    model_name = "fake:test"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if system_prompt.startswith("你是新闻事实抽取器"):
            return ('{"summary": "The central bank raised interest rates by 25 basis points '
                    'on Tuesday, citing inflation.", "key_points": ["25 basis points", "Tuesday"]}')
        return ("# Central bank raises interest rates\n\n"
                "The central bank raised interest rates by 25 basis points on Tuesday, "
                "citing inflation. [1][2]\n")


class PipelineTest(unittest.TestCase):
    def test_articles_cluster_and_synthesize_with_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "frontpage.sqlite")
            init_db(conn)
            for article in make_articles():
                self.assertIsNotNone(insert_article(conn, article))

            # extract_pending equivalent: content_html -> extracted_text
            for row in conn.execute("SELECT id, content_html FROM articles"):
                conn.execute(
                    "UPDATE articles SET extracted_text=?, extract_status='ok' WHERE id=?",
                    (html_to_text(row["content_html"]), row["id"]),
                )
            conn.commit()

            self.assertEqual(assign_pending_articles(conn, JACCARD_SETTINGS), 2)
            self.assertEqual(scalar(conn, "SELECT COUNT(*) FROM clusters"), 1)

            client = FakeModelClient()
            self.assertEqual(facts_pending(conn, client), 2)
            self.assertEqual(synthesize_pending(conn, client), 1)
            text = scalar(conn, "SELECT synthesized_text FROM clusters LIMIT 1")
            self.assertIn("## Sources", text)
            self.assertIn("[1]", text)
            self.assertIn("[2]", text)
            # citation machinery regression guard
            self.assertEqual(scalar(conn, "SELECT COUNT(*) FROM cluster_sources"), 2)


def scalar(conn: sqlite3.Connection, query: str):
    row = conn.execute(query).fetchone()
    return row[0]


if __name__ == "__main__":
    unittest.main()
