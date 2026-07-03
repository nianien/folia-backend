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
                "freshrss.user": "bob",                # str
                "freshrss.mark_read": "yes",           # bool
            },
        )
        s = load_settings(conn)
        self.assertEqual(s["dedupe"]["same_event_threshold"], 0.9)
        self.assertIs(s["loop"]["enabled"], True)
        self.assertEqual(s["loop"]["interval"], 600)
        self.assertEqual(s["freshrss"]["user"], "bob")
        self.assertIs(s["freshrss"]["mark_read"], True)

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

    def test_feed_seed_falls_back_to_defaults_then_uses_table(self) -> None:
        conn = self._db()
        self.assertEqual(len(store.list_feed_seed(conn)), 6)  # empty table → DEFAULT_FEEDS
        conn.execute("INSERT INTO feed_seed(url,title,category) VALUES('u','t','c')")
        conn.commit()
        rows = store.list_feed_seed(conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "u")

    def test_is_pg_dsn(self) -> None:
        self.assertTrue(is_pg_dsn("postgres://user@host/db"))
        self.assertTrue(is_pg_dsn("postgresql://user@host/db"))
        for bad in ("http://evil", "file:///etc/passwd", "redis://x", ""):
            self.assertFalse(is_pg_dsn(bad))


if __name__ == "__main__":
    unittest.main()
