"""三状态账户：进攻 / 中性 / 防守，以及国债补仓。"""

import pandas as pd

from zstock.strategy_management.market_book import (
    BOND,
    BOND30,
    GOLD,
    INDUSTRY_ETFS,
    POLICY,
    MarketBook,
    build_index_bars,
    compose_book_inputs,
    holdings_frame,
    needs_book_trade,
    park_treasury,
)
from zstock.strategy_management.strategy_industry_etf import industry_targets


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
    assert book.step("d1", "green", style_regime="momentum").state == "neutral"
    assert book.step("d2", "green", style_regime="momentum").state == "neutral"
    attack = book.step("d3", "green", style_regime="momentum")
    assert attack.state == "attack"
    assert attack.weights is None
    neutral = book.step("d4", "yellow")
    assert neutral.state == "neutral"
    assert neutral.weights == {BOND: 1.0}


def test_red_and_drawdown_are_defense():
    book = MarketBook(confirm_green_days=3)
    red = book.step("d1", "red", index_closes=[100.0] * 5, nav=1_000_000)
    assert red.state == "defense"
    # dd_stop 默认 15%：净值需跌破峰值足够深才锁防守
    book2 = MarketBook(confirm_green_days=1)
    book2.step("d1", "green", index_closes=[100.0] * 5, nav=100.0)
    locked = book2.step("d2", "green", index_closes=[100.0] * 5, nav=80.0)
    assert locked.state == "defense"
    assert locked.detail["dd_lock"] is True


def test_leave_defense_lands_on_neutral_first():
    # 关闭 leave-skip，验证「先中性再进攻」路径
    book = MarketBook(
        confirm_green_days=3,
        params={"book_regime": {"attack_skip_neutral_on_green": False}},
    )
    book.state = "defense"
    book.green_streak = 2
    first = book.step("d1", "green", style_regime="momentum")
    assert first.state == "neutral"
    assert first.detail["leaving_defense"] is True
    second = book.step("d2", "green", style_regime="momentum")
    assert second.state == "attack"
    reversal = MarketBook(confirm_green_days=1)
    held = reversal.step("d1", "green", style_regime="reversal")
    assert held.state == "neutral"
    assert held.weights is not None


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
    weights = MarketBook()._defensive_targets(closes)
    assert abs(sum(weights.values()) - 1.0) < 1e-8
    assert abs(weights[GOLD] - 0.30) < 1e-8
    assert abs(weights[POLICY] - 0.40) < 1e-8
    assert abs(weights[BOND] - 0.30) < 1e-8


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


def test_compose_book_excludes_trade_date_bar():
    rows = [
        {"trade_date": "2024-01-02", "close": 10.0, "high": 10.5, "low": 9.5, "volume": 1e6},
        {"trade_date": "2024-01-03", "close": 11.0, "high": 11.5, "low": 10.5, "volume": 1e6},
    ]
    ohlcv = {"399300": pd.DataFrame(rows)}
    out = compose_book_inputs("2024-01-03", ohlcv)
    assert len(out.index_bars) == 1
    assert out.index_closes == [10.0]


def test_build_index_bars_shape():
    df = pd.DataFrame([{"close": 100.0, "high": 101.0, "low": 99.0, "volume": 0.0}])
    bars = build_index_bars(df)
    assert len(bars) == 1
    assert bars[0]["close"] == 100.0
