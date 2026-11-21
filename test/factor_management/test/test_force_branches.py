"""M4：inject 路径与真实 flow。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from zstock.factor_management.factors_force import ForceFactors
from test.factor_management.test.conftest import make_ohlcv

LIVE = Path(__file__).resolve().parent / "fixtures" / "live_calc_20240102.json"


def test_inject_path_and_negative_main():
    ohlcv = make_ohlcv(30, close=10.0, volume=2e6, amount=5_000_000.0, turnover=5.0)
    dates = ohlcv["trade_date"].iloc[-3:].tolist()
    last = dates[-1]
    candidates = [
        {
            "code": "603201",
            "sector_code": "SW2汽车零部件",
            "main_flow": 100.0,
            "total_volume": 1e6,
            "turnover_rate": 5.0,
            "trade_date": last,
        },
        {
            "code": "000060",
            "sector_code": "SW2工业金属",
            "main_flow": -50.0,
            "total_volume": 1e6,
            "turnover_rate": 3.0,
            "trade_date": last,
        },
    ]
    out = ForceFactors.apply_cooperative_force_raw(
        candidates,
        stock_ohlcv={"603201": ohlcv, "000060": ohlcv},
        stock_flow_recent={},
        trade_date=last,
    )
    assert out
    by_code = {r["code"]: r for r in out}
    assert "fcoop1_main_net_ratio" in by_code["603201"]
    neg = by_code["000060"]
    assert float(neg["fcoop1_main_net_ratio"]) < 0
    assert float(neg["fcoop2_main_retail_ratio"]) < 0


def test_live_flow_real_docs():
    if not LIVE.is_file():
        pytest.skip("缺少 live_calc")
    with open(LIVE, encoding="utf-8") as f:
        payload = json.load(f)
    flow = payload.get("flow") or {}
    ohlcv = payload.get("ohlcv") or {}
    code = "000060" if "000060" in flow else next(iter(flow), None)
    if not code or code not in ohlcv:
        pytest.skip("live_calc 无可用资金流")
    day_docs = {code: list(flow[code])}
    stock_ohlcv = {code: pd.DataFrame(ohlcv[code])}
    td = str(day_docs[code][-1].get("trade_date") or "")
    candidates = [{"code": code, "sector_code": "SW2工业金属"}]
    out = ForceFactors.apply_cooperative_force_raw(
        candidates,
        stock_ohlcv=stock_ohlcv,
        stock_flow_recent=day_docs,
        trade_date=td,
    )
    assert out and out[0]["code"] == code
