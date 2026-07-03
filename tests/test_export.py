from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from folia.pipeline.db import connect, init_db, insert_article
from folia.pipeline.dedupe import assign_pending_articles
from folia.pipeline.models import FeedArticle
from folia.pipeline.store.export import export_frontpage

JACCARD = {"dedupe": {"jaccard_threshold": 0.4}, "embeddings": {"url": "http://127.0.0.1:1"}}


class ExportTest(unittest.TestCase):
    def test_export_frontpage_shape(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(Path(tmp.name) / "t.sqlite")
        init_db(conn)

        aid = insert_article(
            conn,
            FeedArticle(
                source_id="s1",
                source_name="S1",
                source_tier="wire",
                category="international",
                title="Big News Event Today",
                url="https://example.com/a",
                guid="a",
                published_at="2026-01-01T00:00:00+00:00",
                summary="a summary",
            ),
        )
        conn.execute("UPDATE articles SET extracted_text='body', extract_status='ok' WHERE id=?", (aid,))
        conn.commit()
        assign_pending_articles(conn, JACCARD)  # → 1 cluster + cluster_sources

        conn.execute(
            "UPDATE clusters SET synthesis_status='ok', synthesis_model='heuristic-v1', "
            "synthesized_text=?",
            (
                "# Big News Event Today\n\n## 核心事实\n\nsomething happened [1]\n\n"
                "## Sources\n\n[1] S1 · Big News Event Today · https://example.com/a",
            ),
        )
        conn.commit()

        payload = export_frontpage(conn)
        self.assertEqual(payload["count"], 1)
        story = payload["stories"][0]
        self.assertIsInstance(story["id"], int)
        self.assertEqual(story["title"], "Big News Event Today")
        self.assertIn("something happened", story["synthesis_md"])
        self.assertNotIn("## Sources", story["synthesis_md"])  # sources section stripped
        self.assertEqual(len(story["sources"]), 1)
        self.assertEqual(story["sources"][0]["url"], "https://example.com/a")


if __name__ == "__main__":
    unittest.main()
