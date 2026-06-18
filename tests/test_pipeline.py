from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from frontpage_pipeline.config import SourceMap, SourceMeta
from frontpage_pipeline.db import connect, init_db, insert_article
from frontpage_pipeline.dedupe import assign_pending_articles
from frontpage_pipeline.extractor import html_to_text
from frontpage_pipeline.facts import facts_pending
from frontpage_pipeline.freshrss_client import freshrss_item_to_article
from frontpage_pipeline.synthesizer import synthesize_pending


# No [embeddings] reachable -> is_available() returns False offline -> deterministic Jaccard.
JACCARD_SETTINGS = {"dedupe": {"jaccard_threshold": 0.25}, "embeddings": {"url": "http://127.0.0.1:1"}}


def make_source_map() -> SourceMap:
    return SourceMap(
        by_stream_id={
            "feed/3": SourceMeta(name="AP", tier="wire", category="international"),
            "feed/4": SourceMeta(name="Reuters", tier="wire", category="international"),
        },
        by_title={},
    )


class PipelineTest(unittest.TestCase):
    def test_fixture_articles_cluster_and_synthesize_with_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "frontpage.sqlite")
            init_db(conn)
            source_map = make_source_map()
            feed_path = Path(__file__).parent / "fixtures" / "freshrss_reading_list.json"
            payload = json.loads(feed_path.read_text(encoding="utf-8"))
            for item in payload["items"]:
                article = freshrss_item_to_article(item, source_map)
                self.assertIsNotNone(article)
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

            self.assertEqual(facts_pending(conn), 2)
            self.assertEqual(synthesize_pending(conn), 1)
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
