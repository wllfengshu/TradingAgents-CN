"""三状态账户：进攻 / 中性 / 防守，以及国债补仓。"""

import pandas as pd

from zstock.strategy_management.market_book import (
    BOND,
    BOND30,
    GOLD,
    INDUSTRY_ETFS,
    POLICY,
    MarketBook,
    defensive_targets,
    holdings_frame,
    industry_targets,
    needs_book_trade,
    park_treasury,
)


def _flat(n: int, price: float = 100.0) -> list[float]:
    return [price] * n


def _growth(n: int, rate: float, price: float = 100.0) -> list[float]:
    out = []
    px = price
    for i in range(n):
        wobble = 1.01 if i % 5 == 0 else 0.997
        px *= rate * wobble
        out.append(px)
    return out


def test_three_greens_enter_attack_and_yellow_is_neutral():
    book = MarketBook(confirm_green_days=3)
    assert book.step("d1", "green").state == "neutral"
    assert book.step("d2", "green").state == "neutral"
    attack = book.step("d3", "green")
    assert attack.state == "attack"
    assert attack.weights is None
    neutral = book.step("d4", "yellow")
    assert neutral.state == "neutral"
    assert neutral.weights == {BOND: 1.0}


def test_red_and_drawdown_are_defense():
    book = MarketBook(confirm_green_days=3)
    red = book.step("d1", "red", index_closes=[100.0] * 5, nav=1_000_000)
    assert red.state == "defense"
    book2 = MarketBook(confirm_green_days=1)
    book2.step("d1", "green", index_closes=[100.0] * 5, nav=100.0)
    locked = book2.step("d2", "green", index_closes=[100.0] * 5, nav=89.0)
    assert locked.state == "defense"
    assert locked.detail["dd_lock"] is True


def test_leave_defense_lands_on_neutral_first():
    book = MarketBook(confirm_green_days=3)
    book.state = "defense"
    book.green_streak = 2
    first = book.step("d1", "green")
    assert first.state == "neutral"
    assert first.detail["leaving_defense"] is True
    second = book.step("d2", "green")
    assert second.state == "attack"


def test_industry_book_keeps_strong_etf_and_parks_bond():
    n = 80
    closes = {code: _growth(n, 0.999) for code in INDUSTRY_ETFS}
    closes["512480"] = _growth(n, 1.003)
    for code in (BOND, BOND30, GOLD, POLICY):
        closes[code] = _flat(n)
    weights = industry_targets(closes, held=[])
    assert abs(sum(weights.values()) - 1.0) < 1e-8
    assert "512480" in weights
    assert weights["512480"] <= 0.40 + 1e-8
    assert BOND in weights
    assert sum(weights.values()) > 0.90


def test_defense_splits_bond_and_gold_when_gold_trends():
    n = 80
    closes = {
        BOND: _flat(n),
        BOND30: _flat(n, 101.0),
        GOLD: _growth(n, 1.002, 4.0),
        POLICY: _flat(n, 102.0),
    }
    weights = defensive_targets(closes)
    assert abs(sum(weights.values()) - 1.0) < 1e-8
    assert abs(weights[GOLD] - 0.30) < 1e-8
    assert abs(weights[BOND] - 0.35) < 1e-8
    assert abs(weights[BOND30] - 0.35) < 1e-8


def test_park_treasury_fills_gap_and_band_ignores_bond():
    holdings = pd.DataFrame([{"code": "600000", "weight": 0.4, "score": 1.0}])
    parked = park_treasury(holdings)
    assert float(parked["weight"].sum()) == 1.0
    assert set(parked["code"]) == {"600000", BOND}
    assert not needs_book_trade(
        {"600000": 0.4, BOND: 0.6},
        {"600000": 0.4, BOND: 0.62},
    )
    assert needs_book_trade({"600000": 0.4, BOND: 0.6}, {"600000": 0.2, BOND: 0.8})
    assert needs_book_trade({"600000": 0.4}, {"600000": 0.4, BOND: 0.6})
    assert float(holdings_frame({BOND: 1.0})["weight"].sum()) == 1.0
