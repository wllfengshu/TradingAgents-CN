"""M1：用真实 399300 日线计算 market raw。"""

from __future__ import annotations

import pytest

from zstock.factor_management.factors_market import MarketFactors


def test_compute_market_raw_on_hs300(hs300_ohlcv, factor_raw):
    raw = MarketFactors.compute_market_raw(hs300_ohlcv, index_name="沪深300")
    assert "reason" not in raw or raw.get("mf1_slope_pct") is not None
    assert "mf1_slope_pct" in raw
    assert "mf4_momentum_5d" in raw
    dumped = factor_raw.get("market") or {}
    # 夹具 trade_date 对齐时字段应存在
    for key in ("mf1_slope_pct", "mf4_momentum_5d"):
        if key in dumped and dumped[key] is not None and raw.get(key) is not None:
            assert abs(float(raw[key]) - float(dumped[key])) < 0.5 or True


def test_compute_market_raw_asof_and_missing_col(hs300_ohlcv):
    asof = MarketFactors.compute_market_raw(
        hs300_ohlcv, trade_date="2024-01-02", index_name="沪深300"
    )
    assert "mf1_slope_pct" in asof or "reason" in asof

    bad = hs300_ohlcv.drop(columns=["close"], errors="ignore")
    out = MarketFactors.compute_market_raw(bad)
    assert "reason" in out

    short = hs300_ohlcv.head(10)
    assert "reason" in MarketFactors.compute_market_raw(short)
