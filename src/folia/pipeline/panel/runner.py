"""应用内的 pipeline 循环: 一个后台线程, 受 db 配置 loop.enabled / loop.interval 控制。

配置全部从 db 读(config.load_settings)。启停 = 面板切 loop.enabled(再 notify);
立即跑 = trigger_now; 改间隔 = 写 loop.interval。自带 sqlite 连接(只在本线程用)。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path


class PipelineRunner:
    def __init__(self, db_path: Path, out_path: str = "data/frontpage.json") -> None:
        self.db_path = db_path
        self.out_path = Path(out_path)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._run_now = False
        self.status: dict[str, object] = {
            "busy": False,
            "last_run": None,
            "last_ok": None,
            "last_message": "尚未运行",
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="pipeline-runner")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger_now(self) -> None:
        self._run_now = True
        self._wake.set()

    def notify(self) -> None:
        """配置变了(开启循环/改间隔) → 让线程尽快重读。"""
        self._wake.set()

    # --- internals ---
    def _loop(self) -> None:
        from ..config import load_settings
        from ..db import connect, init_db

        conn = connect(self.db_path)
        init_db(conn)
        while not self._stop.is_set():
            settings = load_settings(conn)
            if self._run_now or settings["loop"]["enabled"]:
                self._run_now = False
                self._run_cycle(conn, settings)
                wait: float = max(5, int(settings["loop"]["interval"]))
            else:
                wait = 3.0  # 暂停时轻量轮询, 等被开启/立即跑
            self._wake.wait(timeout=wait)
            self._wake.clear()

    def _run_cycle(self, conn, settings: dict) -> None:
        from ..cli import run_once
        from ..store.export import write_frontpage

        self.status["busy"] = True
        try:
            code = run_once(conn, settings)
            if code != 0:
                raise RuntimeError("FreshRSS 拉取失败(检查凭据 / 基座层是否在跑)")
            message = "run-once 完成"
            dsn = settings.get("database", {}).get("url")
            if dsn:
                count = write_frontpage(conn, self.out_path)
                from ..store.loader import load as load_stories

                total, _active = load_stories(self.out_path, dsn)
                message = f"run-once 完成; 导出 {count}, 入库 {total} 条"
            self.status.update(last_ok=True, last_message=message)
        except Exception as exc:  # 单轮失败不终止线程
            self.status.update(last_ok=False, last_message=f"失败: {exc}")
        finally:
            self.status.update(
                busy=False,
                last_run=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
