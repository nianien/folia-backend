from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from frontpage_pipeline.db import connect, init_db, insert_article, sync_sources
from frontpage_pipeline.dedupe import assign_pending_articles
from frontpage_pipeline.models import FeedArticle, Source
from frontpage_pipeline.viewer import route_request


class ViewerTest(unittest.TestCase):
    def test_dashboard_and_cluster_pages_render_pipeline_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "frontpage.sqlite"
            conn = connect(database)
            init_db(conn)
            source = Source(id="sample", name="Sample News", url="https://example.com/rss", tier="wire")
            sync_sources(conn, [source])
            article_id = insert_article(
                conn,
                FeedArticle(
                    source_id="sample",
                    source_name="Sample News",
                    source_tier="wire",
                    title="City council approves new transit plan",
                    url="https://example.com/transit",
                    guid="transit-1",
                    published_at="2026-06-15T08:00:00+00:00",
                    summary="The city council approved a new transit plan.",
                ),
            )
            self.assertIsNotNone(article_id)
            conn.execute(
                """
                UPDATE articles
                SET extracted_text=summary,
                    extract_status='fallback_summary',
                    article_facts='{"facts": [{"text": "The city council approved a transit plan.", "type": "core_fact"}]}',
                    fact_status='ok',
                    fetch_status='ok'
                """
            )
            assign_pending_articles(conn, 0.2)
            conn.execute(
                """
                UPDATE clusters
                SET synthesized_text='# Transit plan\n\n## Sources\n\n[1] Sample News · City council approves new transit plan · https://example.com/transit',
                    synthesis_status='ok',
                    synthesis_model='heuristic-v1'
                """
            )
            conn.commit()
            conn.close()

            dashboard_status, dashboard = route_request(database, "/")
            self.assertEqual(dashboard_status, 200)
            self.assertIn("Frontpage Pipeline Viewer", dashboard)
            self.assertIn("City council approves new transit plan", dashboard)

            cluster_status, cluster = route_request(database, "/cluster/1")
            self.assertEqual(cluster_status, 200)
            self.assertIn("Transit plan", cluster)
            self.assertIn("Source Articles", cluster)

            article_status, article = route_request(database, f"/article/{article_id}")
            self.assertEqual(article_status, 200)
            self.assertIn("Facts JSON", article)


def scalar(conn: sqlite3.Connection, query: str):
    row = conn.execute(query).fetchone()
    return row[0]


if __name__ == "__main__":
    unittest.main()
