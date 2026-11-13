"""同步脚本：stockdb → Mongo（ohlcv / capital_flow / nightly）。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from zstock.data_management.query_service import COL_CAPITAL_FLOW, COL_OHLCV, COL_STOCK_INFO
from test.data_management.test.conftest import FakeDatabaseService, FakeMongoDB

LIVE = (
    Path(__file__).resolve().parents[2]
    / "factor_management"
    / "test"
    / "fixtures"
    / "live_calc_20240102.json"
)


def _mgr(fake: FakeMongoDB):
    class Mgr:
        mongo_db = fake

        async def init_mongodb(self):
            self.mongo_db = fake

        async def close_connections(self):
            return None

    return Mgr()


def _patch_db(monkeypatch, fake: FakeMongoDB, fake_ds: FakeDatabaseService | None = None):
    mgr = _mgr(fake)
    monkeypatch.setattr("zstock.common.utils.db_utils.db_manager", mgr)
    monkeypatch.setattr("zstock.common.utils.db_utils.get_database", lambda: fake)
    ds = fake_ds or FakeDatabaseService({})
    monkeypatch.setattr(
        "zstock.data_management.query_service.get_database", lambda: ds.db
    )
    monkeypatch.setattr(
        "zstock.data_management.query_service.DataQueryService._instance",
        None,
    )
    return mgr, ds


@pytest.mark.asyncio
async def test_sync_ohlcv_main(stock_ohlcv_docs, hs300_rows, stock_info_sample, monkeypatch):
    fake = FakeMongoDB()
    stocks = [
        d for d in stock_info_sample["sample"] if d["code"] in {"603201", "601107", "000060"}
    ]
    if not stocks:
        stocks = [{"code": "603201", "name": "常润股份", "is_mainboard": True, "is_st": False}]
    ds = FakeDatabaseService({COL_STOCK_INFO: stocks})
    _patch_db(monkeypatch, fake, ds)
    df_stocks = pd.DataFrame([d for d in stock_ohlcv_docs if d["code"] == "603201"][:8])
    df_idx = pd.DataFrame(hs300_rows[:8])

    def _load(codes, start, end, **k):
        symbols = list(codes)
        if symbols == ["399300"]:
            return df_idx
        if symbols == ["399001"]:
            raise RuntimeError("timeout")
        if len(symbols) == 1 and symbols[0] in {"399006", "899050"}:
            return pd.DataFrame()
        return df_stocks

    monkeypatch.setattr(
        "zstock.data_management.script.sync_ohlcv._load_bars",
        _load,
    )
    monkeypatch.setattr("sys.argv", ["sync_ohlcv.py", "--start", "2024-01-02", "--end", "2024-01-16"])
    from zstock.data_management.script.sync_ohlcv import main

    await main()
    assert await fake[COL_OHLCV].count_documents({"code": "603201"}) >= 1


@pytest.mark.asyncio
async def test_sync_capital_flow_stockdb(monkeypatch):
    if not LIVE.is_file():
        pytest.skip("缺少资金流夹具")
    with open(LIVE, encoding="utf-8") as f:
        rows = json.load(f)["flow"]["000060"]
    df = pd.DataFrame(rows)
    fake = FakeMongoDB()
    _patch_db(monkeypatch, fake)

    class QS:
        async def ensure_indexes(self):
            return None

        async def get_all_stocks(self):
            return [{"code": "000060", "name": "中金岭南"}], "fake"

        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            return {
                "000060": pd.DataFrame(
                    [
                        {
                            "code": "000060",
                            "trade_date": r["trade_date"],
                            "amount": float(r.get("turnover") or 0.0),
                        }
                        for r in rows
                    ]
                )
            }

    monkeypatch.setattr(
        "zstock.data_management.query_service.DataQueryService",
        QS,
    )
    monkeypatch.setattr(
        "zstock.data_management.script.sync_capital_flow._load_money_flow",
        lambda codes, start, end: {"000060": df},
    )

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(
        "zstock.data_management.script.sync_capital_flow.asyncio.sleep",
        _no_sleep,
    )
    from zstock.data_management.script.sync_capital_flow import (
        PERIOD_L2_DAILY,
        sync_capital_flow_range,
    )

    n = await sync_capital_flow_range("2024-01-01", "2024-01-02", code_batch=10, pause=0)
    assert n >= 1
    docs = fake[COL_CAPITAL_FLOW].docs
    assert any(d.get("period") == PERIOD_L2_DAILY for d in docs)


@pytest.mark.asyncio
async def test_nightly_pipeline_skips(monkeypatch, tmp_path):
    from zstock.data_management.script import nightly_pipeline as np

    calls = {"ohlcv": 0, "flow": 0, "factors": 0}

    async def fake_ohlcv(start, end):
        calls["ohlcv"] += 1

    async def fake_flow(start, end):
        calls["flow"] += 1
        return 0

    async def fake_pre(start, end, lookback):
        calls["factors"] += 1
        return {"market": 1}

    monkeypatch.setattr(np, "_run_ohlcv_sync", fake_ohlcv)
    monkeypatch.setattr(np, "_run_capital_flow_sync", fake_flow)
    monkeypatch.setattr(np, "_run_precompute", fake_pre)

    args = np._parse_args(
        [
            "--skip-sync",
            "--skip-factors",
            "--log-dir",
            str(tmp_path),
        ]
    )
    assert await np.run_nightly(args) == 0
    assert calls == {"ohlcv": 0, "flow": 0, "factors": 0}

    args2 = np._parse_args(
        ["--sync-days", "3", "--factor-days", "2", "--log-dir", str(tmp_path)]
    )
    assert await np.run_nightly(args2) == 0
    assert calls == {"ohlcv": 1, "flow": 1, "factors": 1}
