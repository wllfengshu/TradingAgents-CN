"""同步/夜跑脚本分支：失败路径、纯函数、CLI，不跑全市场。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from test.data_management.test.conftest import FakeMongoDB
from zstock.data_management.query_service import COL_CAPITAL_FLOW, PERIOD_L2_DAILY


@pytest.mark.asyncio
async def test_nightly_pipeline_step_failures(monkeypatch, tmp_path):
    from zstock.data_management.script import nightly_pipeline as np

    async def boom_ohlcv(start, end):
        raise RuntimeError("ohlcv down")

    async def boom_flow(start, end):
        raise RuntimeError("flow down")

    async def boom_pre(start, end, lookback):
        raise RuntimeError("factors down")

    monkeypatch.setattr(np, "_run_ohlcv_sync", boom_ohlcv)
    monkeypatch.setattr(np, "_run_capital_flow_sync", boom_flow)
    monkeypatch.setattr(np, "_run_precompute", boom_pre)

    args = np._parse_args(["--log-dir", str(tmp_path), "--start", "2024-01-02", "--end", "2024-01-03"])
    assert await np.run_nightly(args) == 1

    args2 = np._parse_args(
        ["--skip-ohlcv", "--skip-flow", "--skip-factors", "--log-dir", str(tmp_path)]
    )
    assert await np.run_nightly(args2) == 0

    log_path = np._setup_logging(tmp_path / "logs")
    assert log_path.is_file()


def test_capital_flow_frames_and_parse():
    from zstock.data_management.script import sync_capital_flow as scf

    frames = {
        "000060": pd.DataFrame(
            [
                {"trade_date": "2024-01-02", "main_net": 1.0},
                {"trade_date": "2024-01-03", "main_net": 2.0},
            ]
        ),
        "603201": pd.DataFrame(),
    }
    amount_map = {("000060", "2024-01-02"): 1_000_000.0}
    df = scf._frames_to_df(frames, amount_map)
    assert not df.empty
    row = df[df["trade_date"] == "2024-01-02"].iloc[0]
    assert float(row["turnover"]) == 1_000_000.0
    assert row["period"] == PERIOD_L2_DAILY
    assert scf._frames_to_df({}).empty

    args = scf._parse_args(["--start", "2024-01-01", "--end", "2024-01-02", "--code-batch", "10"])
    assert args.start == "2024-01-01"
    assert args.code_batch == 10


@pytest.mark.asyncio
async def test_persist_capital_flow_empty_and_error(monkeypatch):
    from zstock.data_management.script import sync_capital_flow as scf

    assert await scf._persist_capital_flow_bulk(pd.DataFrame()) == 0

    class BoomDB:
        def __getitem__(self, name):
            raise RuntimeError("mongo boom")

    class BoomMgr:
        mongo_db = BoomDB()

        async def init_mongodb(self):
            return None

    monkeypatch.setattr(
        "zstock.common.utils.db_utils.db_manager",
        BoomMgr(),
    )
    df = pd.DataFrame(
        [{"code": "000060", "trade_date": "2024-01-02", "main_net": 1.0, "period": PERIOD_L2_DAILY}]
    )
    assert await scf._persist_capital_flow_bulk(df) == 0


@pytest.mark.asyncio
async def test_capital_flow_main(monkeypatch):
    from zstock.data_management.script import sync_capital_flow as scf

    async def fake_sync(start, end, **kwargs):
        return 3

    async def fake_close():
        return None

    monkeypatch.setattr(scf, "sync_capital_flow_range", fake_sync)
    monkeypatch.setattr(
        "zstock.common.utils.db_utils.db_manager.close_connections",
        fake_close,
    )
    assert await scf.main(["--start", "2024-01-01", "--end", "2024-01-02"]) == 0


def test_enrich_turnover_with_float_volume(stock_ohlcv_docs):
    from zstock.data_management.script.sync_ohlcv import _enrich_turnover_rate, _iter_windows, FREQ_SPEC

    rows = [dict(d) for d in stock_ohlcv_docs if d["code"] == "603201"][:5]
    assert rows
    for r in rows:
        r["floatVolume"] = 1e8
        r["volume"] = 1000.0
    df = pd.DataFrame(rows)
    out = _enrich_turnover_rate(df, ["603201"])
    assert float(out["turnover_rate"].iloc[0]) > 0

    wins = _iter_windows("2024-01-02", "2024-01-10", FREQ_SPEC["5m"])
    assert len(wins) >= 1
    wins_y = _iter_windows("2024-01-02", "2025-03-01", FREQ_SPEC["1d"])
    assert len(wins_y) >= 1


@pytest.mark.asyncio
async def test_persist_ohlcv_and_empty(monkeypatch, stock_ohlcv_docs):
    from zstock.data_management.script import sync_ohlcv as so

    fake = FakeMongoDB()

    class Mgr:
        mongo_db = fake

        async def init_mongodb(self):
            self.mongo_db = fake

    monkeypatch.setattr("zstock.common.utils.db_utils.db_manager", Mgr())
    await so._persist_ohlcv(pd.DataFrame(), "D")
    sample = pd.DataFrame([d for d in stock_ohlcv_docs if d["code"] == "603201"][:2])
    await so._persist_ohlcv(sample, "D")
    assert await fake["zstock_ohlcv"].count_documents({"code": "603201"}) >= 1
