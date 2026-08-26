"""风控：limits 来自 strategy_params.json；持仓来自真实截面优化结果。"""

import pandas as pd

from zstock.strategy_management.portfolio_optimizer import PortfolioOptimizer
from zstock.strategy_management.risk_manager import RiskManager, _load_risk_limits_from_config


def test_limits_loaded_from_ssot(strategy_params):
    limits = _load_risk_limits_from_config()
    top_k = int(strategy_params["final_score"]["top_k"])
    assert limits["top_k"] == top_k
    assert limits["max_weight_per_stock"] == float(strategy_params["portfolio"]["max_weight_per_stock"])
    assert limits["max_sector_exposure"] == float(strategy_params["portfolio"]["max_sector_exposure"])
    assert limits["min_holdings"] == max(1, top_k - 2)
    assert limits["allow_cash"] is True


def test_empty_holdings_warning():
    rm = RiskManager()
    out = rm.check_compliance(pd.DataFrame())
    assert out["status"] == "warning"
    assert out["issues"] == ["empty holdings"]


def test_optimized_real_holdings_within_stock_cap(real_signals, strategy_params):
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    opt = PortfolioOptimizer().optimize_portfolio(
        real_signals, min_holdings=1, max_holdings=5, max_weight_per_stock=cap
    )
    rm = RiskManager()
    compliance = rm.check_compliance(opt["holdings_df"], real_signals)
    assert compliance["metrics"]["max_weight"] <= cap + 1e-6
    # 允许现金时权重和 ∈ [0,1]
    assert -1e-6 <= compliance["metrics"]["weight_sum"] <= 1.0 + 1e-3


def test_corrects_overweight_on_real_name(real_signals, strategy_params):
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    code = str(real_signals.iloc[0]["code"])
    holdings = pd.DataFrame([{"code": code, "weight": cap + 0.15, "score": float(real_signals.iloc[0]["final_score"])}])
    rm = RiskManager()
    before = rm.check_compliance(holdings, real_signals)
    assert any("单股权重" in x for x in before["issues"])
    compliance, corrected = rm.apply_corrections(holdings, real_signals)
    assert float(corrected["weight"].max()) <= cap + 1e-9
    assert "corrected_issues" in compliance


def test_corrects_sector_exposure_with_two_real_publishers(signals_bundle, strategy_params):
    """601921 与 600757 均为 SW2出版（分别来自 2024-06-03 / 2024-01-03 真实截面）。"""
    rows = []
    for payload in signals_bundle["days"].values():
        for rec in payload.get("records") or []:
            if rec.get("sector_code") == "SW2出版":
                rows.append(rec)
    codes = {r["code"] for r in rows}
    if len(codes) < 2:
        import pytest

        pytest.skip("fixture 中出版板块不足 2 只真实票")
    picked = []
    seen = set()
    for rec in rows:
        if rec["code"] not in seen:
            picked.append(rec)
            seen.add(rec["code"])
        if len(picked) == 2:
            break
    sector_cap = float(strategy_params["portfolio"]["max_sector_exposure"])
    holdings = pd.DataFrame(
        [
            {"code": picked[0]["code"], "weight": 0.30, "sector_code": "SW2出版", "score": picked[0]["final_score"]},
            {"code": picked[1]["code"], "weight": 0.30, "sector_code": "SW2出版", "score": picked[1]["final_score"]},
        ]
    )
    rm = RiskManager()
    before = rm.check_compliance(holdings)
    assert any("板块" in x for x in before["issues"])
    _, corrected = rm.apply_corrections(holdings)
    expo = float(corrected.groupby("sector_code")["weight"].sum().iloc[0])
    assert expo <= sector_cap + 1e-6


def test_apply_corrections_noop_when_passed(real_signals, strategy_params):
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    opt = PortfolioOptimizer().optimize_portfolio(
        real_signals, min_holdings=1, max_holdings=5, max_weight_per_stock=cap
    )
    rm = RiskManager()
    compliance, same = rm.apply_corrections(opt["holdings_df"], real_signals)
    if compliance["status"] == "passed":
        assert same is opt["holdings_df"]


def test_disallow_cash_flags_partial_invest(real_signals, strategy_params):
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    opt = PortfolioOptimizer().optimize_portfolio(
        real_signals, min_holdings=1, max_holdings=3, max_weight_per_stock=cap, weighting="equal"
    )
    rm = RiskManager(risk_limits={"allow_cash": False})
    out = rm.check_compliance(opt["holdings_df"])
    if abs(out["metrics"]["weight_sum"] - 1.0) > 1e-3:
        assert out["status"] == "warning"
        assert any("权重和" in x for x in out["issues"])
