"""预计算引擎：用 live_calc + 沪深300 真实行情跑一日全市场原始值。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from zstock.factor_management.script.precompute_factors import (
    FactorPrecomputeService,
    _apply_resource_budget,
    _compute_day_sync,
    _error_handler,
    _is_stock_code,
    _resolve_worker_count,
    _sort_ohlcv_frame,
    _sort_ohlcv_item,
    compute_day_factor_raws,
)
from test.data_management.test.conftest import FakeDatabaseService

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
    compute, load, query = _apply_resource_budget(
        resource_fraction=0.25,
        compute_workers=1,
        load_workers=1,
        query_workers=1,
    )
    assert compute == 1 and load == 1 and query == 1
    compute2, _, _ = _apply_resource_budget(
        resource_fraction=0.9,
        compute_workers=None,
        load_workers=None,
        query_workers=None,
    )
    assert compute2 >= 1
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
    assert _is_stock_code("600000")
    assert not _is_stock_code("")
    assert not _is_stock_code("399300")
    assert not _is_stock_code(None)


def test_compute_day_raws_defaults_to_full_universe(hs300_ohlcv):
    raw = compute_day_factor_raws(
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
    assert "all_candidates" not in raw


def test_layers_independent_on_live_calc():
    """M4 覆盖宇宙内全部有行情个股，不依赖 M3 是否产出。"""
    members, ohlcv, flow, idx = _live_payload()
    compute_universe = {c for codes in members.values() for c in codes if c in ohlcv}
    compute_sectors = [{"sector_code": k, "sector_name": k} for k in members]
    infos = {
        c: {"code": c, "name": c, "is_mainboard": True, "is_st": False}
        for c in ohlcv
    }
    raw = compute_day_factor_raws(
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
    force_codes = {r["code"] for r in raw["force_raw"]}
    assert force_codes == compute_universe


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

    raw = compute_day_factor_raws(
        **sliced,
        assume_sorted=True,
        compute_sectors=preload["compute_sectors"],
        compute_universe=preload["compute_universe"],
        quiet=True,
    )
    svc = FactorPrecomputeService.__new__(FactorPrecomputeService)
    svc.query_service = FakeDatabaseService({})
    counts = await svc._store_all("2024-01-02", raw, preload["stock_infos"])
    assert counts["market"] == 1
    assert counts["sector"] >= 1
    assert counts["dragon"] >= 1
    assert counts["force"] >= 1
    empty = await svc._store_all("2024-01-02", {}, {})
    assert empty == {"market": 0, "sector": 0, "dragon": 0, "force": 0}


def test_compute_day_sync_on_live_slice():
    members, ohlcv, flow, idx = _live_payload()
    dates_index = {
        c: sorted(df["trade_date"].astype(str).tolist()) for c, df in ohlcv.items()
    }
    flow_dates = {
        c: sorted(str(d.get("trade_date")) for d in rows) for c, rows in flow.items()
    }
    preload = {
        "flow_days": 5,
        "stock_ohlcv_full": ohlcv,
        "ohlcv_dates_index": dates_index,
        "index_ohlcv_full": {"399300": idx},
        "index_dates_index": {"399300": sorted(idx["trade_date"].astype(str).tolist())},
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
    raw = _compute_day_sync(preload, "2024-01-02", 90, quiet=True)
    assert raw["market_raw"] and raw["sector_raw"]


@pytest.mark.asyncio
async def test_fundamental_for_date_paths():
    svc = FactorPrecomputeService.__new__(FactorPrecomputeService)
    svc._load_fundamentals = False
    assert await svc._fundamental_for_date("2024-01-02") is None
    svc._load_fundamentals = True

    class QS:
        async def get_fundamental_asof(self, td):
            return {"600000": {"bps": 1.0}}

    svc.query_service = QS()
    assert await svc._fundamental_for_date("2024-01-02")

    class BadQS:
        async def get_fundamental_asof(self, td):
            raise RuntimeError("boom")

    svc.query_service = BadQS()
    assert await svc._fundamental_for_date("2024-01-02") is None


@pytest.mark.asyncio
async def test_store_force_drops_score_fields():
    from zstock.data_management.query_service import COL_FACTOR_FORCE

    svc = FactorPrecomputeService.__new__(FactorPrecomputeService)
    svc.query_service = FakeDatabaseService({COL_FACTOR_FORCE: []})
    n = await svc._store_force(
        "2024-01-02",
        {
            "force_raw": [
                {
                    "code": "601921",
                    "sector_code": "SW2出版",
                    "fcoop1_main_net_ratio": 0.12,
                    "final_score": 99.0,
                    "force_composite_score": 80.0,
                    "dragon_score": 70.0,
                }
            ]
        },
        {"601921": {"name": "x"}},
    )
    assert n == 1
    stored = svc.query_service.db[COL_FACTOR_FORCE].docs[0]
    assert stored["fcoop1_main_net_ratio"] == 0.12
    assert "final_score" not in stored
    assert "force_composite_score" not in stored
    assert "dragon_score" not in stored
