from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from folia.pipeline.analyze import analyze_pending
from folia.pipeline.db import connect, init_db, insert_article, insert_directory
from folia.pipeline.dedupe import assign_pending_articles
from folia.pipeline.models import FeedArticle
from folia.pipeline.synthesizer import synthesize_pending


JACCARD_SETTINGS = {"dedupe": {"jaccard_threshold": 0.2}, "embeddings": {"url": "http://127.0.0.1:1"}}


class FakeModelClient:
    enabled = True
    model_name = "fake:test"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if system_prompt.startswith("你是新闻分析器"):
            return ('{"category": "科技", "tags": ["交通", "议会"], '
                    '"summary": "The city council approved the transit plan, projected to cost 2 billion dollars.", '
                    '"key_points": ["2 billion dollars"]}')
        return ("# City council approves transit plan\n\n"
                "The city council approved the transit plan. [1][2]\n")


class ModelPipelineTest(unittest.TestCase):
    def test_model_client_drives_analyze_and_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "frontpage.sqlite")
            init_db(conn)
            insert_directory(conn, "科技", "", "科技", "#000", 1)
            insert_directory(conn, "综合", "", "综合", "#111", 99)
            conn.commit()
            articles = [
                FeedArticle("sample", "Sample", "wire", "Transit plan approved", "https://example.com/a", "a", None, "City council approved the transit plan."),
                FeedArticle("sample", "Sample", "wire", "City council approves transit plan", "https://example.com/b", "b", None, "The transit plan has a projected cost of 2 billion dollars."),
            ]
            for article in articles:
                insert_article(conn, article)
            conn.execute("UPDATE articles SET extracted_text=summary, extract_status='fallback_summary'")
            conn.commit()

            client = FakeModelClient()
            self.assertEqual(analyze_pending(conn, client, limit=10), 2)
            assign_pending_articles(conn, JACCARD_SETTINGS)
            self.assertEqual(synthesize_pending(conn, client), 1)
            row = conn.execute("SELECT synthesized_text, synthesis_model FROM clusters LIMIT 1").fetchone()
            self.assertIn("[1]", row["synthesized_text"])
            self.assertIn("[2]", row["synthesized_text"])
            self.assertEqual(row["synthesis_model"], "fake:test")


if __name__ == "__main__":
    unittest.main()
