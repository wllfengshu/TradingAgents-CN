"""execution：仓位缩放、reduce-only、book、run_equity_execution 分支。"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from zstock.strategy_management.strategy_section import execute as ex
from zstock.strategy_management.market_book import book_enabled, book_result
from zstock.strategy_management.strategy_section.optimizer import PortfolioOptimizer
from zstock.strategy_management.strategy_section.risk import RiskManager
from zstock.strategy_management.turnover_controller import TurnoverController


def test_clip_and_top_k_and_flags():
    assert ex.clip_scale(1.5) == 1.0
    assert ex.clip_scale(-0.1) == 0.0
    assert ex.clip_scale(0.4) == 0.4
    cfg = {"final_score": {"top_k": 5, "by_regime": {"reversal": {"top_k": 3}}}}
    assert ex.resolve_effective_top_k(cfg, "reversal") == 3
    assert ex.resolve_effective_top_k(cfg, "momentum") == 5
    assert ex.strategy_blocks_new_on_red({"market_selection": {}}) is True
    assert ex.strategy_blocks_new_on_red(
        {"market_selection": {"strategy_blocks_new_on_red": False}}
    ) is False
    assert ex.position_scale_in_strategy(
        {"market_selection": {"position_scale_applied_in": "strategy"}}
    )
    assert not ex.position_scale_in_strategy({"market_selection": {}})


def test_resolve_market_position_scale_branches():
    assert ex.resolve_market_position_scale("red", {}) == 0.0
    te = {
        "target_equity": {
            "enabled": True,
            "by_context": {"momentum_yellow": 0.4},
        }
    }
    assert ex.resolve_market_position_scale("yellow", te, regime="momentum") == 0.4
    overlay = {
        "market_overlay": {
            "by_regime": {"neutral": {"position_scale_green": 0.9}},
            "position_scale_yellow": 0.55,
        },
        "market_factors": {"scale_green": 0.8, "scale_yellow": 0.5},
    }
    assert ex.resolve_market_position_scale("green", overlay, regime="neutral") == 0.9
    assert ex.resolve_market_position_scale("yellow", {"market_overlay": {"position_scale_yellow": 0.33}}) == 0.33
    assert ex.resolve_market_position_scale(
        "yellow", {"market_selection": {}}, m1_default_scale=0.66
    ) == 0.66
    assert ex.resolve_market_position_scale("green", {"market_factors": {"scale_green": 0.7}}) == 0.7
    assert ex.resolve_market_position_scale("yellow", {}) == 0.5
    assert ex.resolve_market_position_scale("blue", {}) == 1.0


def test_reduce_only_and_book_helpers():
    assert not ex.should_reduce_only(None, "reversal", "yellow")
    wrp = {
        "enabled": True,
        "reduce_only": {
            "enabled": True,
            "when_regime": ["reversal"],
            "when_market_grade": ["yellow"],
        },
    }
    assert ex.should_reduce_only(wrp, "reversal", "yellow")
    assert not ex.should_reduce_only(wrp, "momentum", "yellow")

    target = pd.DataFrame({"code": ["A", "B", "C"], "weight": [0.4, 0.3, 0.3]})
    cur = pd.DataFrame({"code": ["A", "D"], "weight": [0.5, 0.5]})
    out = ex.apply_reduce_only_filter(target, cur, force_exit_codes={"D"})
    assert set(out["code"]) == {"A"}
    # None / empty current → no held names to keep
    assert ex.apply_reduce_only_filter(target, None).empty
    empty_cur = pd.DataFrame(columns=["code", "weight"])
    assert ex.apply_reduce_only_filter(target, empty_cur).empty
    kept = ex.apply_reduce_only_filter(
        pd.DataFrame(columns=["code", "weight"]),
        cur,
        force_exit_codes={"D"},
    )
    assert set(kept["code"]) == {"A"}

    assert not book_enabled({})
    assert book_enabled({"book_regime": {"enabled": True}})

    holdings = pd.DataFrame({"code": ["511260"], "weight": [1.0]})
    br = book_result(
        trade_date="2024-01-02",
        results={"market_grade": "red", "signals": pd.DataFrame()},
        state="defense",
        holdings=holdings,
        total_capital=1e6,
    )
    assert br["status"] == "success"
    assert br["book_state"] == "defense"
    assert "511260" in set(br["results"]["final_holdings"]["code"].astype(str))


def _signals(buy_n=3, grade="yellow", scale=0.7, regime="momentum"):
    rows = []
    for i in range(buy_n):
        rows.append(
            {
                "code": f"60000{i}",
                "sector_code": "SW2A",
                "final_score": 100 - i,
                "signal_type": "buy",
                "market_grade": grade,
                "position_scale": scale,
                "weight": 1.0 / buy_n,
            }
        )
    df = pd.DataFrame(rows)
    df.attrs["market_grade"] = grade
    df.attrs["position_scale"] = scale
    df.attrs["regime"] = regime
    return df


def test_run_equity_execution_happy_and_red():
    cfg = {
        "final_score": {"top_k": 3},
        "portfolio_optimization": {
            "min_holdings": 1,
            "max_holdings": 3,
            "max_weight_per_stock": 0.5,
            "weighting": "equal",
        },
        "portfolio": {"max_holdings": 3, "min_holdings": 1},
        "turnover_control": {"buffer_threshold": 0.0, "min_hold_days": 0},
        "exit_rules": {},
        "market_selection": {"strategy_blocks_new_on_red": True, "position_scale_applied_in": "strategy"},
        "market_overlay": {"position_scale_yellow": 0.6},
        "weak_regime_protection": {"enabled": False},
        "book_regime": {"enabled": False},
    }
    po = PortfolioOptimizer()
    rm = RiskManager()
    tc = TurnoverController(buffer_threshold=0.0, min_hold_days=0)
    sig = _signals()
    out = ex.run_equity_execution(
        trade_date="2024-01-02",
        signals_df=sig,
        current_positions=None,
        total_capital=1_000_000,
        cfg=cfg,
        results={"market_grade": "yellow", "style_regime": "momentum"},
        portfolio_optimizer=po,
        risk_manager=rm,
        turnover_controller=tc,
    )
    assert out["status"] == "success"
    assert out["position_scale"] == pytest.approx(0.6)
    assert len(out["results"]["final_holdings"]) >= 1

    red = ex.run_equity_execution(
        trade_date="2024-01-02",
        signals_df=sig,
        current_positions=None,
        total_capital=1_000_000,
        cfg=cfg,
        results={
            "market_grade": "red",
            "style_regime": "momentum",
        },
        portfolio_optimizer=po,
        risk_manager=rm,
        turnover_controller=tc,
    )
    assert red["status"] == "success"
    assert red["statistics"]["signals_count"] == 0


def test_run_equity_execution_risk_failed():
    class OkOpt:
        def optimize_portfolio(self, signals_df, **kwargs):
            hdf = signals_df[["code"]].copy()
            hdf["weight"] = 1.0 / len(hdf)
            return {"status": "success", "holdings_df": hdf, "weights": hdf["weight"].values}

    class BadRisk:
        def apply_corrections(self, holdings, signals_df, limits_override=None):
            return {
                "status": "failed",
                "issues": ["权重和超限", "单股权重过大"],
                "metrics": {},
            }, holdings

    cfg = {
        "final_score": {"top_k": 3},
        "portfolio_optimization": {"max_weight_per_stock": 0.5},
        "portfolio": {},
        "turnover_control": {},
        "exit_rules": {},
        "market_selection": {},
        "weak_regime_protection": {"enabled": False},
        "book_regime": {"enabled": False},
    }
    out = ex.run_equity_execution(
        trade_date="2024-01-02",
        signals_df=_signals(),
        current_positions=None,
        total_capital=1e6,
        cfg=cfg,
        results={"market_grade": "yellow", "style_regime": "neutral"},
        portfolio_optimizer=OkOpt(),
        risk_manager=BadRisk(),
        turnover_controller=TurnoverController(),
    )
    assert out["status"] == "risk_failed"


def test_run_equity_execution_opt_failed_and_factor_scale():
    class BoomOpt:
        def optimize_portfolio(self, **kwargs):
            raise RuntimeError("opt boom")

    cfg = {
        "final_score": {"top_k": 3},
        "portfolio_optimization": {
            "min_holdings": 1,
            "max_holdings": 3,
            "max_weight_per_stock": 0.5,
        },
        "portfolio": {},
        "turnover_control": {},
        "exit_rules": {},
        "market_selection": {"position_scale_applied_in": "factor"},
        "weak_regime_protection": {"enabled": False},
        "book_regime": {"enabled": False},
    }
    out = ex.run_equity_execution(
        trade_date="2024-01-02",
        signals_df=_signals(),
        current_positions=None,
        total_capital=1e6,
        cfg=cfg,
        results={"market_grade": "yellow", "style_regime": "neutral"},
        portfolio_optimizer=BoomOpt(),
        risk_manager=RiskManager(),
        turnover_controller=TurnoverController(),
    )
    assert out["status"] == "optimization_failed"

    # factor 路径读 signals 列 position_scale
    class OkOpt:
        def optimize_portfolio(self, signals_df, **kwargs):
            n = len(signals_df)
            w = np.ones(n) / n
            hdf = signals_df[["code"]].copy()
            hdf["weight"] = w
            return {"status": "success", "holdings_df": hdf, "weights": w}

        def rescale_to_target(self, holdings, **kwargs):
            return holdings

    class OkRisk:
        def apply_corrections(self, holdings, signals_df, limits_override=None):
            return {"status": "passed", "issues": [], "metrics": {}}, holdings

    class OkTc:
        min_hold_days = 0

        def apply_buffer_mechanism(self, **kwargs):
            return kwargs["new_holdings"]

        def estimate_trading_costs(self, **kwargs):
            return {"turnover": 0.1, "cost_pct": 0.001, "cost_amount": 100, "fee_rate": 0.001}

    cfg2 = dict(cfg)
    cfg2["market_selection"] = {"position_scale_applied_in": "factor"}
    out2 = ex.run_equity_execution(
        trade_date="2024-01-02",
        signals_df=_signals(scale=0.55),
        current_positions=None,
        total_capital=1e6,
        cfg=cfg2,
        results={"market_grade": "yellow", "style_regime": "neutral"},
        portfolio_optimizer=OkOpt(),
        risk_manager=OkRisk(),
        turnover_controller=OkTc(),
    )
    assert out2["status"] == "success"
    assert out2["position_scale"] == pytest.approx(0.55)


def test_run_equity_reduce_only_and_attack_book():
    class OkOpt:
        def optimize_portfolio(self, signals_df, **kwargs):
            hdf = signals_df[["code"]].copy()
            hdf["weight"] = 1.0 / len(hdf)
            return {"status": "success", "holdings_df": hdf, "weights": hdf["weight"].values}

        def rescale_to_target(self, holdings, **kwargs):
            return holdings

    class OkRisk:
        def apply_corrections(self, holdings, signals_df, limits_override=None):
            return {"status": "passed", "issues": [], "metrics": {}}, holdings

    class OkTc:
        min_hold_days = 0

        def apply_buffer_mechanism(self, **kwargs):
            return kwargs["new_holdings"]

        def estimate_trading_costs(self, **kwargs):
            return {"turnover": 0.0, "cost_pct": 0.0, "cost_amount": 0.0, "fee_rate": 0.0}

    cfg = {
        "final_score": {"top_k": 3},
        "portfolio_optimization": {"max_weight_per_stock": 0.5},
        "portfolio": {},
        "turnover_control": {},
        "exit_rules": {},
        "market_selection": {"position_scale_applied_in": "factor"},
        "weak_regime_protection": {
            "enabled": True,
            "reduce_only": {
                "enabled": True,
                "when_regime": ["reversal"],
                "when_market_grade": ["yellow"],
            },
        },
        "book_regime": {"enabled": True, "bond_code": "511260"},
    }
    cur = pd.DataFrame({"code": ["600000"], "weight": [0.5]})
    out = ex.run_equity_execution(
        trade_date="2024-01-02",
        signals_df=_signals(regime="reversal"),
        current_positions=cur,
        total_capital=1e6,
        cfg=cfg,
        results={"market_grade": "yellow", "style_regime": "reversal"},
        portfolio_optimizer=OkOpt(),
        risk_manager=OkRisk(),
        turnover_controller=OkTc(),
    )
    assert out["reduce_only"] is True

    # 权重未打满时 park_treasury 补国债；已打满则仅标记 attack
    class PartialOpt(OkOpt):
        def optimize_portfolio(self, signals_df, **kwargs):
            hdf = signals_df[["code"]].copy()
            hdf["weight"] = 0.2
            return {"status": "success", "holdings_df": hdf, "weights": hdf["weight"].values}

    attack = SimpleNamespace(state="attack", reason="ok")
    out2 = ex.run_equity_execution(
        trade_date="2024-01-02",
        signals_df=_signals(),
        current_positions=None,
        total_capital=1e6,
        cfg=cfg,
        results={"market_grade": "green", "style_regime": "momentum"},
        portfolio_optimizer=PartialOpt(),
        risk_manager=OkRisk(),
        turnover_controller=OkTc(),
        decision=attack,
    )
    assert out2["results"]["book_state"] == "attack"
    codes = set(out2["results"]["final_holdings"]["code"].astype(str))
    assert "511260" in codes
    assert out2["position_scale"] == pytest.approx(1.0)
