from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from folia.pipeline.config import is_pg_dsn, load_settings, truthy
from folia.pipeline.db import connect, init_db, insert_directory, insert_feed, insert_setting
from folia.pipeline.panel import settings as store


class ConfigDbTest(unittest.TestCase):
    def _db(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(Path(tmp.name) / "t.sqlite")
        init_db(conn)
        return conn

    def test_empty_settings_returns_empty(self) -> None:
        # 无内置默认: 空库 → 空 dict(消费者各自带内联默认应对缺键)
        self.assertEqual(load_settings(self._db()), {})

    def test_load_rebuilds_nested_dict_as_strings(self) -> None:
        conn = self._db()
        store.set_many(
            conn,
            {
                "dedupe.same_event_threshold": "0.9",
                "loop.enabled": "1",
                "models.synthesis.provider": "openai",
                "models.synthesis.model": "gpt-4.1-mini",
            },
        )
        s = load_settings(conn)
        # 点分键还原成嵌套 dict; 叶子一律字符串(消费者读时自行转型)
        self.assertEqual(s["dedupe"]["same_event_threshold"], "0.9")
        self.assertEqual(s["loop"]["enabled"], "1")
        self.assertEqual(s["models"]["synthesis"], {"provider": "openai", "model": "gpt-4.1-mini"})

    def test_truthy(self) -> None:
        for yes in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(truthy(yes))
        for no in ("0", "false", "", "no", "off", None):
            self.assertFalse(truthy(no))

    def test_generic_inserts_are_idempotent(self) -> None:
        conn = self._db()
        self.assertEqual(insert_feed(conn, "https://x.example/rss", "X", "desc"), 1)
        self.assertEqual(insert_feed(conn, "https://x.example/rss", "X2", "desc2"), 0)  # 已存在跳过
        self.assertEqual(insert_directory(conn, "国际", "", "d", "#000", 1), 1)
        self.assertEqual(insert_directory(conn, "国际", "", "d2", "#111", 2), 0)
        self.assertEqual(insert_setting(conn, "loop.interval", "1800"), 1)
        self.assertEqual(insert_setting(conn, "loop.interval", "600"), 0)  # 不覆盖已有
        conn.commit()
        self.assertEqual(load_settings(conn)["loop"]["interval"], "1800")

    def test_install_seeds_once(self) -> None:
        import importlib.util

        script = Path(__file__).resolve().parents[1] / "scripts" / "init_db.py"
        spec = importlib.util.spec_from_file_location("folia_init_db", script)
        install = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(install)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        prev = os.environ.get("FOLIA_DB_PATH")
        os.environ["FOLIA_DB_PATH"] = str(Path(tmp.name) / "install.sqlite")
        try:
            self.assertEqual(install.main(), 0)  # 首次: 播种
            conn = connect(Path(os.environ["FOLIA_DB_PATH"]))
            s = load_settings(conn)
            self.assertEqual(len(store.list_feeds(conn)), len(install.DEFAULT_FEEDS))
            self.assertEqual(s["models"]["categorize"]["provider"], "ollama")
            self.assertEqual(s["loop"]["enabled"], "0")  # bool → '0'
            conn.close()
            self.assertEqual(install.main(), 0)  # 再跑一次: INSERT OR IGNORE, 不重复
            conn = connect(Path(os.environ["FOLIA_DB_PATH"]))
            self.assertEqual(len(store.list_feeds(conn)), len(install.DEFAULT_FEEDS))
            conn.close()
        finally:
            if prev is None:
                os.environ.pop("FOLIA_DB_PATH", None)
            else:
                os.environ["FOLIA_DB_PATH"] = prev

    def test_feed_crud(self) -> None:
        conn = self._db()
        self.assertEqual(store.list_feeds(conn), [])
        store.add_feed(conn, "https://x.example/rss", "X", "一句话描述")
        self.assertEqual(len(store.list_feeds(conn)), 1)
        store.remove_feed(conn, "https://x.example/rss")
        self.assertEqual(store.list_feeds(conn), [])

    def test_is_pg_dsn(self) -> None:
        self.assertTrue(is_pg_dsn("postgres://user@host/db"))
        self.assertTrue(is_pg_dsn("postgresql://user@host/db"))
        for bad in ("http://evil", "file:///etc/passwd", "redis://x", ""):
            self.assertFalse(is_pg_dsn(bad))


if __name__ == "__main__":
    unittest.main()
