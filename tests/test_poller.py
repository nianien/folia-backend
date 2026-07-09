from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from folia.pipeline.db import connect, init_db

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


@unittest.skipUnless(importlib.util.find_spec("feedparser"), "feedparser 未安装")
class PollerTest(unittest.TestCase):
    def _db(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(Path(tmp.name) / "t.sqlite")
        init_db(conn)
        return conn

    def _add_fixture_feed(self, conn):
        conn.execute(
            "INSERT INTO feed (url, name, description) VALUES (?,?,?)",
            (str(FIXTURE), "Sample Wire", "本地样本源"),
        )
        conn.commit()

    def test_poll_local_feed_inserts_articles(self) -> None:
        from folia.pipeline import poller

        conn = self._db()
        self._add_fixture_feed(conn)
        inserted = poller.poll(conn, {})
        self.assertEqual(inserted, 2)
        rows = conn.execute("SELECT title, source_name, category FROM articles").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["source_name"] == "Sample Wire" for r in rows))  # 源名来自 feed.name
        self.assertTrue(all((r["category"] or "") == "" for r in rows))        # 分类留给 categorize_pending(LLM)

    def test_poll_is_idempotent(self) -> None:
        from folia.pipeline import poller

        conn = self._db()
        self._add_fixture_feed(conn)
        self.assertEqual(poller.poll(conn, {}), 2)
        self.assertEqual(poller.poll(conn, {}), 0)  # 同 guid 不重复入库
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 2)

    def test_poll_records_feed_status(self) -> None:
        from folia.pipeline import poller

        conn = self._db()
        self._add_fixture_feed(conn)
        poller.poll(conn, {})
        status = conn.execute("SELECT last_status FROM feed WHERE url=?", (str(FIXTURE),)).fetchone()[0]
        self.assertIn("ok", status)

    def test_poll_empty_feed_table_is_noop(self) -> None:
        # 不再自动播种: 空 feed 表 → 轮询啥都不做, 返回 0(源由 scripts/init_db.py / 面板提供)
        from folia.pipeline import poller

        conn = self._db()
        self.assertEqual(poller.poll(conn, {}), 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM feed").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
