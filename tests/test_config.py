from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from folia.pipeline.config import is_pg_dsn, load_settings, load_source_map
from folia.pipeline.db import connect, init_db
from folia.pipeline.panel import settings as store


class ConfigDbTest(unittest.TestCase):
    def _db(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(Path(tmp.name) / "t.sqlite")
        init_db(conn)
        return conn

    def test_defaults_when_settings_empty(self) -> None:
        s = load_settings(self._db())
        self.assertEqual(s["dedupe"]["same_event_threshold"], 0.85)
        self.assertIs(s["loop"]["enabled"], False)
        self.assertEqual(s["loop"]["interval"], 1800)
        self.assertEqual(s["model"]["provider"], "heuristic")

    def test_dotted_override_with_type_coercion(self) -> None:
        conn = self._db()
        store.set_many(
            conn,
            {
                "dedupe.same_event_threshold": "0.9",  # float
                "loop.enabled": "1",                   # bool
                "loop.interval": "600",                # int
                "embeddings.model": "custom-embed",    # str
                "poller.timeout_seconds": "45",        # int
            },
        )
        s = load_settings(conn)
        self.assertEqual(s["dedupe"]["same_event_threshold"], 0.9)
        self.assertIs(s["loop"]["enabled"], True)
        self.assertEqual(s["loop"]["interval"], 600)
        self.assertEqual(s["embeddings"]["model"], "custom-embed")
        self.assertEqual(s["poller"]["timeout_seconds"], 45)

    def test_bad_int_falls_back_to_default(self) -> None:
        conn = self._db()
        store.set_many(conn, {"loop.interval": "not-a-number"})
        self.assertEqual(load_settings(conn)["loop"]["interval"], 1800)

    def test_source_map_resolve(self) -> None:
        conn = self._db()
        store.set_source_map(conn, "title", "BBC World", "BBC", "broadsheet", "international")
        store.set_source_map(conn, "stream_id", "feed/3", "AP", "wire", "international")
        sm = load_source_map(conn)
        self.assertEqual(sm.resolve(None, "BBC World").tier, "broadsheet")
        self.assertEqual(sm.resolve("feed/3", None).category, "international")
        unmatched = sm.resolve(None, "Nope")
        self.assertEqual((unmatched.tier, unmatched.category), ("unknown", "uncategorized"))

    def test_source_map_delete(self) -> None:
        conn = self._db()
        store.set_source_map(conn, "title", "X", "X", "wire", "international")
        self.assertEqual(len(store.list_source_map(conn)), 1)
        store.delete_source_map(conn, "title", "X")
        self.assertEqual(len(store.list_source_map(conn)), 0)

    def test_feed_seed_and_crud(self) -> None:
        from folia.pipeline.db import seed_default_feeds

        conn = self._db()
        self.assertEqual(store.list_feeds(conn), [])          # 初始空
        self.assertEqual(seed_default_feeds(conn), 6)         # 播种默认
        self.assertEqual(len(store.list_feeds(conn)), 6)
        self.assertEqual(seed_default_feeds(conn), 0)         # 非空不重播
        store.add_feed(conn, "https://x.example/rss", "X", "wire", "tech")
        self.assertEqual(len(store.list_feeds(conn)), 7)
        store.remove_feed(conn, "https://x.example/rss")
        self.assertEqual(len(store.list_feeds(conn)), 6)

    def test_is_pg_dsn(self) -> None:
        self.assertTrue(is_pg_dsn("postgres://user@host/db"))
        self.assertTrue(is_pg_dsn("postgresql://user@host/db"))
        for bad in ("http://evil", "file:///etc/passwd", "redis://x", ""):
            self.assertFalse(is_pg_dsn(bad))


if __name__ == "__main__":
    unittest.main()
