"""M1：合成日线校验 MF1–MF5 公式 + 风格分类；夹具做回归。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zstock.factor_management.market_factors import MarketFactors
from zstock.factor_management.test.conftest import make_ohlcv

_K_SLOPE = 800.0
_K_MOM = 30.0


def _sigmoid(x: float, k: float) -> float:
    return float(np.clip(100.0 / (1.0 + np.exp(np.clip(-k * x, -709, 709))), 0.0, 100.0))


def test_insufficient_bars_is_neutral():
    short = make_ohlcv(11, close=100.0)
    out = MarketFactors.calculate_market_sentiment(short, index_name="沪深300")
    assert out["market_risk_level"] == "yellow"
    assert out["market_composite_score"] == 50.0
    assert out["allow_new_open"] is True
    assert out["position_scale_factor"] == 0.4


def test_missing_column_is_neutral():
    df = make_ohlcv(40, close=100.0).drop(columns=["volume"])
    out = MarketFactors.calculate_market_sentiment(df)
    assert out["market_risk_level"] == "yellow"
    assert out["market_composite_score"] == 50.0


def test_mf4_momentum_matches_close_to_close_return():
    close = np.full(40, 100.0)
    close[-1] = 105.0
    df = make_ohlcv(40, close=close, volume=1_000_000.0)
    out = MarketFactors.calculate_market_sentiment(df)
    expected = (105.0 - 100.0) / 100.0
    assert out["detail"]["mf4_momentum_5d"] == pytest.approx(expected)
    assert out["market_5day_momentum"] == pytest.approx(_sigmoid(expected, _K_MOM))


def test_mf3_constant_volume_ratio_is_one():
    df = make_ohlcv(40, close=100.0, volume=1_000_000.0)
    out = MarketFactors.calculate_market_sentiment(df)
    assert out["detail"]["mf3_vol_ratio"] == pytest.approx(1.0)
    assert out["market_volume_state"] == pytest.approx(90.0)


def test_mf3_last_bar_double_volume():
    vol = np.full(40, 1_000_000.0)
    vol[-1] = 2_000_000.0
    df = make_ohlcv(40, close=100.0, volume=vol)
    out = MarketFactors.calculate_market_sentiment(df)
    vol_ma20 = (19 * 1_000_000.0 + 2_000_000.0) / 20.0
    expected = 2_000_000.0 / vol_ma20
    assert out["detail"]["mf3_vol_ratio"] == pytest.approx(expected)


def test_mf1_slope_pct_from_linear_close():
    close = 100.0 + np.arange(40, dtype=float)
    df = make_ohlcv(40, close=close, volume=1_000_000.0)
    out = MarketFactors.calculate_market_sentiment(df)
    ma = pd.Series(close).rolling(20, min_periods=20).mean().to_numpy()
    valid = ma[~np.isnan(ma)][-5:]
    slope = float(np.polyfit(np.arange(len(valid), dtype=float), valid, 1)[0])
    slope_pct = slope / valid[-1]
    assert out["detail"]["mf1_slope_pct"] == pytest.approx(slope_pct)
    assert out["market_trend_strength"] == pytest.approx(_sigmoid(slope_pct, _K_SLOPE))


def test_mf2_boll_pct_formula():
    close = 100.0 + 5.0 * np.sin(np.linspace(0, 6 * np.pi, 40))
    df = make_ohlcv(40, close=close, volume=1_000_000.0)
    ma = pd.Series(close).rolling(20, min_periods=20).mean()
    std = pd.Series(close).rolling(20, min_periods=20).std()
    upper = float(ma.iloc[-1] + 2.0 * std.iloc[-1])
    lower = float(ma.iloc[-1] - 2.0 * std.iloc[-1])
    expected = float(np.clip((close[-1] - lower) / (upper - lower), 0.0, 1.0))
    out = MarketFactors.calculate_market_sentiment(df)
    assert np.isfinite(expected)
    assert out["detail"]["mf2_boll_pct"] == pytest.approx(expected)
    assert out["market_bollinger_position"] == pytest.approx(expected * 100.0)


def test_mf5_atr_ratio_constant_range():
    df = make_ohlcv(40, close=100.0, volume=1_000_000.0)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    atr = float(pd.Series(tr).rolling(20, min_periods=20).mean().iloc[-1])
    ma = float(pd.Series(close).rolling(20, min_periods=20).mean().iloc[-1])
    expected = atr / ma
    out = MarketFactors.calculate_market_sentiment(df)
    assert out["detail"]["mf5_atr_ratio"] == pytest.approx(expected)
    # high=101, low=99, close=100 → TR=2, ATR=2, MA=100, ratio=0.02 → 70 分
    assert expected == pytest.approx(0.02)
    assert out["market_volatility_suppression"] == pytest.approx(70.0)


def test_score_from_raw_matches_calculate_mapping():
    close = 100.0 + np.arange(40, dtype=float)
    df = make_ohlcv(40, close=close, volume=1_000_000.0)
    live = MarketFactors.calculate_market_sentiment(df)
    d = live["detail"]
    replay = MarketFactors.score_from_raw(
        mf1_slope_pct=float(d["mf1_slope_pct"]),
        mf2_boll_pct=float(d["mf2_boll_pct"]),
        mf3_vol_ratio=float(d["mf3_vol_ratio"]),
        mf4_momentum_5d=float(d["mf4_momentum_5d"]),
        mf5_atr_ratio=float(d["mf5_atr_ratio"]),
    )
    assert replay["market_composite_score"] == pytest.approx(live["market_composite_score"])
    assert replay["market_risk_level"] == live["market_risk_level"]
    assert replay["position_scale_factor"] == live["position_scale_factor"]
    doc = MarketFactors.score_from_doc(
        {k: d[k] for k in ("mf1_slope_pct", "mf2_boll_pct", "mf3_vol_ratio", "mf4_momentum_5d", "mf5_atr_ratio")}
    )
    assert doc["market_composite_score"] == pytest.approx(live["market_composite_score"])


def test_score_from_raw_nan_maps_to_neutral_subscores():
    out = MarketFactors.score_from_raw(
        mf1_slope_pct=float("nan"),
        mf2_boll_pct=float("nan"),
        mf3_vol_ratio=float("nan"),
        mf4_momentum_5d=float("nan"),
        mf5_atr_ratio=float("nan"),
    )
    assert out["market_trend_strength"] == 50.0
    assert out["market_bollinger_position"] == 50.0
    assert out["market_volume_state"] == 50.0
    assert out["market_5day_momentum"] == 50.0
    assert out["market_volatility_suppression"] == 50.0


def test_compose_green_yellow_red_from_known_raw():
    green = MarketFactors.score_from_raw(0.01, 0.9, 1.0, 0.05, 0.005)
    assert green["market_risk_level"] == "green"
    assert green["position_scale_factor"] == 1.0
    assert green["allow_new_open"] is True
    yellow = MarketFactors.score_from_raw(0.0, 0.5, 1.0, 0.0, 0.02)
    assert yellow["market_risk_level"] in {"green", "yellow"}
    red = MarketFactors.score_from_raw(-0.02, 0.05, 0.2, -0.08, 0.12)
    assert red["market_risk_level"] == "red"
    assert red["allow_new_open"] is False
    assert red["position_scale_factor"] == 0.0


def test_detect_style_empty_and_missing_close_are_neutral():
    empty = MarketFactors.detect_style(None)
    assert empty["regime"] == "neutral"
    assert empty["momentum_weight"] == 0.5
    assert empty["reversal_weight"] == 0.5
    no_close = MarketFactors.detect_style(pd.DataFrame({"open": [1.0, 2.0]}))
    assert no_close["regime"] == "neutral"


def test_detect_style_trending_vs_alternating():
    n = 40
    # 收益率严格递增 → 排名自相关为正 → 动量
    r = 0.001 * np.arange(1, n)
    trend_close = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + r]))
    mom = MarketFactors.detect_style(make_ohlcv(len(trend_close), close=trend_close))
    assert mom["regime"] == "momentum"
    # 正负交替 → 排名自相关为负 → 反转
    alt_close = np.where(np.arange(n) % 2 == 0, 100.0, 90.0)
    rev = MarketFactors.detect_style(make_ohlcv(n, close=alt_close))
    assert rev["regime"] == "reversal"
    assert abs(mom["momentum_weight"] + mom["reversal_weight"] - 1.0) < 1e-9


def test_calculate_market_sentiment_on_hs300(hs300_ohlcv):
    asof = hs300_ohlcv[hs300_ohlcv["trade_date"] <= "2024-01-02"]
    assert len(asof) >= 31
    out = MarketFactors.calculate_market_sentiment(
        asof, index_name="沪深300", trade_date="2024-01-02"
    )
    assert out["market_risk_level"] in {"green", "yellow", "red"}
    assert 0.0 <= out["market_composite_score"] <= 100.0
    assert out["position_scale_factor"] in {0.0, 0.4, 1.0}
    assert out["allow_new_open"] == (out["market_risk_level"] != "red")


def test_score_from_raw_matches_dumped_market(factor_raw):
    m = factor_raw["market"]
    out = MarketFactors.score_from_raw(
        mf1_slope_pct=float(m["mf1_slope_pct"]),
        mf2_boll_pct=float(m["mf2_boll_pct"]),
        mf3_vol_ratio=float(m["mf3_vol_ratio"]),
        mf4_momentum_5d=float(m["mf4_momentum_5d"]),
        mf5_atr_ratio=float(m["mf5_atr_ratio"]),
    )
    assert out["market_risk_level"] == "yellow"
    assert out["position_scale_factor"] == 0.4
    assert out["allow_new_open"] is True


def test_detect_on_hs300_around_jan2024(hs300_ohlcv):
    out = MarketFactors.detect_style(hs300_ohlcv, trade_date="2024-01-02")
    assert out["regime"] in {"momentum", "reversal", "neutral"}
    assert 0.0 <= out["strength"] <= 1.0
    assert abs(out["momentum_weight"] + out["reversal_weight"] - 1.0) < 1e-9
