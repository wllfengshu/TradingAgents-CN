"""趋势退出：require_both 时缺日线不得误触发。"""

import pandas as pd

from zstock.strategy_management.pipeline import StrategyPipeline


def _ohlcv(n=25, last_close=9.0, peak=12.0, start="2023-12-01"):
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d").tolist()
    highs = [10.0] * (n - 2) + [peak, 10.0]
    closes = [10.0] * (n - 1) + [last_close]
    return pd.DataFrame(
        {
            "trade_date": dates,
            "high": highs,
            "close": closes,
            "open": closes,
            "low": [8.0] * n,
        }
    )


BOTH = {
    "enabled": True,
    "require_both": True,
    "bollinger_trend_below": 40.0,
    "no_new_high_lookback": 20,
}


def test_disabled_and_regime_filter():
    assert StrategyPipeline._trend_exit_enabled({"trend_exit": {"enabled": False}}) is False
    assert StrategyPipeline._trend_exit_enabled(
        {"trend_exit": {"enabled": True, "when_regimes": ["momentum"]}},
        regime="reversal",
    ) is False
    assert StrategyPipeline._trend_exit_enabled(
        {"trend_exit": {"enabled": True, "when_regimes": ["momentum"]}},
        regime="momentum",
    ) is True


def test_require_both_without_ohlcv_is_false():
    assert StrategyPipeline._should_trend_exit(
        ohlcv=None,
        trade_date="2024-01-02",
        f35=10.0,
        prev_f35=None,
        trend_cfg=BOTH,
    ) is False
    assert StrategyPipeline._should_trend_exit(
        ohlcv=pd.DataFrame(),
        trade_date="2024-01-02",
        f35=10.0,
        prev_f35=None,
        trend_cfg=BOTH,
    ) is False


def test_require_both_true_when_no_new_high_and_weak_bollinger():
    ohlcv = _ohlcv()
    td = str(ohlcv["trade_date"].iloc[-1])
    assert StrategyPipeline._should_trend_exit(
        ohlcv=ohlcv,
        trade_date=td,
        f35=10.0,
        prev_f35=None,
        trend_cfg=BOTH,
    ) is True
    assert StrategyPipeline._should_trend_exit(
        ohlcv=ohlcv,
        trade_date=td,
        f35=80.0,
        prev_f35=None,
        trend_cfg=BOTH,
    ) is False


def test_or_mode_can_exit_on_bollinger_only():
    cfg = dict(BOTH, require_both=False)
    assert StrategyPipeline._should_trend_exit(
        ohlcv=None,
        trade_date="2024-01-02",
        f35=10.0,
        prev_f35=None,
        trend_cfg=cfg,
    ) is True


def test_force_exit_empty_ohlcv_map_with_require_both():
    rules = {"trend_exit": BOTH}
    out = StrategyPipeline._trend_force_exit_codes(
        {"000001"},
        "2024-01-02",
        rules,
        ohlcv_by_code={},
        f35_by_code={"000001": 10.0},
    )
    assert out == set()
    ohlcv = _ohlcv()
    td = str(ohlcv["trade_date"].iloc[-1])
    out = StrategyPipeline._trend_force_exit_codes(
        {"000001"},
        td,
        rules,
        ohlcv_by_code={"000001": ohlcv},
        f35_by_code={"000001": 10.0},
    )
    assert out == {"000001"}
