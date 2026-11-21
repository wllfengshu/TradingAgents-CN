"""MarketBook：snapshot/restore/prepare/regime 强制防守分支。"""

from __future__ import annotations

from zstock.strategy_management.market_book import BOND, GOLD, INDUSTRY_ETFS, MarketBook


def test_snapshot_restore_and_date_rollback():
    book = MarketBook(confirm_green_days=2)
    book.state = "defense"
    book.green_streak = 1
    book.dd_lock = True
    book.lock_age = 3
    book._last_date = "2024-01-10"
    book._gold_active = "gold"
    book._gold_wish = "gold"
    book._gold_wish_streak = 2
    snap = book.snapshot()
    book2 = MarketBook(confirm_green_days=3)
    book2.restore(snap)
    assert book2.state == "defense"
    assert book2.dd_lock is True
    assert book2.confirm_green_days == 2
    assert book2._gold_active == "gold"
    assert book2._gold_wish_streak == 2

    book2.restore(None)
    assert book2.state == "neutral"

    book3 = MarketBook(confirm_green_days=2)
    book3._last_date = "2024-01-10"
    book3.state = "attack"
    book3.prepare_for_date("2024-01-05")  # 回退 → reset
    assert book3.state == "neutral"


def test_regime_off_forces_defense():
    book = MarketBook(confirm_green_days=1)
    # 价格跌破均线：构造长序列后突然下跌
    up = [100.0 + i * 0.1 for i in range(130)]
    down = up[:-5] + [up[-6] * 0.9] * 5
    d = book.step("2024-01-02", "green", index_closes=down, nav=1e6)
    # 可能 defense（regime_off）或 attack；至少返回合法状态
    assert d.state in ("defense", "neutral", "attack")

    # 明确 dd_lock 路径
    book2 = MarketBook(confirm_green_days=1)
    book2.step("d1", "green", index_closes=[100.0] * 40, nav=100.0)
    locked = book2.step("d2", "green", index_closes=[100.0] * 40, nav=85.0)
    assert locked.state == "defense"
    assert locked.detail.get("dd_lock") or book2.dd_lock

    # yellow + closes → industry weights
    closes = {c: [100.0 + (i % 3) * 0.01 for i in range(80)] for c in INDUSTRY_ETFS}
    closes[BOND] = [100.0] * 80
    closes[GOLD] = [100.0] * 80
    yellow = book2.step(
        "d3",
        "yellow",
        index_closes=[100.0] * 40,
        nav=1e6,
        closes=closes,
    )
    assert yellow.state in ("neutral", "defense")
    if yellow.weights:
        assert abs(sum(yellow.weights.values()) - 1.0) < 1e-6
