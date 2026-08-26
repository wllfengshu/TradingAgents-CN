"""M1：沪深300 真实日线 + 2024-01-02 预计算原始值。"""

from zstock.factor_management.market_factors import MarketFactors


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


def test_insufficient_bars_is_neutral(hs300_ohlcv):
    short = hs300_ohlcv.head(11)
    out = MarketFactors.calculate_market_sentiment(short, index_name="沪深300")
    assert out["market_risk_level"] == "yellow"
    assert out["market_composite_score"] == 50.0
    assert out["position_scale_factor"] == 0.4
    assert out["allow_new_open"] is True


def test_score_from_raw_matches_dumped_market(factor_raw):
    m = factor_raw["market"]
    out = MarketFactors.score_from_raw(
        mf1_slope_pct=float(m["mf1_slope_pct"]),
        mf2_boll_pct=float(m["mf2_boll_pct"]),
        mf3_vol_ratio=float(m["mf3_vol_ratio"]),
        mf4_momentum_5d=float(m["mf4_momentum_5d"]),
        mf5_atr_ratio=float(m["mf5_atr_ratio"]),
    )
    assert out["market_risk_level"] in {"green", "yellow", "red"}
    # 2024-01-02 策略截面 attrs 为 yellow，预计算打分应同档
    assert out["market_risk_level"] == "yellow"
    assert out["position_scale_factor"] == 0.4
    assert out["allow_new_open"] is True


def test_missing_column_is_neutral(hs300_ohlcv):
    df = hs300_ohlcv.drop(columns=["volume"])
    out = MarketFactors.calculate_market_sentiment(df)
    assert out["market_risk_level"] == "yellow"
    assert out["market_composite_score"] == 50.0
