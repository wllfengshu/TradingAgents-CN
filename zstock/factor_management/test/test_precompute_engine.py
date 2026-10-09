"""预计算引擎：用 live_calc + 沪深300 真实行情跑一日全市场原始值。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from zstock.factor_management.script.precompute_factors import (
    FactorComputeEngine,
    FactorPrecomputeService,
    _apply_resource_budget,
    _error_handler,
    _resolve_worker_count,
    _sort_ohlcv_frame,
    _sort_ohlcv_item,
)
from zstock.data_management.test.conftest import FakeDatabaseService

LIVE = Path(__file__).resolve().parent / "fixtures" / "live_calc_20240102.json"
HS300 = Path(__file__).resolve().parent / "fixtures" / "hs300_ohlcv.json"


def _live_payload():
    if not LIVE.is_file() or not HS300.is_file():
        pytest.skip("缺少 live_calc / hs300 夹具")
    with open(LIVE, encoding="utf-8") as f:
        live = json.load(f)
    with open(HS300, encoding="utf-8") as f:
        hs = json.load(f)
    members = live.get("members") or {}
    ohlcv = {c: pd.DataFrame(rows) for c, rows in (live.get("ohlcv") or {}).items()}
    flow = live.get("flow") or {}
    idx = pd.DataFrame(hs.get("rows") or [])
    if idx.empty or not members or not ohlcv:
        pytest.skip("夹具为空")
    return members, ohlcv, flow, idx


def test_worker_budget_and_sort_helpers(hs300_ohlcv, monkeypatch):
    monkeypatch.delenv("ZSTOCK_PRECOMPUTE_WORKERS", raising=False)
    monkeypatch.setenv("ZSTOCK_PRECOMPUTE_WORKERS", "not-int")
    n = _resolve_worker_count("ZSTOCK_PRECOMPUTE_WORKERS", 4, name="workers")
    assert n == 4
    monkeypatch.setenv("ZSTOCK_PRECOMPUTE_WORKERS", "2")
    assert _resolve_worker_count("ZSTOCK_PRECOMPUTE_WORKERS", 8, name="workers") >= 1
    compute, load, query, budget = _apply_resource_budget(
        resource_fraction=0.25,
        compute_workers=1,
        load_workers=1,
        query_workers=1,
    )
    assert compute >= 1 and load >= 1 and query >= 1
    assert budget.resource_fraction == 0.25
    empty = pd.DataFrame()
    code, df = _sort_ohlcv_frame("399300", empty)
    assert code == "399300" and df.empty
    no_date = pd.DataFrame({"close": [1.0]})
    _, df2 = _sort_ohlcv_frame("399300", no_date)
    assert "close" in df2.columns
    _, sorted_df = _sort_ohlcv_item(("399300", hs300_ohlcv.copy()))
    assert list(sorted_df["trade_date"]) == sorted(sorted_df["trade_date"].astype(str))
    with _error_handler("ut", critical=False):
        raise RuntimeError("non-critical")
    with pytest.raises(RuntimeError):
        with _error_handler("ut", critical=True):
            raise RuntimeError("critical")


def test_compute_engine_defaults_to_full_universe(hs300_ohlcv):
    raw = FactorComputeEngine.compute_all_factors_raw_sync(
        trade_date="2024-01-02",
        all_stocks=["399300"],
        stock_infos={},
        stock_ohlcv={},
        stock_flow_recent={},
        sectors=[],
        sector_stocks={},
        index_ohlcv={"399300": hs300_ohlcv},
        assume_sorted=False,
    )
    assert raw["trade_date"] == "2024-01-02"
    assert raw["market_raw"]


def test_compute_all_factors_on_live_calc():
    members, ohlcv, flow, idx = _live_payload()
    compute_universe = {c for codes in members.values() for c in codes if c in ohlcv}
    compute_sectors = [{"sector_code": k, "sector_name": k} for k in members]
    infos = {
        c: {"code": c, "name": c, "is_mainboard": True, "is_st": False}
        for c in ohlcv
    }
    raw = FactorComputeEngine.compute_all_factors_raw_sync(
        trade_date="2024-01-02",
        all_stocks=list(ohlcv),
        stock_infos=infos,
        stock_ohlcv=ohlcv,
        stock_flow_recent=flow,
        sectors=compute_sectors,
        sector_stocks=members,
        index_ohlcv={"399300": idx},
        assume_sorted=False,
        compute_sectors=compute_sectors,
        compute_universe=compute_universe,
        quiet=True,
    )
    assert raw["trade_date"] == "2024-01-02"
    assert raw["market_raw"]
    assert raw["sector_raw"]
    assert raw["dragon_raw_by_sector"]
    assert raw["force_raw"]
    assert raw["stock_last_close"]


@pytest.mark.asyncio
async def test_slice_and_store_with_live_preload():
    members, ohlcv, flow, idx = _live_payload()
    dates_index = {
        c: sorted(df["trade_date"].astype(str).tolist()) for c, df in ohlcv.items()
    }
    flow_dates = {
        c: sorted(str(d.get("trade_date")) for d in rows) for c, rows in flow.items()
    }
    idx_dates = sorted(idx["trade_date"].astype(str).tolist())
    preload = {
        "flow_days": 5,
        "stock_ohlcv_full": ohlcv,
        "ohlcv_dates_index": dates_index,
        "index_ohlcv_full": {"399300": idx},
        "index_dates_index": {"399300": idx_dates},
        "stock_flow_full": flow,
        "flow_dates_index": flow_dates,
        "stock_lhb_full": {},
        "lhb_dates_index": {},
        "sector_ohlcv_full": {},
        "sector_dates_index": {},
        "all_stocks": list(ohlcv),
        "stock_infos": {c: {"code": c, "name": c} for c in ohlcv},
        "sectors": [{"sector_code": k} for k in members],
        "sector_stocks": members,
        "index_name": "沪深 300",
        "compute_sectors": [{"sector_code": k} for k in members],
        "compute_universe": set(ohlcv),
    }
    sliced = FactorPrecomputeService.slice_preloaded_data(preload, "2024-01-02", 90)
    assert sliced["trade_date"] == "2024-01-02"
    assert sliced["index_ohlcv"]
    assert sliced["stock_ohlcv"]

    raw = FactorComputeEngine.compute_all_factors_raw_sync(
        **sliced,
        assume_sorted=True,
        compute_sectors=preload["compute_sectors"],
        compute_universe=preload["compute_universe"],
        quiet=True,
    )
    svc = FactorPrecomputeService.__new__(FactorPrecomputeService)
    svc.database_service = FakeDatabaseService({})
    counts = await svc._store_all("2024-01-02", raw, preload["stock_infos"])
    assert counts["market"] == 1
    assert counts["sector"] >= 1
    assert counts["dragon"] >= 1
    assert counts["force"] >= 1
    empty = await svc._store_all("2024-01-02", {}, {})
    assert empty == {"market": 0, "sector": 0, "dragon": 0, "force": 0}


@pytest.mark.asyncio
async def test_store_force_drops_score_fields():
    from zstock.data_management.query_service import COL_FACTOR_FORCE

    svc = FactorPrecomputeService.__new__(FactorPrecomputeService)
    svc.database_service = FakeDatabaseService({COL_FACTOR_FORCE: []})
    n = await svc._store_force(
        "2024-01-02",
        {
            "force_raw": [
                {
                    "code": "601921",
                    "sector_code": "SW2出版",
                    "fcoop1_main_net_ratio": 0.12,
                    "strategy_signal_score": 99.0,
                    "force_composite_score": 80.0,
                    "dragon_composite_score": 70.0,
                }
            ]
        },
        {"601921": {"name": "x"}},
    )
    assert n == 1
    stored = svc.database_service.db[COL_FACTOR_FORCE].docs[0]
    assert stored["fcoop1_main_net_ratio"] == 0.12
    assert "strategy_signal_score" not in stored
    assert "force_composite_score" not in stored
    assert "dragon_composite_score" not in stored
