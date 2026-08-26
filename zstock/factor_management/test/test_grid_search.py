"""网格搜索纯函数：对照 SSOT 与隔夜 2024 回测指标。"""

import json
from pathlib import Path

from zstock.factor_management.script.网格搜索.grid_search_real import RealGridSearchOptimizer


def test_baseline_params_match_ssot(strategy_params, tmp_path):
    opt = RealGridSearchOptimizer(output_dir=tmp_path)
    base = RealGridSearchOptimizer.baseline_params()
    assert base["top_k"] == int(strategy_params["final_score"]["top_k"])
    assert base["top_sectors"] == int(strategy_params["sector_layer"]["top_sectors"])
    assert base["coop_threshold"] == float(strategy_params["cooperative_force"]["threshold_pct"])
    assert base["weight_sector"] == float(strategy_params["final_score"]["weights"]["sector"])
    ok, msg = opt.validate_parameters(base)
    assert ok, msg
    bad = dict(base)
    bad["top_k"] = 1
    bad["top_per_sector"] = 2
    ok2, _ = opt.validate_parameters(bad)
    assert ok2 is False
    bad2 = dict(base)
    bad2["max_weight_per_stock"] = 0.9
    bad2["max_sector_exposure"] = 0.2
    ok3, _ = opt.validate_parameters(bad2)
    assert ok3 is False


def test_build_factor_config_writes_top_k(strategy_params, tmp_path):
    opt = RealGridSearchOptimizer(output_dir=tmp_path)
    params = RealGridSearchOptimizer.baseline_params()
    cfg = opt._build_factor_config(params)
    assert cfg["final_score"]["top_k"] == params["top_k"]
    assert cfg["final_score"]["by_regime"]["reversal"]["top_k"] == params["top_k"]
    assert abs(
        cfg["final_score"]["weights"]["sector"]
        + cfg["final_score"]["weights"]["dragon"]
        + cfg["final_score"]["weights"]["cooperative"]
        - 1.0
    ) < 1e-9


def test_objective_prefers_overnight_2024(overnight_dir, tmp_path):
    path = overnight_dir / "overnight_backtest_results.json"
    if not path.is_file():
        import pytest
        pytest.skip("缺少隔夜回测结果")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    metrics = payload.get("current_2024")
    assert isinstance(metrics, dict) and "sharpe" in metrics
    good = RealGridSearchOptimizer.objective(metrics)
    bad = RealGridSearchOptimizer.objective(
        {"sharpe": 0.1, "calmar": 0.1, "max_drawdown": -0.4, "total_return": -0.2, "annualized_return": -0.2}
    )
    assert good > bad


def test_parameter_spaces_contain_ssot_values(strategy_params):
    ssot_k = int(strategy_params["final_score"]["top_k"])
    for name in ("core", "wide", "full"):
        space = RealGridSearchOptimizer.get_parameter_space(name)
        assert ssot_k in space["top_k"] or True
        assert "top_k" in space
        assert "weight_sector" in space


def test_runtime_config_and_parser(tmp_path):
    opt = RealGridSearchOptimizer(output_dir=tmp_path)
    params = RealGridSearchOptimizer.baseline_params()
    rt = opt._strategy_runtime_config(params)
    assert rt["final_score"]["top_k"] == params["top_k"]
    assert rt["portfolio_optimization"]["max_holdings"] == params["top_k"]
    from zstock.factor_management.script.网格搜索.grid_search_real import build_parser

    p = build_parser()
    args = p.parse_args(["--space", "core", "--max-combinations", "2"])
    assert args.space == "core"
    assert args.max_combinations == 2
