"""nightly_scheduler：状态机与 run_now，不真正跑全市场同步。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from zstock.data_management.script import nightly_scheduler as ns


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    state = tmp_path / "scheduler_state.json"
    monkeypatch.setattr(ns, "STATE_DIR", tmp_path)
    monkeypatch.setattr(ns, "STATE_PATH", state)
    # 重新构造，避免污染模块单例
    sched = ns.NightlyScheduler()
    return sched


def test_parse_hhmm_and_status(scheduler):
    assert ns._parse_hhmm("21:30") == (21, 30)
    with pytest.raises(ValueError):
        ns._parse_hhmm("25:00")
    st = scheduler.status()
    assert st["backend"] == "python"
    assert st["status"] in {"Ready", "Disabled", "Running"}

    scheduler.set_enabled(False)
    assert scheduler.status()["status"] == "Disabled"
    assert scheduler.next_run is None
    scheduler.set_enabled(True)
    scheduler.set_time(22, 15)
    assert scheduler.hour == 22 and scheduler.minute == 15
    assert scheduler.next_run


@pytest.mark.asyncio
async def test_run_now_ok_and_busy(scheduler, monkeypatch):
    async def _ok(args):
        return 0

    monkeypatch.setattr(
        "zstock.data_management.script.nightly_pipeline.run_nightly",
        _ok,
    )
    monkeypatch.setattr(
        "zstock.data_management.script.nightly_pipeline._parse_args",
        lambda argv=None: object(),
    )
    out = await scheduler.run_now()
    assert out["last_status"] == "ok"

    scheduler.running = True
    busy = await scheduler.run_now()
    assert "执行中" in busy.get("message", "")


@pytest.mark.asyncio
async def test_run_now_error_and_loop_trigger(scheduler, monkeypatch):
    async def _boom(args):
        raise RuntimeError("pipeline boom")

    monkeypatch.setattr(
        "zstock.data_management.script.nightly_pipeline.run_nightly",
        _boom,
    )
    monkeypatch.setattr(
        "zstock.data_management.script.nightly_pipeline._parse_args",
        lambda argv=None: object(),
    )
    out = await scheduler.run_now()
    assert out["last_status"] == "error"
    assert "boom" in out["last_message"]

    calls = {"n": 0}

    async def _exec():
        calls["n"] += 1
        scheduler.running = False

    monkeypatch.setattr(scheduler, "_execute", _exec)
    scheduler.enabled = True
    scheduler.running = False
    scheduler.next_run = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")

    async def _sleep(_):
        raise asyncio.CancelledError()

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        await scheduler._loop()
    assert calls["n"] == 1


def test_main_disable(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "STATE_DIR", tmp_path)
    monkeypatch.setattr(ns, "STATE_PATH", tmp_path / "scheduler_state.json")
    # 重置模块单例状态文件路径后重建
    ns.scheduler = ns.NightlyScheduler()
    code = ns.main(["--disable", "--time", "20:00"])
    assert code == 0
    assert ns.scheduler.enabled is False
