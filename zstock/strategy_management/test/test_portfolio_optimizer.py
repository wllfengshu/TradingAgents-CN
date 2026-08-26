"""组合优化：用 Mongo 导出的 2024-01-02 真实截面，约束来自 strategy_params。"""

import numpy as np
import pandas as pd
import pytest

from zstock.strategy_management.portfolio_optimizer import PortfolioOptimizer


def test_empty_signals_fail():
    opt = PortfolioOptimizer()
    out = opt.optimize_portfolio(pd.DataFrame())
    assert out["status"] == "failed"
    assert out["reason"] == "empty signals"


def test_score_weighting_respects_production_cap(real_signals, strategy_params):
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    top_k = int(strategy_params["final_score"]["top_k"])
    opt = PortfolioOptimizer()
    out = opt.optimize_portfolio(
        real_signals,
        min_holdings=max(1, top_k - 2),
        max_holdings=top_k,
        max_weight_per_stock=cap,
        weighting="score",
    )
    assert out["status"] == "success"
    hdf = out["holdings_df"]
    assert set(hdf["code"]) <= set(real_signals["code"].astype(str))
    assert float(hdf["weight"].max()) <= cap + 1e-9
    assert float(hdf["weight"].sum()) <= 1.0 + 1e-9
    # 分数更高的票权重不应更低
    ranked = hdf.sort_values("score", ascending=False)
    weights = ranked["weight"].astype(float).tolist()
    assert weights == sorted(weights, reverse=True)


def test_equal_weight_hits_cap_cash_when_n_times_cap_lt_one(real_signals, strategy_params):
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    n = min(len(real_signals), int(strategy_params["final_score"]["top_k"]))
    opt = PortfolioOptimizer()
    out = opt.optimize_portfolio(
        real_signals,
        min_holdings=1,
        max_holdings=n,
        max_weight_per_stock=cap,
        weighting="equal",
    )
    assert out["status"] == "success"
    invested = float(out["invested_weight"])
    expected = min(1.0, n * cap)
    assert invested == pytest.approx(expected, abs=1e-9)
    assert float(out["max_weight_actual"]) <= cap + 1e-9


def test_softmax_differs_from_equal_on_unequal_scores(real_signals, strategy_params):
    scores = real_signals["final_score"].astype(float)
    if scores.nunique() < 2:
        pytest.skip("该日截面分数无差异，无法区分 softmax 与等权")
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    opt = PortfolioOptimizer()
    soft = opt.optimize_portfolio(real_signals, min_holdings=1, max_holdings=5, max_weight_per_stock=cap, weighting="softmax")
    eq = opt.optimize_portfolio(real_signals, min_holdings=1, max_holdings=5, max_weight_per_stock=cap, weighting="equal")
    assert soft["status"] == eq["status"] == "success"
    assert not np.allclose(soft["weights"], eq["weights"])


def test_strategy_signal_score_alias(real_signals, strategy_params):
    df = real_signals.drop(columns=["final_score"])
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    out = PortfolioOptimizer().optimize_portfolio(df, min_holdings=1, max_holdings=5, max_weight_per_stock=cap)
    assert out["status"] == "success"
    assert "score" in out["holdings_df"].columns


def test_missing_score_column_defaults_zero(real_signals, strategy_params):
    df = real_signals.drop(columns=["final_score", "strategy_signal_score"], errors="ignore")
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    out = PortfolioOptimizer().optimize_portfolio(df, min_holdings=1, max_holdings=5, max_weight_per_stock=cap)
    assert out["status"] == "success"
    assert float(out["holdings_df"]["score"].max()) == 0.0


def test_min_holdings_warning_when_candidates_short(real_signals, strategy_params):
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    out = PortfolioOptimizer().optimize_portfolio(
        real_signals, min_holdings=20, max_holdings=5, max_weight_per_stock=cap
    )
    assert out["status"] == "success"
    assert out["n_holdings"] == min(len(real_signals), 5)


def test_zero_cap_fails_all_weights(real_signals):
    out = PortfolioOptimizer().optimize_portfolio(
        real_signals, min_holdings=1, max_holdings=5, max_weight_per_stock=0.0
    )
    assert out["status"] == "failed"
    assert out["reason"] == "all weights zero after cap"


def test_max_holdings_zero_fails(real_signals):
    out = PortfolioOptimizer().optimize_portfolio(real_signals, min_holdings=1, max_holdings=0)
    assert out["status"] == "failed"


def test_uncapped_renormalize_when_below_cap(real_signals):
    out = PortfolioOptimizer().optimize_portfolio(
        real_signals, min_holdings=1, max_holdings=5, max_weight_per_stock=0.5, weighting="equal"
    )
    assert out["status"] == "success"
    assert float(out["invested_weight"]) == pytest.approx(1.0)
