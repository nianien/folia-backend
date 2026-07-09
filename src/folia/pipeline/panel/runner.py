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
            "phase": "启动中",  # 随即进入 爬取中/解析中/... 阶段; 无独立"自检"状态
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

    def _set_phase(self, phase: str) -> None:
        """run_once 每进入一个阶段回调这里, 面板状态随之变化(爬取中/解析中/...)。"""
        self.status["phase"] = phase

    # --- internals ---
    def _loop(self) -> None:
        from ..config import load_settings
        from ..db import connect, init_db

        conn = connect(self.db_path)
        init_db(conn)
        while not self._stop.is_set():
            settings = load_settings(conn)
            self._run_now = False
            processed = self._run_cycle(conn, settings)  # 逐阶段上报, 只处理未完成项, 幂等, 限量
            # 有积压(处理了东西)就 5 秒后接着啃; 都消化完了才歇到抓取间隔。
            if processed > 0:
                wait: float = 5.0
            else:
                self.status["phase"] = "空闲·等待下一轮"
                wait = max(5, int(settings.get("loop", {}).get("interval", 1800)))
            self._wake.wait(timeout=wait)  # 立即跑会提前唤醒
            self._wake.clear()

    def _run_cycle(self, conn, settings: dict) -> int:
        """跑一轮 run_once, 返回本轮处理项数(供 _loop 决定节奏)。单轮失败不终止线程。"""
        from ..cli import run_once
        from ..store.export import write_frontpage

        self.status["busy"] = True
        processed = 0
        try:
            processed = run_once(conn, settings, on_stage=self._set_phase)
            message = f"自检完成, 本轮处理 {processed} 项"
            dsn = settings.get("database", {}).get("url")
            if dsn and processed > 0:  # 有变化才推云端, 免得空转反复导出
                count = write_frontpage(conn, self.out_path)
                from ..store.loader import load as load_stories

                total, _active = load_stories(self.out_path, dsn)
                message = f"处理 {processed} 项; 导出 {count}, 入库 {total} 条"
            self.status.update(last_ok=True, last_message=message)
        except Exception as exc:  # 单轮失败不终止线程
            self.status.update(last_ok=False, last_message=f"失败: {exc}")
        finally:
            self.status.update(
                busy=False,
                last_run=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        return processed
