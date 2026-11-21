"""grid_search_real：参数空间 / 校验 / 配置构建 / objective（不跑回测）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from zstock.strategy_management.script.grid_search_real import (
    RealGridSearchOptimizer,
    build_parser,
)


def test_parameter_space_and_baseline():
    core = RealGridSearchOptimizer.get_parameter_space("core")
    wide = RealGridSearchOptimizer.get_parameter_space("wide")
    full = RealGridSearchOptimizer.get_parameter_space("full")
    assert "top_k" in core and "top_k" in wide and "top_k" in full
    assert len(full["top_k"]) >= len(core["top_k"])
    base = RealGridSearchOptimizer.baseline_params()
    assert base["top_k"] >= 1
    assert 0 < base["coop_threshold"] < 1


def test_validate_and_build_config(tmp_path: Path):
    opt = RealGridSearchOptimizer(output_dir=tmp_path, scoring_method="linear")
    good = {
        "top_sectors": 3,
        "top_per_sector": 2,
        "top_k": 5,
        "coop_threshold": 0.02,
        "weight_sector": 0.4,
        "weight_dragon": 0.35,
        "rebalance_freq": 5,
        "buffer_threshold": 0.25,
        "min_hold_days": 3,
        "hard_stop_loss_pct": -0.08,
        "max_weight_per_stock": 0.15,
        "max_sector_exposure": 0.35,
    }
    ok, msg = RealGridSearchOptimizer.validate_parameters(good)
    assert ok and msg == ""

    bad_w = dict(good, weight_sector=0.9, weight_dragon=0.2)
    ok2, msg2 = RealGridSearchOptimizer.validate_parameters(bad_w)
    assert not ok2 and "weight_coop" in msg2

    bad_cap = dict(good, max_weight_per_stock=0.5, max_sector_exposure=0.3)
    ok3, _ = RealGridSearchOptimizer.validate_parameters(bad_cap)
    assert not ok3

    bad_k = dict(good, top_k=1, top_per_sector=3)
    ok4, _ = RealGridSearchOptimizer.validate_parameters(bad_k)
    assert not ok4

    cfg = opt._build_factor_config(good)
    assert cfg["final_score"]["top_k"] == 5
    assert cfg["sector_layer"]["top_sectors"] == 3
    assert abs(cfg["final_score"]["weights"]["cooperative"] - 0.25) < 1e-9
    from zstock.common.config.strategy_config import build_runtime_config

    rt = build_runtime_config(cfg)
    assert rt["portfolio_optimization"]["max_holdings"] == 5


def test_objective_penalties():
    high = RealGridSearchOptimizer.objective(
        {"sharpe": 2.0, "calmar": 1.5, "max_drawdown": -0.05, "total_return": 0.2, "annualized_return": 0.3}
    )
    deep_dd = RealGridSearchOptimizer.objective(
        {"sharpe": 2.0, "calmar": 1.5, "max_drawdown": -0.30, "total_return": 0.2, "annualized_return": 0.3}
    )
    neg = RealGridSearchOptimizer.objective(
        {"sharpe": 1.0, "calmar": 0.5, "max_drawdown": -0.05, "total_return": -0.2, "annualized_return": -0.1}
    )
    assert high > deep_dd
    assert high > neg


def test_build_parser():
    p = build_parser()
    args = p.parse_args(["--space", "core", "--max-combinations", "10"])
    assert args.space == "core"
    assert args.max_combinations == 10
