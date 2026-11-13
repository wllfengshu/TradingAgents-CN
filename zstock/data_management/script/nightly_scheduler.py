"""
Python 进程内夜跑调度（替代 Windows schtasks）。

随 Web API 启动；也可单独跑：
    python -m zstock.data_management.script.nightly_scheduler
    python -m zstock.data_management.script.nightly_scheduler --time 21:00
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

STATE_DIR = PROJECT_ROOT / "zstock" / "data_management" / "script" / "output" / "nightly"
STATE_PATH = STATE_DIR / "scheduler_state.json"
DEFAULT_HOUR = 21
DEFAULT_MINUTE = 0


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _parse_hhmm(value: str) -> Tuple[int, int]:
    parts = (value or "21:00").strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"非法时间: {value}")
    return hour, minute


def _next_run_at(hour: int, minute: int, after: Optional[datetime] = None) -> datetime:
    base = after or _now()
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= base:
        candidate = candidate + timedelta(days=1)
    return candidate


class NightlyScheduler:
    """单例：enable/disable + 每日定点跑 nightly_pipeline。"""

    def __init__(self) -> None:
        self.enabled = True
        self.hour = DEFAULT_HOUR
        self.minute = DEFAULT_MINUTE
        self.last_run: Optional[str] = None
        self.last_status: Optional[str] = None
        self.last_message: str = ""
        self.next_run: Optional[str] = None
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._load()

    def _load(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if not STATE_PATH.is_file():
            self._recompute_next()
            self._save()
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self.enabled = bool(data.get("enabled", True))
            self.hour = int(data.get("hour", DEFAULT_HOUR))
            self.minute = int(data.get("minute", DEFAULT_MINUTE))
            self.last_run = data.get("last_run")
            self.last_status = data.get("last_status")
            self.last_message = str(data.get("last_message") or "")
            self.next_run = data.get("next_run")
        except Exception as e:
            logger.warning("读取调度状态失败，使用默认: %s", e)
        self._recompute_next()
        self._save()

    def _save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": self.enabled,
            "hour": self.hour,
            "minute": self.minute,
            "last_run": self.last_run,
            "last_status": self.last_status,
            "last_message": self.last_message,
            "next_run": self.next_run,
        }
        STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _recompute_next(self) -> None:
        if not self.enabled:
            self.next_run = None
            return
        self.next_run = _next_run_at(self.hour, self.minute).isoformat(sep=" ")

    def status(self) -> Dict[str, Any]:
        if self.running:
            st = "Running"
        elif not self.enabled:
            st = "Disabled"
        else:
            st = "Ready"
        return {
            "task_name": "ZStockNightlyPipeline",
            "exists": True,
            "backend": "python",
            "enabled": self.enabled,
            "status": st,
            "next_run": self.next_run,
            "last_run": self.last_run,
            "last_status": self.last_status,
            "last_message": self.last_message,
            "schedule": f"{self.hour:02d}:{self.minute:02d}",
        }

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        self.enabled = bool(enabled)
        self._recompute_next()
        self._save()
        return self.status()

    def set_time(self, hour: int, minute: int) -> Dict[str, Any]:
        self.hour = int(hour)
        self.minute = int(minute)
        self._recompute_next()
        self._save()
        return self.status()

    async def run_now(self) -> Dict[str, Any]:
        if self.running:
            return {**self.status(), "message": "夜跑已在执行中"}
        await self._execute()
        return self.status()

    async def _execute(self) -> None:
        self.running = True
        self.last_run = _now().isoformat(sep=" ")
        self.last_status = "running"
        self.last_message = "nightly_pipeline 执行中"
        self._save()
        code = 1
        try:
            from zstock.data_management.script.nightly_pipeline import _parse_args, run_nightly

            args = _parse_args([])
            code = await run_nightly(args)
            self.last_status = "ok" if code == 0 else "error"
            self.last_message = "完成" if code == 0 else f"退出码 {code}"
        except Exception as e:
            self.last_status = "error"
            self.last_message = str(e)[:240]
            logger.exception("夜跑执行失败")
        finally:
            self.running = False
            self._recompute_next()
            self._save()

    async def _loop(self) -> None:
        logger.info(
            "夜跑调度已启动 enabled=%s time=%02d:%02d next=%s",
            self.enabled,
            self.hour,
            self.minute,
            self.next_run,
        )
        while True:
            try:
                if self.enabled and not self.running and self.next_run:
                    due = datetime.strptime(self.next_run, "%Y-%m-%d %H:%M:%S")
                    if _now() >= due:
                        await self._execute()
            except Exception:
                logger.exception("调度循环异常")
            await asyncio.sleep(20)

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="zstock-nightly-scheduler")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None


scheduler = NightlyScheduler()


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    p = argparse.ArgumentParser(description="ZStock Python 夜跑调度")
    p.add_argument("--time", default="21:00", help="每日运行时间 HH:MM")
    p.add_argument("--run-once", action="store_true", help="立即跑一次后退出")
    p.add_argument("--disable", action="store_true", help="禁用定时（仅写状态）")
    args = p.parse_args(argv)
    hour, minute = _parse_hhmm(args.time)
    scheduler.set_time(hour, minute)
    if args.disable:
        scheduler.set_enabled(False)
        print(scheduler.status())
        return 0
    scheduler.set_enabled(True)
    if args.run_once:
        return 0 if asyncio.run(scheduler.run_now()).get("last_status") == "ok" else 1

    async def _serve():
        scheduler.start()
        while True:
            await asyncio.sleep(3600)

    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
