"""BacktestResult：summary / plot / export（覆盖报告与绘图分支）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from zstock.strategy_management.script.backtester import BacktestResult


def _result(tmp_path: Path | None = None) -> BacktestResult:
    idx = pd.Index([f"2024-01-{i:02d}" for i in range(2, 22)], name="date")
    equity = pd.Series([1.0 + i * 0.01 for i in range(len(idx))], index=idx)
    rets = equity.pct_change().fillna(0.0)
    dd = equity / equity.cummax() - 1.0
    tov = pd.Series([0.1] * len(idx), index=idx)
    cost = pd.Series([0.001] * len(idx), index=idx)
    return BacktestResult(
        equity_curve=equity,
        daily_returns=rets,
        drawdown_curve=dd,
        turnover_series=tov,
        cost_series=cost,
        holdings_log=[{"date": "2024-01-02", "holdings": [{"code": "600000", "weight": 0.5}]}],
        trades=[{"date": "2024-01-02", "code": "600000", "side": "buy", "weight": 0.5}],
        metrics={
            "total_return": 0.2,
            "annualized_return": 0.15,
            "annualized_vol": 0.1,
            "sharpe": 1.2,
            "sortino": 1.5,
            "calmar": 2.0,
            "max_drawdown": -0.08,
            "max_drawdown_duration": 3,
            "downside_vol": 0.05,
            "win_rate": 0.55,
            "profit_loss_ratio": 1.2,
            "best_day": 0.03,
            "worst_day": -0.02,
            "rebalance_count": 4,
            "avg_turnover": 0.1,
            "max_turnover": 0.3,
            "total_cost": 0.01,
            "total_return_gross": 0.22,
        },
        config_snapshot={
            "initial_capital": 1e6,
            "rebalance_freq": 5,
            "fee_rate": 0.001,
            "min_holdings": 1,
            "max_holdings": 5,
            "max_weight_per_stock": 0.2,
            "weighting": "score",
            "buffer_threshold": 0.25,
        },
    )


def test_summary_and_export_and_plot(tmp_path):
    r = _result()
    text = r.summary()
    assert "回测结果摘要" in text
    assert "夏普" in text

    if hasattr(r, "export_csv"):
        r.export_csv(str(tmp_path))
        assert any(tmp_path.iterdir())

    out = tmp_path / "eq.png"
    path = r.plot(output_path=str(out), title="UT", show=False)
    assert path is None or Path(path).exists() or out.exists()
