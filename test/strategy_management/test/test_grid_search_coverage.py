"""grid_search_real：参数空间、采样、评分、报告与 CLI（mock 重回测）。"""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from zstock.strategy_management.script import grid_search_real as gs
from zstock.strategy_management.script.grid_search_real import RealGridSearchOptimizer


def test_parameter_spaces_and_validate(tmp_path):
    for name in ("core", "wide", "full"):
        space = RealGridSearchOptimizer.get_parameter_space(name)
        assert "top_k" in space
        assert len(space["top_k"]) >= 2

    opt = RealGridSearchOptimizer(output_dir=tmp_path)
    base = RealGridSearchOptimizer.baseline_params()
    ok, msg = RealGridSearchOptimizer.validate_parameters(base)
    assert ok, msg

    bad_w = dict(base)
    bad_w["weight_sector"] = 0.9
    bad_w["weight_dragon"] = 0.9
    ok2, err = RealGridSearchOptimizer.validate_parameters(bad_w)
    assert not ok2
    assert "weight_coop" in err

    # extreme min_hold vs rebalance still returns True (only pass)
    extreme = dict(base)
    extreme["min_hold_days"] = 100
    extreme["rebalance_freq"] = 1
    ok3, _ = RealGridSearchOptimizer.validate_parameters(extreme)
    assert ok3


def test_objective_branches():
    good = RealGridSearchOptimizer.objective(
        {"sharpe": 1.5, "calmar": 1.0, "max_drawdown": -0.1, "total_return": 0.2, "annualized_return": 0.15}
    )
    deep_dd = RealGridSearchOptimizer.objective(
        {"sharpe": 1.5, "calmar": 1.0, "max_drawdown": -0.3, "total_return": 0.2, "annualized_return": 0.15}
    )
    neg = RealGridSearchOptimizer.objective(
        {"sharpe": 0.5, "calmar": 0.2, "max_drawdown": -0.05, "total_return": -0.1, "annualized_return": -0.1}
    )
    assert good > deep_dd
    assert good > neg
    empty = RealGridSearchOptimizer.objective({})
    assert isinstance(empty, float)


def test_sample_combinations_includes_baseline(tmp_path):
    opt = RealGridSearchOptimizer(output_dir=tmp_path)
    space = RealGridSearchOptimizer.get_parameter_space("core")
    combos = opt.sample_combinations(space, max_combinations=5, seed=1, include_baseline=True)
    assert len(combos) >= 1
    assert len(combos) <= 5
    base = RealGridSearchOptimizer.baseline_params()
    assert combos[0] == base


def test_log_row_and_generate_report(tmp_path, capsys):
    opt = RealGridSearchOptimizer(output_dir=tmp_path)
    RealGridSearchOptimizer._log_row(
        {
            "status": "success",
            "objective": 1.2,
            "total_return": 0.1,
            "max_drawdown": -0.05,
            "sharpe": 1.0,
            "calmar": 0.8,
        }
    )
    RealGridSearchOptimizer._log_row({"status": "failed:boom"})

    empty_report = opt.generate_report(pd.DataFrame())
    assert "无结果" in empty_report

    fail_df = pd.DataFrame([{"status": "failed", "objective": -1e9}])
    assert "无成功回测" in opt.generate_report(fail_df)

    base = RealGridSearchOptimizer.baseline_params()
    row = {
        **base,
        "status": "success",
        "objective": 2.0,
        "total_return": 0.2,
        "annualized_return": 0.15,
        "max_drawdown": -0.08,
        "sharpe": 1.2,
        "calmar": 1.0,
        "avg_turnover": 0.1,
        "total_cost": 0.01,
        "weight_coop": 0.25,
        "rebalance_freq": base["rebalance_freq"],
        "top_k": base["top_k"],
        "hard_stop_loss_pct": base["hard_stop_loss_pct"],
        "max_weight_per_stock": base["max_weight_per_stock"],
    }
    df = pd.DataFrame([row, {**row, "objective": 1.0, "top_k": base["top_k"] + 2}])
    text = opt.generate_report(df)
    assert "最优" in text
    assert "Top 5" in text

    csv = opt.save_results(df)
    assert csv.is_file()
    assert (tmp_path / "best_strategy_params.json").is_file()
    assert (tmp_path / "report.txt").is_file()


@pytest.mark.asyncio
async def test_run_one_invalid_and_mocked_success(tmp_path, monkeypatch):
    opt = RealGridSearchOptimizer(output_dir=tmp_path)
    bad = RealGridSearchOptimizer.baseline_params()
    bad["top_k"] = 1
    bad["top_per_sector"] = 5
    row = await opt.run_one(bad, lambda td: {}, "2024-01-02", "2024-01-10", 1e6, 0.001)
    assert row["status"].startswith("invalid")
    assert row["objective"] == -1e9

    class _Metrics:
        metrics = {
            "total_return": 0.1,
            "annualized_return": 0.08,
            "sharpe": 1.0,
            "calmar": 0.8,
            "max_drawdown": -0.05,
            "annualized_vol": 0.1,
            "avg_turnover": 0.05,
            "total_cost": 0.001,
            "rebalance_count": 2,
        }

    class _BT:
        def __init__(self, **kwargs):
            pass

        async def run(self, **kwargs):
            return _Metrics()

    monkeypatch.setattr(
        "zstock.strategy_management.script.backtester.Backtester",
        _BT,
    )
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.StrategyPipeline",
        lambda **kwargs: MagicMock(),
    )
    good = RealGridSearchOptimizer.baseline_params()
    ok_row = await opt.run_one(good, lambda td: {}, "2024-01-02", "2024-01-10", 1e6, 0.001)
    assert ok_row["status"] == "success"
    assert "objective" in ok_row


@pytest.mark.asyncio
async def test_run_grid_search_sequential_mocked(tmp_path, monkeypatch):
    opt = RealGridSearchOptimizer(output_dir=tmp_path)

    async def _fake_one(params, *a, **k):
        return {**params, "status": "success", "objective": 1.0, "total_return": 0.1,
                "annualized_return": 0.08, "sharpe": 1.0, "calmar": 0.5,
                "max_drawdown": -0.05, "annualized_vol": 0.1, "avg_turnover": 0.1,
                "total_cost": 0.01, "rebalance_count": 1}

    monkeypatch.setattr(opt, "run_one", _fake_one)
    monkeypatch.setattr(
        opt,
        "sample_combinations",
        lambda *a, **k: [RealGridSearchOptimizer.baseline_params()],
    )
    df = await opt.run_grid_search(
        ohlcv_provider=lambda td: {},
        start="2024-01-02",
        end="2024-01-10",
        capital=1e6,
        fee=0.001,
        max_combinations=1,
        space_name="core",
        workers=1,
    )
    assert len(df) == 1
    assert df.iloc[0]["status"] == "success"


@pytest.mark.asyncio
async def test_run_grid_parallel_fallback(tmp_path, monkeypatch):
    opt = RealGridSearchOptimizer(output_dir=tmp_path)

    async def _fake_one(params, *a, **k):
        return {
            **params,
            "status": "success",
            "objective": 0.5,
            "total_return": 0.1,
            "max_drawdown": -0.05,
            "sharpe": 1.0,
            "calmar": 0.5,
        }

    monkeypatch.setattr(opt, "run_one", _fake_one)
    monkeypatch.setattr(
        opt,
        "sample_combinations",
        lambda *a, **k: [RealGridSearchOptimizer.baseline_params()],
    )

    def _boom(*a, **k):
        raise RuntimeError("pool fail")

    monkeypatch.setattr(opt, "_run_parallel", _boom)
    df = await opt.run_grid_search(
        ohlcv_provider=lambda td: {},
        start="2024-01-02",
        end="2024-01-10",
        capital=1e6,
        fee=0.001,
        max_combinations=1,
        space_name="core",
        workers=2,
        ohlcv_data={"600000": pd.DataFrame()},
    )
    assert len(df) == 1


def test_quiet_clear_write_cache_reset(tmp_path, monkeypatch):
    gs._quiet_loggers()
    gs._clear_strategy_caches()
    cache = RealGridSearchOptimizer._write_ohlcv_worker_cache({"a": pd.DataFrame({"x": [1]})})
    assert cache.is_file()
    cache.unlink(missing_ok=True)

    monkeypatch.setattr(
        "zstock.common.utils.db_utils.db_manager",
        MagicMock(mongo_client=1, mongo_db=1),
    )
    import zstock.common.utils.db_utils as db_mod

    monkeypatch.setattr(db_mod, "mongo_client", object(), raising=False)
    monkeypatch.setattr(db_mod, "mongo_db", object(), raising=False)
    import zstock.data_management.query_service as qs_mod

    monkeypatch.setattr(qs_mod, "_data_query_service", object(), raising=False)
    gs._reset_worker_singletons()


@pytest.mark.asyncio
async def test_load_ohlcv_mocked(monkeypatch):
    class _QS:
        async def get_all_stocks(self):
            return (
                [
                    {"code": "600000", "is_mainboard": True, "is_st": False},
                    {"code": "300001", "is_mainboard": False, "is_st": False},
                ],
                None,
            )

        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            return {
                c: pd.DataFrame({"trade_date": ["2024-01-02"], "close": [10.0]})
                for c in codes
            }

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service",
        lambda: _QS(),
    )
    data = await gs.load_ohlcv("2024-01-02", "2024-01-10")
    assert "600000" in data


@pytest.mark.asyncio
async def test_async_main_paths(tmp_path, monkeypatch):
    args = gs.build_parser().parse_args(
        [
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-10",
            "--max-combinations",
            "1",
            "--space",
            "core",
            "--workers",
            "1",
            "--output",
            str(tmp_path),
        ]
    )

    async def _fail_init():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(
        "zstock.common.utils.db_utils.init_zstock_database",
        _fail_init,
    )
    with pytest.raises(RuntimeError):
        await gs.async_main(args)

    async def _ok_init():
        return None

    async def _ok_close():
        return None

    async def _empty_ohlcv(*a, **k):
        return {}

    monkeypatch.setattr(
        "zstock.common.utils.db_utils.init_zstock_database",
        _ok_init,
    )
    monkeypatch.setattr(
        "zstock.common.utils.db_utils.close_zstock_database",
        _ok_close,
    )
    monkeypatch.setattr(gs, "load_ohlcv", _empty_ohlcv)
    assert await gs.async_main(args) == 1

    async def _ohlcv(*a, **k):
        return {"600000": pd.DataFrame({"trade_date": ["2024-01-02"], "close": [1.0]})}

    async def _grid(self, **kwargs):
        base = RealGridSearchOptimizer.baseline_params()
        return pd.DataFrame(
            [
                {
                    **base,
                    "status": "success",
                    "objective": 1.0,
                    "total_return": 0.1,
                    "annualized_return": 0.08,
                    "max_drawdown": -0.05,
                    "sharpe": 1.0,
                    "calmar": 0.5,
                    "avg_turnover": 0.1,
                    "total_cost": 0.01,
                    "weight_coop": 0.25,
                }
            ]
        )

    monkeypatch.setattr(gs, "load_ohlcv", _ohlcv)
    monkeypatch.setattr(RealGridSearchOptimizer, "run_grid_search", _grid)
    assert await gs.async_main(args) == 0
    assert (tmp_path / "grid_search_results.csv").is_file() or list(tmp_path.iterdir())


def test_main_parses_argv(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["grid_search_real", "--space", "core", "--max-combinations", "1", "--workers", "1"],
    )
    called = {}

    async def _fake(args):
        called["space"] = args.space
        return 0

    monkeypatch.setattr(gs, "async_main", _fake)
    assert gs.main() == 0
    assert called["space"] == "core"


def test_init_worker_pool(tmp_path, monkeypatch):
    cache = tmp_path / "ohlcv.pkl"
    import pickle

    with open(cache, "wb") as f:
        pickle.dump({"x": 1}, f)
    monkeypatch.setattr(gs, "_quiet_loggers", lambda: None)
    gs._init_worker_pool(str(cache), "linear")
    assert gs._POOL_OHLCV == {"x": 1}
    assert gs._POOL_SCORING_METHOD == "linear"
