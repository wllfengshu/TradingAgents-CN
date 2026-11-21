"""overnight async_main：非 year_scope，mock 网格/敏感性/回测。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from zstock.strategy_management.script import overnight_backtest as ob
from zstock.strategy_management.script.grid_search_real import RealGridSearchOptimizer


def _ok_bt(*a, **k):
    label = a[7] if len(a) > 7 else k.get("label", "bt")
    return {
        "status": "success",
        "label": label,
        "start": a[1] if len(a) > 1 else "",
        "end": a[2] if len(a) > 2 else "",
        "total_return": 0.1,
        "total_return_gross": 0.11,
        "annualized_return": 0.12,
        "sharpe": 1.0,
        "calmar": 0.8,
        "max_drawdown": -0.05,
        "avg_turnover": 0.1,
        "total_cost": 0.01,
        "rebalance_count": 1,
        "avg_exposure": 0.5,
        "final_equity": 1.1e6,
    }


@pytest.mark.asyncio
async def test_async_main_with_grid_and_sensitivity(tmp_path, monkeypatch):
    # 缩短任务：用很早的 end-date 让部分区间被 clip，但仍非 year_scope
    args = ob.build_parser().parse_args(
        [
            "--end-date",
            "2024-06-30",
            "--skip-precompute",
            "--skip-eval",
            "--no-quarters",
            "--max-combinations",
            "1",
            "--space",
            "core",
            "--grid-workers",
            "1",
            "--output",
            str(tmp_path),
        ]
    )

    async def _ok():
        return None

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _ok)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _ok)
    monkeypatch.setattr(ob, "preflight_checks", AsyncMock(return_value={"ok": True, "issues": []}))
    monkeypatch.setattr(ob, "backup_strategy_params", lambda d: d / "bak.json")
    monkeypatch.setattr(
        ob, "check_factor_coverage_by_year", AsyncMock(return_value={"by_year": {}})
    )

    ohlcv = {"600000": pd.DataFrame({"trade_date": ["2024-01-02"], "close": [10.0]})}

    async def _load(s, e):
        return ohlcv

    monkeypatch.setattr(
        "zstock.strategy_management.script.grid_search_real.load_ohlcv",
        _load,
    )

    async def _bt(*a, **k):
        return _ok_bt(*a, **k)

    monkeypatch.setattr(ob, "run_backtest_current", _bt)
    monkeypatch.setattr(
        ob,
        "compute_benchmark",
        AsyncMock(
            return_value={
                "status": "success",
                "label": "bench",
                "total_return": 0.05,
                "sharpe": 0.4,
                "max_drawdown": -0.1,
            }
        ),
    )

    base = RealGridSearchOptimizer.baseline_params()
    grid_df = pd.DataFrame(
        [
            {
                **base,
                "status": "success",
                "objective": 1.0,
                "total_return": 0.1,
                "sharpe": 1.0,
                "calmar": 0.5,
                "max_drawdown": -0.05,
                "weight_coop": 0.25,
            }
        ]
    )

    async def _grid(self, *a, **k):
        return grid_df

    monkeypatch.setattr(RealGridSearchOptimizer, "run_grid_search", _grid)
    monkeypatch.setattr(
        RealGridSearchOptimizer,
        "save_results",
        lambda self, df: None,
    )

    async def _grid_bt(*a, **k):
        return {
            "status": "success",
            "label": a[-1] if a else "g",
            "total_return": 0.08,
            "sharpe": 0.9,
            "max_drawdown": -0.04,
            "objective": 0.8,
        }

    monkeypatch.setattr(ob, "run_backtest_grid", _grid_bt)

    code = await ob.async_main(args)
    assert code == 0
    assert (tmp_path / "overnight_backtest_report.txt").exists()
    assert (tmp_path / "comparison.csv").exists() or (tmp_path / "overnight_backtest_results.json").exists()
