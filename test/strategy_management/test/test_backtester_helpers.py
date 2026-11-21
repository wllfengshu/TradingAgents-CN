"""backtester：配置加载、结果摘要/导出、OHLCV helpers、signal provider。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from zstock.strategy_management.script import backtester as bt_mod
from zstock.strategy_management.script.backtester import (
    BacktestResult,
    Backtester,
    _append_book_bars,
    _slim_ohlcv_frame,
    load_runtime_config,
    make_ohlcv_provider_from_dict,
    make_signal_provider_from_pipeline_data,
)
from zstock.strategy_management.market_book import BOOK_OHLCV_CODES, INDEX_CODE


def test_load_runtime_config_paths(strategy_params):
    base = load_runtime_config()
    assert "portfolio_optimization" in base
    partial = load_runtime_config({"turnover_control": {"buffer_threshold": 0.99}})
    assert partial["turnover_control"]["buffer_threshold"] == 0.99
    full = load_runtime_config(strategy_params)
    assert "portfolio" in full or "portfolio_optimization" in full


def test_backtest_result_summary_export_plot(tmp_path):
    idx = pd.Index(["2024-01-02", "2024-01-03", "2024-01-04"], name="date")
    eq = pd.Series([1.0, 1.01, 0.99], index=idx)
    rets = pd.Series([0.0, 0.01, -0.0198], index=idx)
    dd = pd.Series([0.0, 0.0, -0.02], index=idx)
    tov = pd.Series([0.0, 0.1, 0.0], index=idx)
    cost = pd.Series([0.0, 0.001, 0.0], index=idx)
    result = BacktestResult(
        equity_curve=eq,
        daily_returns=rets,
        drawdown_curve=dd,
        turnover_series=tov,
        cost_series=cost,
        holdings_log=[
            {
                "trade_date": "2024-01-03",
                "holdings": [{"code": "600000", "weight": 0.5}],
            }
        ],
        trades=[{"trade_date": "2024-01-03", "code": "600000", "side": "buy", "weight": 0.5}],
        metrics={
            "total_return": -0.01,
            "annualized_return": -0.5,
            "sharpe": -0.2,
            "max_drawdown": -0.02,
            "win_rate": 0.5,
            "rebalance_count": 1,
            "avg_turnover": 0.1,
            "total_cost": 0.001,
        },
        config_snapshot={
            "initial_capital": 1e6,
            "rebalance_freq": 5,
            "fee_rate": 0.0015,
            "min_holdings": 1,
            "max_holdings": 5,
            "max_weight_per_stock": 0.2,
            "weighting": "score",
            "buffer_threshold": 0.25,
        },
    )
    text = result.summary()
    assert "回测结果摘要" in text
    assert "累计收益" in text

    paths = result.export_csv(str(tmp_path))
    assert "curve" in paths
    assert Path(paths["curve"]).is_file()
    assert "trades" in paths
    assert "holdings" in paths

    plot_path = result.plot(output_path=str(tmp_path / "bt.png"), show=False)
    assert plot_path and Path(plot_path).is_file()


def test_slim_ohlcv_and_append_book_bars():
    df = pd.DataFrame(
        {
            "trade_date": ["2024-01-02"],
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.05],
            "volume": [100],
            "extra_col": ["drop_me"],
        }
    )
    slim = _slim_ohlcv_frame(df)
    assert "extra_col" not in slim.columns
    assert slim["close"].dtype == "float32"

    index_rows: list = []
    asset_hist: dict = {}
    day = {
        INDEX_CODE: pd.DataFrame(
            {
                "close": [3500.0],
                "high": [3510.0],
                "low": [3490.0],
                "volume": [1e9],
            }
        ),
        "511260": pd.DataFrame({"close": [100.0]}),
        "600000": pd.DataFrame({"close": [10.0]}),  # not in BOOK codes → skip
        "bad": pd.DataFrame({"close": ["x"]}),
        "empty": pd.DataFrame(),
    }
    # ensure bond code from BOOK set
    bond = next((c for c in BOOK_OHLCV_CODES if c != INDEX_CODE), "511260")
    day[bond] = pd.DataFrame({"close": [100.5]})
    _append_book_bars(index_rows, asset_hist, day)
    assert INDEX_CODE in asset_hist
    assert len(index_rows) == 1
    assert index_rows[0]["close"] == 3500.0

    _append_book_bars(index_rows, asset_hist, None)
    _append_book_bars(index_rows, asset_hist, {"x": None})


def test_make_ohlcv_provider_skips_bad_frames():
    ohlcv = {
        "600000": pd.DataFrame(
            {"trade_date": ["2024-01-02", "2024-01-03"], "close": [10.0, 10.5], "open": [9.9, 10.0]}
        ),
        "bad": pd.DataFrame({"close": [1.0]}),  # no trade_date
        "empty": pd.DataFrame(),
        "none": None,
    }
    provider = make_ohlcv_provider_from_dict(ohlcv)
    day = provider("2024-01-02")
    assert "600000" in day
    assert "trade_date" in day["600000"].columns
    assert provider("2099-01-01") == {}


def test_make_signal_provider():
    data = {"2024-01-02": {"all_stocks": ["600000"]}}
    provider = make_signal_provider_from_pipeline_data(data)
    assert provider("2024-01-02")["all_stocks"] == ["600000"]
    assert provider("2024-01-03") is None


def test_load_runtime_config_matches_ssot_resolve(strategy_params):
    from zstock.common.config.strategy_config import resolve_runtime_config

    assert load_runtime_config(strategy_params) == resolve_runtime_config(strategy_params)
    partial = {"turnover_control": {"buffer_threshold": 0.5}}
    assert load_runtime_config(partial)["turnover_control"]["buffer_threshold"] == 0.5
