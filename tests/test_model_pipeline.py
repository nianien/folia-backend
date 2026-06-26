from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from folia.pipeline.db import connect, init_db, insert_article
from folia.pipeline.dedupe import assign_pending_articles
from folia.pipeline.facts import facts_pending
from folia.pipeline.models import FeedArticle
from folia.pipeline.synthesizer import synthesize_pending


JACCARD_SETTINGS = {"dedupe": {"jaccard_threshold": 0.2}, "embeddings": {"url": "http://127.0.0.1:1"}}


class FakeModelClient:
    enabled = True
    model_name = "fake:test"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if "JSON" in system_prompt or "json" in user_prompt.lower():
            return """
            {
              "facts": [{"text": "The city council approved the transit plan.", "type": "core_fact"}],
              "numbers": ["The projected cost is 2 billion dollars."],
              "quotes": [],
              "background": [],
              "uncertainties": []
            }
            """
        return """# City council approves transit plan

## 核心事实

The city council approved the transit plan. [1][2]
"""


class ModelPipelineTest(unittest.TestCase):
    def test_model_client_can_drive_facts_and_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "frontpage.sqlite")
            init_db(conn)
            articles = [
                FeedArticle("sample", "Sample", "wire", "Transit plan approved", "https://example.com/a", "a", None, "City council approved the transit plan."),
                FeedArticle("sample", "Sample", "wire", "City council approves transit plan", "https://example.com/b", "b", None, "The transit plan has a projected cost of 2 billion dollars."),
            ]
            for article in articles:
                insert_article(conn, article)
            conn.execute("UPDATE articles SET extracted_text=summary, extract_status='fallback_summary'")
            conn.commit()
            assign_pending_articles(conn, JACCARD_SETTINGS)

            client = FakeModelClient()
            self.assertEqual(facts_pending(conn, client), 2)
            self.assertEqual(synthesize_pending(conn, client), 1)
            row = conn.execute("SELECT synthesized_text, synthesis_model FROM clusters LIMIT 1").fetchone()
            self.assertIn("[1]", row["synthesized_text"])
            self.assertIn("[2]", row["synthesized_text"])
            self.assertEqual(row["synthesis_model"], "fake:test")


if __name__ == "__main__":
    unittest.main()
