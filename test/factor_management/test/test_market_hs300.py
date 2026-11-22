"""M1：用真实 399300 日线计算 market raw。"""

from __future__ import annotations

import pytest

from zstock.factor_management.factors_market import MarketFactors


def test_compute_market_raw_on_hs300(hs300_ohlcv, factor_raw):
    raw = MarketFactors.compute_market_raw(hs300_ohlcv, index_name="沪深300")
    if len(hs300_ohlcv) < MarketFactors.min_bars():
        assert "reason" in raw
        return
    assert "trend_gap" in raw
    assert factor_raw.get("market") is not None


def test_compute_market_raw_asof_and_missing_col(hs300_ohlcv):
    asof = MarketFactors.compute_market_raw(
        hs300_ohlcv, trade_date="2024-01-02", index_name="沪深300"
    )
    assert "trend_gap" in asof or "reason" in asof

    bad = hs300_ohlcv.drop(columns=["close"], errors="ignore")
    out = MarketFactors.compute_market_raw(bad)
    assert "reason" in out

    short = hs300_ohlcv.head(10)
    assert "reason" in MarketFactors.compute_market_raw(short)
