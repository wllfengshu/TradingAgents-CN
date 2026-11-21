"""overnight_backtest.async_main：跳过路径 + 网格/敏感性 stub（不跑真实回测）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from zstock.strategy_management.script import overnight_backtest as ob


def _args(tmp_path, extra=None):
    base = [
        "--output",
        str(tmp_path),
        "--end-date",
        "2024-06-30",
        "--skip-precompute",
        "--skip-eval",
        "--skip-grid",
        "--skip-sensitivity",
        "--no-quarters",
        "--year",
        "2024",
    ]
    if extra:
        base.extend(extra)
    return ob.build_parser().parse_args(base)


@pytest.mark.asyncio
async def test_async_main_year_scope_minimal(tmp_path, monkeypatch):
    args = _args(tmp_path)

    async def _ok_init():
        return None

    async def _ok_close():
        return None

    async def _preflight():
        return {"ok": True, "mongo": "ok", "issues": []}

    async def _coverage(periods):
        return {"by_year": {"2024": {"market_days": 10}}}

    async def _ohlcv(start, end):
        return {
            "399300": pd.DataFrame(
                {"trade_date": ["2024-01-02", "2024-01-03"], "close": [3500.0, 3510.0]}
            ),
            "600000": pd.DataFrame(
                {"trade_date": ["2024-01-02", "2024-01-03"], "close": [10.0, 10.1]}
            ),
        }

    async def _bt(*a, **k):
        return {
            "status": "success",
            "total_return": 0.1,
            "annualized_return": 0.08,
            "sharpe": 1.0,
            "calmar": 0.5,
            "max_drawdown": -0.05,
            "avg_turnover": 0.1,
            "avg_exposure": 0.8,
            "start": a[1] if len(a) > 1 else "2024-01-01",
            "end": a[2] if len(a) > 2 else "2024-06-30",
        }

    async def _bench(start, end):
        return {
            "status": "success",
            "total_return": 0.05,
            "annualized_return": 0.04,
            "max_drawdown": -0.03,
            "index": "399300",
            "start": start,
            "end": end,
        }

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _ok_init)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _ok_close)
    monkeypatch.setattr(ob, "preflight_checks", _preflight)
    monkeypatch.setattr(ob, "backup_strategy_params", lambda d: d / "backup.json")
    monkeypatch.setattr(ob, "check_factor_coverage_by_year", _coverage)
    monkeypatch.setattr(
        "zstock.strategy_management.script.grid_search_real.load_ohlcv",
        _ohlcv,
    )
    monkeypatch.setattr(ob, "run_backtest_current", _bt)
    monkeypatch.setattr(ob, "compute_benchmark", _bench)

    rc = await ob.async_main(args)
    assert rc == 0
    assert (tmp_path / "overnight_backtest_report.txt").is_file()
    assert (tmp_path / "overnight_backtest_results.json").is_file()
    assert (tmp_path / "comparison.csv").is_file()


@pytest.mark.asyncio
async def test_async_main_empty_ohlcv_exits(tmp_path, monkeypatch):
    args = _args(tmp_path)

    async def _ok():
        return None

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _ok)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _ok)
    monkeypatch.setattr(ob, "preflight_checks", AsyncMock(return_value={"ok": True, "issues": []}))
    monkeypatch.setattr(ob, "backup_strategy_params", lambda d: d)
    monkeypatch.setattr(
        ob, "check_factor_coverage_by_year", AsyncMock(return_value={"by_year": {}})
    )
    monkeypatch.setattr(
        "zstock.strategy_management.script.grid_search_real.load_ohlcv",
        AsyncMock(return_value={}),
    )
    assert await ob.async_main(args) == 1


@pytest.mark.asyncio
async def test_async_main_with_precompute_eval_grid(tmp_path, monkeypatch):
    """走非 year_scope 全量开关：预计算/测评/网格/敏感性均 mock。"""
    args = ob.build_parser().parse_args(
        [
            "--output",
            str(tmp_path),
            "--end-date",
            "2026-08-24",
            "--max-combinations",
            "1",
            "--space",
            "core",
            "--grid-workers",
            "1",
            "--no-quarters",
        ]
    )

    async def _ok():
        return None

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _ok)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _ok)
    monkeypatch.setattr(ob, "preflight_checks", AsyncMock(return_value={"ok": True, "issues": []}))
    monkeypatch.setattr(ob, "backup_strategy_params", lambda d: d / "b.json")
    monkeypatch.setattr(
        ob,
        "run_precompute",
        AsyncMock(return_value={"days": 1, "ok": 1}),
    )
    monkeypatch.setattr(
        ob,
        "check_factor_coverage_by_year",
        AsyncMock(return_value={"by_year": {"2024": {"market_days": 1}}}),
    )
    monkeypatch.setattr(
        ob,
        "run_factor_eval",
        AsyncMock(return_value=str(tmp_path / "summary.csv")),
    )
    (tmp_path / "summary.csv").write_text("factor,ic\nx,0.1\n", encoding="utf-8")

    ohlcv = {
        "399300": pd.DataFrame({"trade_date": ["2024-01-02"], "close": [1.0]}),
        "600000": pd.DataFrame({"trade_date": ["2024-01-02"], "close": [1.0]}),
    }
    monkeypatch.setattr(
        "zstock.strategy_management.script.grid_search_real.load_ohlcv",
        AsyncMock(return_value=ohlcv),
    )

    async def _bt(*a, **k):
        return {
            "status": "success",
            "total_return": 0.1,
            "annualized_return": 0.08,
            "sharpe": 1.2,
            "calmar": 0.8,
            "max_drawdown": -0.05,
            "avg_turnover": 0.1,
            "avg_exposure": 0.9,
            "start": "2024-01-01",
            "end": "2024-12-31",
        }

    monkeypatch.setattr(ob, "run_backtest_current", _bt)
    monkeypatch.setattr(
        ob,
        "compute_benchmark",
        AsyncMock(
            return_value={
                "status": "success",
                "total_return": 0.05,
                "annualized_return": 0.04,
                "max_drawdown": -0.02,
            }
        ),
    )

    from zstock.strategy_management.script.grid_search_real import RealGridSearchOptimizer

    base = RealGridSearchOptimizer.baseline_params()

    async def _grid(self, *a, **k):
        return pd.DataFrame(
            [
                {
                    **base,
                    "status": "success",
                    "objective": 2.0,
                    "total_return": 0.2,
                    "annualized_return": 0.15,
                    "max_drawdown": -0.08,
                    "sharpe": 1.5,
                    "calmar": 1.0,
                    "avg_turnover": 0.1,
                    "total_cost": 0.01,
                    "weight_coop": 0.25,
                }
            ]
        )

    monkeypatch.setattr(RealGridSearchOptimizer, "run_grid_search", _grid)
    monkeypatch.setattr(
        RealGridSearchOptimizer,
        "save_results",
        lambda self, df: tmp_path / "grid.csv",
    )
    monkeypatch.setattr(
        ob,
        "run_backtest_grid",
        AsyncMock(
            return_value={
                "status": "success",
                "total_return": 0.05,
                "sharpe": 0.8,
                "label": "oos",
            }
        ),
    )

    rc = await ob.async_main(args)
    assert rc == 0
    assert (tmp_path / "overnight_backtest_report.txt").is_file()


@pytest.mark.asyncio
async def test_async_main_pipeline_exception(tmp_path, monkeypatch):
    args = _args(tmp_path)

    async def _ok():
        return None

    async def _boom():
        raise RuntimeError("preflight boom")

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _ok)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _ok)
    monkeypatch.setattr(ob, "preflight_checks", _boom)
    rc = await ob.async_main(args)
    assert rc == 1
