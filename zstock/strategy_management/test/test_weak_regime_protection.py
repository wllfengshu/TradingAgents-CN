"""弱段保护：对照正式 weak_regime_protection 与隔夜记录的 2024 回撤。"""

import pandas as pd

from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.strategy_management.script.backtester import Backtester


def test_should_reduce_only_matches_production(strategy_params):
    cfg = strategy_params["weak_regime_protection"]
    assert StrategyPipeline._should_reduce_only(cfg, "reversal", "yellow") is True
    assert StrategyPipeline._should_reduce_only(cfg, "reversal", "green") is False
    assert StrategyPipeline._should_reduce_only(cfg, "neutral", "yellow") is False
    assert StrategyPipeline._should_reduce_only({"enabled": False}, "reversal", "yellow") is False
    assert StrategyPipeline._should_reduce_only(None, "reversal", "yellow") is False


def test_reduce_only_disabled_subflag(strategy_params):
    cfg = {
        "enabled": True,
        "reduce_only": {"enabled": False, "when_regime": ["reversal"], "when_market_grade": ["yellow"]},
    }
    assert StrategyPipeline._should_reduce_only(cfg, "reversal", "yellow") is False


def test_apply_reduce_only_no_current_does_not_open(reversal_yellow_signals):
    empty = StrategyPipeline._apply_reduce_only_filter(reversal_yellow_signals, None)
    assert empty.empty


def test_apply_reduce_only_keeps_intersection(reversal_yellow_signals):
    code = str(reversal_yellow_signals.iloc[0]["code"])
    current = pd.DataFrame([{"code": code, "weight": 0.2, "score": 40.0}])
    kept = StrategyPipeline._apply_reduce_only_filter(
        reversal_yellow_signals.rename(columns={"final_score": "score"}), current
    )
    assert set(kept["code"].astype(str)) == {code}

    cleared = StrategyPipeline._apply_reduce_only_filter(
        reversal_yellow_signals, current, force_exit_codes={code}
    )
    assert cleared.empty


def test_apply_reduce_only_empty_target_keeps_old(reversal_yellow_signals):
    code = str(reversal_yellow_signals.iloc[0]["code"])
    current = pd.DataFrame([{"code": code, "weight": 0.2, "score": 40.0}])
    kept = StrategyPipeline._apply_reduce_only_filter(pd.DataFrame(), current)
    assert list(kept["code"]) == [code]


def test_drawdown_scale_idle_paths(strategy_params):
    cfg = strategy_params["weak_regime_protection"]
    assert Backtester._compute_drawdown_scale([], cfg) == 1.0
    assert Backtester._compute_drawdown_scale([1.0], {"enabled": False}) == 1.0
    disabled = {"enabled": True, "drawdown_throttle": {"enabled": False}}
    assert Backtester._compute_drawdown_scale([1.0, 0.5], disabled) == 1.0
    assert Backtester._compute_drawdown_scale([0.0, 0.0], cfg) == 1.0


def test_drawdown_scale_uses_recorded_2024_mdd(strategy_params, overnight_results):
    """2024 全年记录 MDD≈-11.0%，超过 10% 阈值 → scale_factor=0.7。"""
    cfg = strategy_params["weak_regime_protection"]
    mdd = float(overnight_results["current_2024"]["max_drawdown"])
    assert mdd <= -0.10
    scale = Backtester._compute_drawdown_scale([1.0, 1.0 + mdd], cfg)
    assert scale == 0.7

    q1_final = 1.1360  # segments/current_2024_q1/summary.txt 最终净值
    assert Backtester._compute_drawdown_scale([1.0, q1_final], cfg) == 1.0
