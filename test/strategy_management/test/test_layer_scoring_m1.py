"""M1 灯：收盘相对 120 日均线。风格分类仍在本文件后半。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zstock.strategy_management.sentiment import (
    detect_style,
    score_market_from_closes,
    score_market_from_doc,
    score_market_from_gap,
    score_market_sentiment,
)
from test.factor_management.test.conftest import make_ohlcv


def _rising(n: int = 160, step: float = 1.0) -> pd.DataFrame:
    close = 100.0 + step * np.arange(n, dtype=float)
    return make_ohlcv(n, close=close, volume=1_000_000.0)


def test_insufficient_bars_is_neutral():
    short = make_ohlcv(11, close=100.0)
    out = score_market_sentiment(short, index_name="沪深300")
    assert out["market_risk_level"] == "yellow"
    assert out["market_composite_score"] == 50.0
    assert out["allow_new_open"] is True
    assert out["position_scale_factor"] == 0.4
    assert out["detail"]["reason"] == "data_insufficient"


def test_missing_close_is_neutral():
    df = _rising().drop(columns=["close"])
    out = score_market_sentiment(df)
    assert out["market_risk_level"] == "yellow"
    assert out["detail"]["reason"] == "missing_col_close"


def test_trend_gap_matches_close_over_ma120():
    df = _rising()
    close = df["close"].to_numpy()
    expected = float(close[-1] / close[-120:].mean() - 1.0)
    out = score_market_sentiment(df)
    assert out["detail"]["trend_gap"] == pytest.approx(expected)
    assert out["trend_gap"] == pytest.approx(expected)
    assert expected > 0.03
    assert out["market_risk_level"] == "green"
    assert out["position_scale_factor"] == 1.0
    assert out["allow_new_open"] is True


def test_flat_market_is_yellow():
    df = make_ohlcv(160, close=100.0, volume=1_000_000.0)
    out = score_market_sentiment(df)
    assert out["trend_gap"] == pytest.approx(0.0)
    assert out["market_risk_level"] == "yellow"
    assert out["market_composite_score"] == pytest.approx(50.0)
    assert out["position_scale_factor"] == 0.4


def test_decline_is_red():
    close = 200.0 - np.arange(160, dtype=float)
    df = make_ohlcv(160, close=close, volume=1_000_000.0)
    out = score_market_sentiment(df)
    assert out["trend_gap"] < -0.03
    assert out["market_risk_level"] == "red"
    assert out["allow_new_open"] is False
    assert out["position_scale_factor"] == 0.0


def test_gap_band_edges():
    assert score_market_from_gap(0.03)["market_risk_level"] == "green"
    assert score_market_from_gap(0.029)["market_risk_level"] == "yellow"
    assert score_market_from_gap(-0.03)["market_risk_level"] == "red"
    assert score_market_from_gap(-0.029)["market_risk_level"] == "yellow"
    nan = score_market_from_gap(float("nan"))
    assert nan["market_risk_level"] == "yellow"
    assert nan["data_missing"] is False


def test_score_from_closes_uses_last_window():
    closes = [100.0] * 119 + [103.0]
    out = score_market_from_closes(closes)
    ma = (100.0 * 119 + 103.0) / 120.0
    assert out["trend_gap"] == pytest.approx(103.0 / ma - 1.0)
    short = score_market_from_closes([100.0] * 10)
    assert short["market_risk_level"] == "yellow"
    assert short["detail"]["reason"] == "data_insufficient"


def test_score_from_doc_matches_gap():
    live = score_market_from_gap(0.05)
    doc = score_market_from_doc({"trend_gap": 0.05})
    assert doc["market_risk_level"] == live["market_risk_level"]
    assert doc["market_composite_score"] == pytest.approx(live["market_composite_score"])
    missing = score_market_from_doc({})
    assert missing["data_missing"] is True
    assert missing["market_risk_level"] == "yellow"


def test_replay_from_detail_matches_live():
    df = _rising(step=0.2)
    live = score_market_sentiment(df)
    replay = score_market_from_gap(float(live["detail"]["trend_gap"]))
    assert replay["market_risk_level"] == live["market_risk_level"]
    assert replay["market_composite_score"] == pytest.approx(live["market_composite_score"])


def test_light_holds_until_price_returns_to_average():
    # 刚越过 3% 亮绿灯。下一天仍在均线上方、但不到 3%，灯保持。
    up = [100.0] * 119 + [104.0]
    assert score_market_from_closes(up)["market_risk_level"] == "green"
    held = score_market_from_closes(up + [101.5])
    assert held["trend_gap"] < 0.03
    assert held["market_risk_level"] == "green"
    # 回到均线下方，绿灯灭，还没到 -3%，是黄灯。
    back = score_market_from_closes(up + [101.5, 99.0])
    assert back["trend_gap"] < 0
    assert back["market_risk_level"] == "yellow"

    down = [100.0] * 119 + [96.0]
    assert score_market_from_closes(down)["market_risk_level"] == "red"
    held_red = score_market_from_closes(down + [99.0])
    assert held_red["trend_gap"] < 0
    assert held_red["trend_gap"] > -0.03
    assert held_red["market_risk_level"] == "red"
    released = score_market_from_closes(down + [99.0, 102.0])
    assert released["trend_gap"] > 0
    assert released["market_risk_level"] == "yellow"


def test_detect_style_empty_and_missing_close_are_neutral():
    empty = detect_style(None)
    assert empty["regime"] == "neutral"
    assert empty["momentum_weight"] == 0.5
    assert empty["reversal_weight"] == 0.5
    no_close = detect_style(pd.DataFrame({"open": [1.0, 2.0]}))
    assert no_close["regime"] == "neutral"


def test_detect_style_trending_vs_alternating():
    n = 40
    r = 0.001 * np.arange(1, n)
    trend_close = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + r]))
    mom = detect_style(make_ohlcv(len(trend_close), close=trend_close))
    assert mom["regime"] == "momentum"
    alt_close = np.where(np.arange(n) % 2 == 0, 100.0, 90.0)
    rev = detect_style(make_ohlcv(n, close=alt_close))
    assert rev["regime"] == "reversal"
    assert abs(mom["momentum_weight"] + mom["reversal_weight"] - 1.0) < 1e-9


def test_calculate_market_sentiment_on_hs300(hs300_ohlcv):
    asof = hs300_ohlcv[hs300_ohlcv["trade_date"] <= "2024-01-02"]
    out = score_market_sentiment(
        asof, index_name="沪深300", trade_date="2024-01-02"
    )
    if len(asof) < 120:
        assert out["market_risk_level"] == "yellow"
        assert "reason" in out["detail"]
        return
    assert out["market_risk_level"] in {"green", "yellow", "red"}
    assert 0.0 <= out["market_composite_score"] <= 100.0
    assert out["position_scale_factor"] in {0.0, 0.4, 1.0}
    assert out["allow_new_open"] == (out["market_risk_level"] != "red")


def test_detect_on_hs300_around_jan2024(hs300_ohlcv):
    out = detect_style(hs300_ohlcv, trade_date="2024-01-02")
    assert out["regime"] in {"momentum", "reversal", "neutral"}
    assert 0.0 <= out["strength"] <= 1.0
    assert abs(out["momentum_weight"] + out["reversal_weight"] - 1.0) < 1e-9
