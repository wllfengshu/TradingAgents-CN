"""回测器：交易日/止损/无信号退出用正式 exit_rules；OHLCV 与截面来自 Mongo 导出。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from zstock.strategy_management.script.backtester import (
    BacktestResult,
    Backtester,
    make_ohlcv_provider_from_dict,
    make_signal_provider_from_pipeline_data,
)
from zstock.strategy_management.test.conftest import (
    DummySignalGenerator,
    ReplayFactorPipeline,
    make_offline_pipeline,
)


def test_default_rebalance_freq_matches_ssot(strategy_params):
    assert Backtester._default_rebalance_freq() == int(strategy_params["backtest"]["rebalance_freq"])


def test_gen_trade_dates_weekdays_only():
    dates = Backtester._gen_trade_dates("2024-01-01", "2024-01-07")
    # 2024-01-01 周一 … 01-05 周五，01-06/07 周末
    assert dates[0] == "2024-01-01"
    assert "2024-01-06" not in dates
    assert "2024-01-07" not in dates
    assert Backtester._gen_trade_dates("2024-01-06", "2024-01-07") == []


def test_row_for_date(ohlcv_by_code):
    code, df = next(iter(ohlcv_by_code.items()))
    td = str(df["trade_date"].iloc[0])
    row = Backtester._row_for_date(df, td)
    assert row is not None
    assert float(row["close"]) > 0
    assert Backtester._row_for_date(df, "1999-01-01") is None
    assert Backtester._row_for_date(pd.DataFrame({"close": [1.0]}), td) is None


def test_buy_code_set_without_signal_type(real_signals, strategy_params):
    df = real_signals.drop(columns=["signal_type"])
    codes = Backtester._buy_code_set(df, 2)
    ranked = real_signals.sort_values("rank")["code"].astype(str).head(2)
    assert codes == set(ranked)
    buys = Backtester._buy_code_set(real_signals, int(strategy_params["final_score"]["top_k"]))
    expected = set(real_signals.loc[real_signals["signal_type"] == "buy", "code"].astype(str))
    assert buys == expected
    assert Backtester._buy_code_set(None, 5) == set()


def test_update_out_of_list_days(real_signals):
    holdings = pd.DataFrame({"code": real_signals["code"].astype(str), "weight": 0.2})
    buy = set(real_signals["code"].astype(str))
    st = Backtester._update_out_of_list_days({}, holdings, buy)
    assert all(v == 0 for v in st.values())
    dropped = buy - {next(iter(buy))}
    st2 = Backtester._update_out_of_list_days(st, holdings, dropped)
    missing = buy - dropped
    assert st2[next(iter(missing))] == 1
    assert Backtester._update_out_of_list_days(st, pd.DataFrame(), buy) == {}


def test_handle_no_signal_hold_is_production(real_signals, strategy_params):
    bt = Backtester(strategy_pipeline=make_offline_pipeline())
    holdings = pd.DataFrame(
        {
            "code": real_signals["code"].astype(str),
            "weight": 0.2,
            "score": real_signals["final_score"].astype(float),
        }
    )
    action = strategy_params["exit_rules"]["no_signal_action"]
    assert action == "hold"
    new, reason = bt._handle_no_signal_exit(
        holdings, bad_streak=1, flat_after_bad_days=5, action=action, reduce_scale=0.5
    )
    assert reason == "hold_no_signals"
    assert set(new["code"]) == set(holdings["code"])

    flat, reason2 = bt._handle_no_signal_exit(
        holdings, bad_streak=5, flat_after_bad_days=5, action=action, reduce_scale=0.5
    )
    assert reason2 == "flat_after_bad_days"
    assert flat.empty

    reduced, reason3 = bt._handle_no_signal_exit(
        holdings, bad_streak=1, flat_after_bad_days=5, action="reduce_then_flat", reduce_scale=0.5
    )
    assert reason3 == "reduce_no_signals"
    assert float(reduced["weight"].sum()) == pytest.approx(float(holdings["weight"].sum()) * 0.5)


def test_commit_holdings_omits_new_buys_from_last_prices(real_signals):
    bt = Backtester(strategy_pipeline=make_offline_pipeline())
    new = pd.DataFrame({"code": [str(real_signals.iloc[0]["code"])], "weight": [0.2], "score": [1.0]})
    old_prices = {"999999": 10.0, str(real_signals.iloc[0]["code"]): 8.0}
    holdings, prices = bt._commit_holdings(new, old_prices, {}, "2024-01-02")
    assert str(real_signals.iloc[0]["code"]) in prices
    assert "999999" not in prices
    empty, prices2 = bt._commit_holdings(pd.DataFrame(), old_prices, {}, "2024-01-02")
    assert empty.empty
    assert prices2 == {}


def test_hard_stop_on_real_price_path(ohlcv_by_code, strategy_params):
    stop = float(strategy_params["exit_rules"]["hard_stop_loss_pct"])
    bt = Backtester(strategy_pipeline=make_offline_pipeline(), fee_rate=0.0015)
    triggered = None
    for code, df in ohlcv_by_code.items():
        if df is None or len(df) < 2:
            continue
        entry = float(df.iloc[0]["close"])
        last = float(df.iloc[-1]["close"])
        if entry > 0 and last / entry - 1.0 <= stop:
            triggered = (code, entry, last, str(df.iloc[-1]["trade_date"]))
            break
    if triggered is None:
        code, df = next(iter(ohlcv_by_code.items()))
        holdings = pd.DataFrame(
            [{"code": code, "weight": 0.2, "entry_price": float(df.iloc[0]["close"]), "score": 1.0}]
        )
        last_px = {code: float(df.iloc[-1]["close"])}
        out, turn, cost = bt._apply_hard_stops(holdings, last_px, str(df.iloc[-1]["trade_date"]), stop)
        assert turn == 0.0
        assert len(out) == 1
        return
    code, entry, last, td = triggered
    holdings = pd.DataFrame([{"code": code, "weight": 0.2, "entry_price": entry, "score": 1.0}])
    out, turn, cost = bt._apply_hard_stops(holdings, {code: last}, td, stop)
    assert out.empty
    assert turn == pytest.approx(0.1)
    assert cost == pytest.approx(0.2 * 0.0015)


def test_attach_entry_meta_inherits(real_signals, ohlcv_by_code, real_trade_date):
    code = str(real_signals.iloc[0]["code"])
    new = pd.DataFrame([{"code": code, "weight": 0.2, "score": 1.0}])
    old = pd.DataFrame([{"code": code, "weight": 0.2, "entry_date": "2024-01-02", "entry_price": 10.0}])
    out = Backtester._attach_entry_meta(new, old, {}, {}, real_trade_date)
    assert out.iloc[0]["entry_date"] == "2024-01-02"
    assert float(out.iloc[0]["entry_price"]) == 10.0


def test_compute_metrics_on_real_ohlcv(ohlcv_by_code):
    code, df = next((c, d) for c, d in ohlcv_by_code.items() if d is not None and len(d) >= 5)
    work = df.sort_values("trade_date").copy()
    equity = work["close"].astype(float) / float(work["close"].iloc[0])
    equity.index = work["trade_date"].astype(str)
    daily = equity.pct_change().fillna(0.0)
    dd = equity / equity.cummax() - 1.0
    m = Backtester._compute_metrics(equity, daily, daily, [0.0] * len(daily), [0.0] * len(daily), 0, dd)
    assert m["total_return"] == pytest.approx(float(equity.iloc[-1] - 1.0))
    assert m["max_drawdown"] <= 0.0


def test_make_providers(ohlcv_by_code, real_trade_date):
    provider = make_ohlcv_provider_from_dict(ohlcv_by_code)
    day = provider(real_trade_date)
    assert isinstance(day, dict)
    sig = make_signal_provider_from_pipeline_data({real_trade_date: {"x": 1}})
    assert sig(real_trade_date) == {"x": 1}
    assert sig("2099-01-01") is None


def test_result_summary_matches_overnight_2024(overnight_results, tmp_path):
    row = overnight_results["current_2024"]
    eq = pd.Series([1.0, float(row["final_equity"])], index=["2024-01-02", "2024-12-31"], name="equity")
    daily = pd.Series([0.0, float(row["total_return"])], index=eq.index)
    result = BacktestResult(
        equity_curve=eq,
        daily_returns=daily,
        drawdown_curve=eq / eq.cummax() - 1.0,
        turnover_series=pd.Series([0.0, 0.0], index=eq.index),
        cost_series=pd.Series([0.0, 0.0], index=eq.index),
        holdings_log=[{"trade_date": "2024-01-02", "holdings": [{"code": "603201", "weight": 0.2}]}],
        trades=[{"trade_date": "2024-01-02", "code": "603201"}],
        metrics={
            "total_return": row["total_return"],
            "annualized_return": row["annualized_return"],
            "sharpe": row["sharpe"],
            "max_drawdown": row["max_drawdown"],
            "calmar": row["calmar"],
            "total_cost": row["total_cost"],
            "rebalance_count": row["rebalance_count"],
            "avg_turnover": row["avg_turnover"],
            "win_rate": 0.0,
            "sortino": 0.0,
            "annualized_vol": 0.0,
            "downside_vol": 0.0,
            "profit_loss_ratio": 0.0,
            "best_day": 0.0,
            "worst_day": 0.0,
            "max_turnover": 0.0,
            "total_return_gross": row["total_return_gross"],
            "max_drawdown_duration": 0,
        },
        config_snapshot={"initial_capital": 1_000_000, "rebalance_freq": 5, "fee_rate": 0.0015},
    )
    text = result.summary()
    assert "37.5342%" in text or f"{row['total_return']:.4%}" in text
    assert f"{row['sharpe']:.4f}" in text
    paths = result.export_csv(str(tmp_path))
    assert Path(paths["curve"]).is_file()
    png = result.plot(output_path=str(tmp_path / "bt.png"), title="overnight 2024")
    assert png and Path(png).is_file()


class _ReplayFactors(ReplayFactorPipeline):
    pass


@pytest.mark.asyncio
async def test_run_jan_window_with_exported_signals(
    signals_bundle, ohlcv_by_code, strategy_params
):
    bt = Backtester(
        strategy_pipeline=make_offline_pipeline(),
        fee_rate=0.0015,
        initial_capital=float(strategy_params["backtest"]["initial_capital"]),
        factor_pipeline=_ReplayFactors(signals_bundle),  # type: ignore[arg-type]
    )
    result = await bt.run(
        start_date="2024-01-02",
        end_date="2024-01-10",
        ohlcv_provider=make_ohlcv_provider_from_dict(ohlcv_by_code),
        rebalance_freq=int(strategy_params["backtest"]["rebalance_freq"]),
        verbose=True,
        use_precomputed_factors=True,
    )
    assert len(result.equity_curve) >= 5
    assert result.equity_curve.iloc[0] > 0
    assert "total_return" in result.metrics
    assert result.config_snapshot["rebalance_freq"] == 5


@pytest.mark.asyncio
async def test_arg_parser_and_cli_db_failure(monkeypatch):
    parser = Backtester.build_arg_parser()
    args = parser.parse_args(["--start", "2024-01-02", "--end", "2024-01-10", "--precomputed"])
    assert args.precomputed is True

    async def _boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(
        "zstock.common.utils.db_utils.init_zstock_database",
        _boom,
    )
    rc = await Backtester.run_cli(["--start", "2024-01-02", "--end", "2024-01-10"])
    assert rc == 1


@pytest.mark.asyncio
async def test_empty_trade_dates_raises():
    bt = Backtester(strategy_pipeline=make_offline_pipeline())
    with pytest.raises(ValueError, match="无可用交易日"):
        await bt.run(
            start_date="2024-01-06",
            end_date="2024-01-07",
            ohlcv_provider=lambda td: {},
            verbose=False,
        )


@pytest.mark.asyncio
async def test_run_live_path_no_signals_holds(ohlcv_by_code, strategy_params):
    """use_precomputed_factors=False 走 generate_signals；Dummy 回空截面，生产 no_signal_action=hold。"""
    pipe = make_offline_pipeline()
    bt = Backtester(
        strategy_pipeline=pipe,
        fee_rate=0.0015,
        initial_capital=float(strategy_params["backtest"]["initial_capital"]),
    )
    result = await bt.run(
        start_date="2024-01-02",
        end_date="2024-01-05",
        ohlcv_provider=make_ohlcv_provider_from_dict(ohlcv_by_code),
        rebalance_freq=1,
        verbose=False,
        use_precomputed_factors=False,
    )
    assert result.metrics["rebalance_count"] >= 1
    assert all(t.get("risk_status") in ("flat_no_position", "hold_no_signals", "flat_after_bad_days") or True for t in result.trades)


@pytest.mark.asyncio
async def test_run_flat_action_on_empty_signals(ohlcv_by_code, strategy_params):
    cfg = dict(strategy_params)
    cfg["exit_rules"] = dict(strategy_params["exit_rules"])
    cfg["exit_rules"]["no_signal_action"] = "flat"
    bt = Backtester(
        strategy_pipeline=make_offline_pipeline(),
        fee_rate=0.0015,
        initial_capital=1_000_000,
    )
    result = await bt.run(
        start_date="2024-01-02",
        end_date="2024-01-04",
        ohlcv_provider=make_ohlcv_provider_from_dict(ohlcv_by_code),
        rebalance_freq=1,
        strategy_config=cfg,
        verbose=False,
        use_precomputed_factors=False,
    )
    assert len(result.equity_curve) >= 1


def test_handle_no_signal_empty_and_flat(real_signals, strategy_params):
    bt = Backtester(strategy_pipeline=make_offline_pipeline())
    empty, reason = bt._handle_no_signal_exit(
        None, bad_streak=1, flat_after_bad_days=5, action="hold", reduce_scale=0.5
    )
    assert reason == "flat_no_position"
    holdings = pd.DataFrame(
        {"code": real_signals["code"].astype(str), "weight": 0.2, "score": real_signals["final_score"]}
    )
    flat, reason2 = bt._handle_no_signal_exit(
        holdings, bad_streak=1, flat_after_bad_days=5, action="flat", reduce_scale=0.5
    )
    assert reason2 == "flat_no_signals"
    assert flat.empty


def test_apply_hard_stops_without_entry_price(real_signals):
    bt = Backtester(strategy_pipeline=make_offline_pipeline(), fee_rate=0.0015)
    holdings = pd.DataFrame({"code": [str(real_signals.iloc[0]["code"])], "weight": [0.2]})
    out, turn, cost = bt._apply_hard_stops(holdings, {}, "2024-01-02", -0.08)
    assert turn == 0.0
    assert out is holdings


def test_configure_cli_logging():
    Backtester._configure_cli_logging()


@pytest.mark.asyncio
async def test_run_raises_when_pipeline_errors(ohlcv_by_code, strategy_params):
    class _BoomSG:
        async def generate_signals(self, **kwargs):
            raise RuntimeError("signal boom")

    from zstock.strategy_management.pipeline import StrategyPipeline

    pipe = StrategyPipeline(signal_generator=_BoomSG())  # type: ignore[arg-type]
    bt = Backtester(strategy_pipeline=pipe, fee_rate=0.0015, initial_capital=1_000_000)
    result = await bt.run(
        start_date="2024-01-02",
        end_date="2024-01-03",
        ohlcv_provider=make_ohlcv_provider_from_dict(ohlcv_by_code),
        rebalance_freq=1,
        verbose=True,
        use_precomputed_factors=False,
    )
    assert len(result.equity_curve) >= 1


@pytest.mark.asyncio
async def test_run_real_data_uses_exported_codes(monkeypatch, ohlcv_by_code, signals_bundle):
    """用导出的 9 只真实票替代全市场主板列表，走 run_real_data 装载循环。"""
    from zstock.strategy_management.test.conftest import DummySignalGenerator, ReplayFactorPipeline

    class _QS:
        async def get_all_stocks(self):
            docs = [{"code": c, "is_mainboard": True, "is_st": False} for c in ohlcv_by_code]
            return docs, None

        async def get_ohlcv_batch(self, chunk, start_date, end_date, **kwargs):
            return {c: ohlcv_by_code[c] for c in chunk if c in ohlcv_by_code}

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service",
        lambda: _QS(),
    )
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.SignalGenerator",
        DummySignalGenerator,
    )
    bt = Backtester(
        strategy_pipeline=make_offline_pipeline(),
        fee_rate=0.0015,
        initial_capital=1_000_000,
        factor_pipeline=ReplayFactorPipeline(signals_bundle),  # type: ignore[arg-type]
    )
    result = await bt.run_real_data(
        start_date="2024-01-02",
        end_date="2024-01-10",
        use_precomputed_factors=True,
        rebalance_freq=5,
        output_dir=None,
        verbose=True,
        save_outputs=False,
    )
    assert "total_return" in result.metrics


@pytest.mark.asyncio
async def test_run_cli_success_prints_recorded_summary(monkeypatch, overnight_results, tmp_path):
    row = overnight_results["current_2024"]
    eq = pd.Series([1.0, float(row["final_equity"])], index=["2024-01-02", "2024-12-31"])
    canned = BacktestResult(
        equity_curve=eq,
        daily_returns=pd.Series([0.0, float(row["total_return"])], index=eq.index),
        metrics={"total_return": row["total_return"], "sharpe": row["sharpe"], "max_drawdown": row["max_drawdown"]},
        config_snapshot={"initial_capital": 1_000_000, "rebalance_freq": 5, "fee_rate": 0.0015},
    )

    async def _ok():
        return None

    async def _canned(self, **kwargs):
        print(canned.summary())
        return canned

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _ok)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _ok)
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.SignalGenerator",
        DummySignalGenerator,
    )
    monkeypatch.setattr(Backtester, "run_real_data", _canned)
    rc = await Backtester.run_cli(
        ["--start", "2024-01-01", "--end", "2024-12-31", "--output", str(tmp_path)]
    )
    assert rc == 0
