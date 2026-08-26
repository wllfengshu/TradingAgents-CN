"""换手控制：buffer / 最短持有 / 成本。持仓代码与分数来自真实截面。"""

import pandas as pd
import pytest

from zstock.strategy_management.portfolio_optimizer import PortfolioOptimizer
from zstock.strategy_management.turnover_controller import TurnoverController, _trading_days_between


def _optimize(signals, strategy_params):
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    return PortfolioOptimizer().optimize_portfolio(
        signals, min_holdings=1, max_holdings=5, max_weight_per_stock=cap
    )["holdings_df"]


def test_trading_days_between():
    assert _trading_days_between("2024-01-02", "2024-01-05") == 3
    assert _trading_days_between("bad", "2024-01-05") == 0


def test_first_entry_adopts_new(real_signals, strategy_params):
    tov = strategy_params["turnover_control"]
    ctrl = TurnoverController(
        buffer_threshold=float(tov["buffer_threshold"]),
        min_hold_days=int(tov["min_hold_days"]),
    )
    new_h = _optimize(real_signals, strategy_params)
    out = ctrl.apply_buffer_mechanism(new_h, current_holdings=None)
    assert set(out["code"]) == set(new_h["code"])
    assert float(out["weight"].sum()) <= 1.0 + 1e-9


def test_buffer_rejects_weak_newcomer(signals_bundle, strategy_params):
    jan2 = signals_bundle["days"]["2024-01-02"]
    jan3 = signals_bundle["days"]["2024-01-03"]
    from zstock.strategy_management.test.conftest import signals_from_payload

    cur_sig = signals_from_payload(jan2)
    new_sig = signals_from_payload(jan3)
    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    current = _optimize(cur_sig, strategy_params)
    target = _optimize(new_sig, strategy_params)
    current = current.copy()
    current["score"] = current["code"].map(cur_sig.set_index("code")["final_score"])
    target = target.copy()
    target["score"] = target["code"].map(new_sig.set_index("code")["final_score"])

    ctrl = TurnoverController(buffer_threshold=0.25, min_hold_days=3)
    out = ctrl.apply_buffer_mechanism(target, current, trade_date="2024-01-03")
    min_cur = float(current["score"].min())
    threshold = min_cur * 1.25
    added = set(out["code"]) - set(current["code"])
    for code in added:
        assert float(target.set_index("code").loc[code, "score"]) >= threshold


def test_min_hold_protects_and_force_exit_overrides(real_signals, strategy_params):
    ctrl = TurnoverController(buffer_threshold=0.25, min_hold_days=3)
    current = _optimize(real_signals, strategy_params).copy()
    current["score"] = current["code"].map(real_signals.set_index("code")["final_score"])
    current["entry_date"] = "2024-01-02"
    # 全新目标：一只当日未持有的真实票（2024-01-03 的 600812）
    new = pd.DataFrame([{"code": "600812", "weight": 0.2, "score": 86.55822569772339}])
    protected = ctrl.apply_buffer_mechanism(
        new, current, trade_date="2024-01-03", min_hold_days=3
    )
    assert set(current["code"]).issubset(set(protected["code"]))

    forced = ctrl.apply_buffer_mechanism(
        new,
        current,
        trade_date="2024-01-03",
        min_hold_days=3,
        force_exit_codes=set(current["code"].astype(str)),
    )
    assert set(current["code"]).isdisjoint(set(forced["code"]))


def test_empty_new_keeps_protected(real_signals, strategy_params):
    ctrl = TurnoverController(buffer_threshold=0.25, min_hold_days=3)
    current = _optimize(real_signals, strategy_params).copy()
    current["entry_date"] = "2024-01-02"
    out = ctrl.apply_buffer_mechanism(
        pd.DataFrame(), current, trade_date="2024-01-03", min_hold_days=3
    )
    assert not out.empty
    assert set(out["code"]) == set(current["code"])


def test_normalize_only_downscales():
    ctrl = TurnoverController()
    df = pd.DataFrame([{"code": "601107", "weight": 0.7}, {"code": "000060", "weight": 0.5}])
    out = ctrl._normalize(df)
    assert abs(float(out["weight"].sum()) - 1.0) < 1e-9
    under = pd.DataFrame([{"code": "601107", "weight": 0.2}])
    assert float(ctrl._normalize(under)["weight"].sum()) == 0.2


def test_estimate_costs_and_positions(real_signals, strategy_params):
    ctrl = TurnoverController(fee_rate=0.0015)
    new_h = _optimize(real_signals, strategy_params)
    costs = ctrl.estimate_trading_costs(None, new_h, total_capital=1e7)
    assert costs["turnover"] == pytest.approx(0.5 * float(new_h["weight"].abs().sum()), abs=1e-9)
    assert costs["cost_pct"] == pytest.approx(costs["turnover"] * 0.0015, abs=1e-12)
    pos = ctrl.generate_final_positions(new_h, trade_date="2024-01-02")
    assert pos["count"] == len(new_h)
    assert pos["trade_date"] == "2024-01-02"
    empty = ctrl.generate_final_positions(pd.DataFrame(), trade_date="2024-01-02")
    assert empty["count"] == 0
