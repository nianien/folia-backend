from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from frontpage_pipeline.db import connect, init_db, insert_article, sync_sources
from frontpage_pipeline.dedupe import assign_pending_articles
from frontpage_pipeline.facts import facts_pending
from frontpage_pipeline.fetcher import parse_feed
from frontpage_pipeline.models import Source
from frontpage_pipeline.synthesizer import synthesize_pending


class PipelineTest(unittest.TestCase):
    def test_fixture_articles_cluster_and_synthesize_with_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "frontpage.sqlite")
            init_db(conn)
            source = Source(
                id="sample",
                name="Sample News",
                url="file://sample",
                tier="wire",
                category_hint="test",
            )
            sync_sources(conn, [source])
            feed_path = Path(__file__).parent / "fixtures" / "sample_feed.xml"
            for article in parse_feed(feed_path.read_bytes(), source):
                self.assertIsNotNone(insert_article(conn, article))

            conn.execute("UPDATE articles SET extracted_text=summary, extract_status='fallback_summary'")
            conn.commit()

            self.assertEqual(assign_pending_articles(conn, 0.25), 2)
            cluster_count = scalar(conn, "SELECT COUNT(*) FROM clusters")
            self.assertEqual(cluster_count, 1)

            self.assertEqual(facts_pending(conn), 2)
            self.assertEqual(synthesize_pending(conn), 1)
            text = scalar(conn, "SELECT synthesized_text FROM clusters LIMIT 1")
            self.assertIn("## Sources", text)
            self.assertIn("[1]", text)
            self.assertIn("[2]", text)


def scalar(conn: sqlite3.Connection, query: str):
    row = conn.execute(query).fetchone()
    return row[0]


if __name__ == "__main__":
    unittest.main()
