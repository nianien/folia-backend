from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from folia.pipeline.db import connect, init_db, insert_article
from folia.pipeline.dedupe import assign_pending_articles
from folia.pipeline.models import FeedArticle

# embeddings unreachable → Jaccard path; threshold 0.4
JACCARD = {"dedupe": {"jaccard_threshold": 0.4}, "embeddings": {"url": "http://127.0.0.1:1"}}


class TwoPhaseAccretionTest(unittest.TestCase):
    def _db(self) -> sqlite3.Connection:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(Path(tmp.name) / "t.sqlite")
        init_db(conn)
        return conn

    def _add(self, conn, guid: str, text: str) -> None:
        aid = insert_article(
            conn,
            FeedArticle(
                source_id="s1",
                source_name="S1",
                source_tier="wire",
                title=text,
                url=f"https://example.com/{guid}",
                guid=guid,
                published_at="2026-01-01T00:00:00+00:00",
                summary=text,
            ),
        )
        self.assertIsNotNone(aid)
        # 聚类现在只认"已分析"的文章(有 article_facts), 且用其 summary 做比较文本
        facts = json.dumps({"summary": text, "key_points": [], "source_no": 0})
        conn.execute(
            "UPDATE articles SET extracted_text=?, extract_status='ok', "
            "article_facts=?, fact_status='ok' WHERE id=?",
            (text, facts, aid),
        )
        conn.commit()

    def _clusters(self, conn) -> int:
        return conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]

    def test_phase1_new_article_joins_existing_cluster(self) -> None:
        conn = self._db()
        self._add(conn, "a", "cats kittens feline whiskers purr")
        assign_pending_articles(conn, JACCARD)
        self.assertEqual(self._clusters(conn), 1)

        # very similar new article → joins existing (Phase 1), no new cluster
        self._add(conn, "a2", "cats kittens feline whiskers purr playing")
        assign_pending_articles(conn, JACCARD)
        self.assertEqual(self._clusters(conn), 1)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM articles WHERE cluster_id IS NOT NULL").fetchone()[0],
            2,
        )

    def test_phase2_distinct_article_makes_new_cluster(self) -> None:
        conn = self._db()
        self._add(conn, "a", "cats kittens feline whiskers purr")
        assign_pending_articles(conn, JACCARD)
        self._add(conn, "q", "quantum physics electron photon research")
        assign_pending_articles(conn, JACCARD)
        self.assertEqual(self._clusters(conn), 2)

    def test_new_article_joins_one_cluster_without_merging(self) -> None:
        conn = self._db()
        # one batch, two dissimilar articles → Phase 2 makes two clusters
        self._add(conn, "a", "cats kittens feline whiskers purr")
        self._add(conn, "b", "dogs puppies canine bark leash")
        assign_pending_articles(conn, JACCARD)
        self.assertEqual(self._clusters(conn), 2)

        # a new article close to the cats cluster joins it; clusters stay 2 (never merges)
        self._add(conn, "a3", "cats kittens feline whiskers meow")
        assign_pending_articles(conn, JACCARD)
        self.assertEqual(self._clusters(conn), 2)


if __name__ == "__main__":
    unittest.main()
