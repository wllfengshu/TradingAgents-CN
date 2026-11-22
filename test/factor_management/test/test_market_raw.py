"""M1：仅校验 mf* 原始值（打分见 strategy test_layer_scoring_m1 / sentiment）。"""

from __future__ import annotations

import numpy as np
import pytest

from zstock.factor_management.factors_market import MarketFactors
from test.factor_management.test.conftest import make_ohlcv


def test_compute_market_raw_insufficient_has_reason():
    short = make_ohlcv(11, close=100.0)
    raw = MarketFactors.compute_market_raw(short, index_name="沪深300")
    assert "reason" in raw
    assert "trend_gap" not in raw


def test_trend_gap_raw():
    close = np.full(120, 100.0)
    close[-1] = 105.0
    df = make_ohlcv(120, close=close, volume=1_000_000.0)
    raw = MarketFactors.compute_market_raw(df)
    expected = 105.0 / ((100.0 * 119 + 105.0) / 120.0) - 1.0
    assert raw["trend_gap"] == pytest.approx(expected)
