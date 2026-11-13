"""StrategyPipeline：预计算信号来自 Mongo 导出截面，配置来自 SSOT。"""

import pandas as pd
import pytest

from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.strategy_management.test.conftest import make_offline_pipeline


def test_default_config_matches_ssot(strategy_params):
    cfg = StrategyPipeline._default_config()
    top_k = int(strategy_params["final_score"]["top_k"])
    assert cfg["portfolio_optimization"]["max_holdings"] == top_k
    assert cfg["portfolio_optimization"]["max_weight_per_stock"] == float(
        strategy_params["portfolio"]["max_weight_per_stock"]
    )
    assert cfg["portfolio_optimization"].get("fill_weights_min_names", 0) == 0
    assert cfg["turnover_control"]["buffer_threshold"] == float(
        strategy_params["turnover_control"]["buffer_threshold"]
    )
    assert cfg["exit_rules"]["hard_stop_loss_pct"] == float(
        strategy_params["exit_rules"]["hard_stop_loss_pct"]
    )
    assert cfg["exit_rules"]["no_signal_action"] == "hold"
    for key in (
        "weak_regime_protection",
        "adaptive_rebalance",
        "market_selection",
        "target_equity",
        "market_overlay",
        "final_score",
        "portfolio",
        "book_regime",
    ):
        assert key in cfg
        assert cfg[key] == (strategy_params.get(key) or {})


def test_resolve_effective_top_k(strategy_params):
    assert StrategyPipeline._resolve_effective_top_k(strategy_params, "reversal") == 3
    assert StrategyPipeline._resolve_effective_top_k(strategy_params, "neutral") == 5
    assert StrategyPipeline._resolve_effective_top_k(strategy_params, "momentum") == 5


def test_extract_market_grade_from_attrs_and_columns(green_signals):
    assert StrategyPipeline._extract_market_grade(green_signals) == "green"
    assert StrategyPipeline._extract_market_grade(None) == "unknown"
    empty = pd.DataFrame()
    assert StrategyPipeline._extract_market_grade(empty) == "unknown"


@pytest.mark.asyncio
async def test_empty_precomputed_on_red_is_defense_book():
    empty = CrossSectionStrategyPipeline._empty_signals_df("2024-01-02", "red", 0.0)
    pipe = make_offline_pipeline()
    out = await pipe.execute_full_pipeline(trade_date="2024-01-02", precomputed_signals=empty)
    assert out["status"] == "success"
    assert out["book_state"] == "defense"
    holdings = out["results"]["final_holdings"]
    assert float(holdings["weight"].sum()) == pytest.approx(1.0)
    assert "511260" in set(holdings["code"].astype(str))


@pytest.mark.asyncio
async def test_real_jan_signals_success_and_yellow_scale(real_signals, real_trade_date, strategy_params):
    pipe = make_offline_pipeline()
    out = await pipe.execute_full_pipeline(
        trade_date=real_trade_date,
        precomputed_signals=real_signals,
        total_capital=float(strategy_params["backtest"]["initial_capital"]),
        config=strategy_params,
    )
    assert out["status"] == "success"
    assert out["market_grade"] == "yellow"
    assert out["book_state"] == "neutral"
    holdings = out["results"]["final_holdings"]
    assert not holdings.empty
    assert float(holdings["weight"].sum()) == pytest.approx(1.0)
    assert float(holdings["weight"].sum()) > 0.90


@pytest.mark.asyncio
async def test_reversal_yellow_reduce_only_blocks_new_entry(
    reversal_yellow_signals, strategy_params
):
    td = str(reversal_yellow_signals.iloc[0]["trade_date"])
    pipe = make_offline_pipeline()
    out = await pipe.execute_full_pipeline(
        trade_date=td,
        precomputed_signals=reversal_yellow_signals,
        current_positions=None,
        config=strategy_params,
    )
    assert out["status"] == "success"
    assert out["book_state"] == "neutral"
    assert float(out["results"]["final_holdings"]["weight"].sum()) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_reduce_only_with_runtime_config_only(reversal_yellow_signals):
    """黄灯走中性行业 ETF 账户，不再在这一层开新股票。"""
    runtime = StrategyPipeline._default_config()
    assert runtime.get("weak_regime_protection")
    td = str(reversal_yellow_signals.iloc[0]["trade_date"])
    pipe = make_offline_pipeline()
    out = await pipe.execute_full_pipeline(
        trade_date=td,
        precomputed_signals=reversal_yellow_signals,
        current_positions=None,
        config=runtime,
    )
    assert out["status"] == "success"
    assert out["book_state"] == "neutral"
    assert float(out["results"]["final_holdings"]["weight"].sum()) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_rank_force_exit_on_worst_jan_name(real_signals, strategy_params):
    worst = real_signals.sort_values("rank", ascending=False).iloc[0]
    current = pd.DataFrame(
        [{"code": worst["code"], "weight": 0.2, "score": float(worst["final_score"])}]
    )
    force = StrategyPipeline._rank_force_exit_codes(
        current, real_signals, strategy_params["exit_rules"]
    )
    n = len(real_signals)
    pct = float(worst["rank"]) / float(n)
    if pct > float(strategy_params["exit_rules"]["rank_percentile_threshold"]):
        assert str(worst["code"]) in force
    else:
        assert str(worst["code"]) not in force


@pytest.mark.asyncio
async def test_optimization_failure_status(real_signals, real_trade_date):
    class _Boom:
        def optimize_portfolio(self, **kwargs):
            raise RuntimeError("opt boom")

    from zstock.strategy_management.market_book import BookDecision

    pipe = StrategyPipeline(
        factor_pipeline=make_offline_pipeline()._factor_pipeline,
        portfolio_optimizer=_Boom(),  # type: ignore[arg-type]
    )
    pipe.book.step = lambda *args, **kwargs: BookDecision("attack", None, "green")  # type: ignore[method-assign]
    out = await pipe.execute_full_pipeline(trade_date=real_trade_date, precomputed_signals=real_signals)
    assert out["status"] == "optimization_failed"


def test_rank_force_exit_guards(real_signals, strategy_params):
    assert StrategyPipeline._rank_force_exit_codes(None, real_signals, {}) == set()
    tiny = real_signals.head(1)
    current = pd.DataFrame([{"code": tiny.iloc[0]["code"], "weight": 0.2}])
    assert StrategyPipeline._rank_force_exit_codes(current, tiny, strategy_params["exit_rules"]) == set()


def test_extract_grade_from_column(green_signals):
    df = green_signals.copy()
    df.attrs.clear()
    assert StrategyPipeline._extract_market_grade(df) == "green"
