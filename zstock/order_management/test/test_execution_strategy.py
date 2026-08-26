"""执行策略：用真实订单量做整手拆批，executor 为可注入桩。"""

from types import SimpleNamespace

import pytest

from zstock.order_management.execution_strategy import ExecutionStrategy, _lot_batches
from zstock.common.entity.order_entity import Order


class StubExecutor:
    def __init__(self, fail_ids=None):
        self.submitted = []
        self.fail_ids = set(fail_ids or [])

    async def submit_order(self, order):
        if order.order_id in self.fail_ids:
            return None
        self.submitted.append(order)
        return 1


def _order(oid, code, direction, volume):
    return Order(order_id=oid, stock_code=code, direction=direction, volume=volume)


def test_lot_batches_real_round_lots():
    assert _lot_batches(0, 5) == []
    assert _lot_batches(80, 5) == []
    assert _lot_batches(150, 5) == []
    batches = _lot_batches(1000, 5)
    assert batches
    assert sum(batches) == 1000
    assert all(b % 100 == 0 and b > 0 for b in batches)


@pytest.mark.asyncio
async def test_auction_sell_defers_buys():
    strat = ExecutionStrategy(twap_interval_seconds=0)
    sell = _order("s1", "603201", "sell", 500)
    buy = _order("b1", "000060", "buy", 400)
    exe = StubExecutor()
    report = await strat.execute_with_strategy([sell, buy], exe, strategy="auction_sell")
    assert report["submitted"] == 1
    assert report["deferred"] == 1
    assert report["failed"] == 0
    assert exe.submitted[0].direction == "sell"


@pytest.mark.asyncio
async def test_twap_buy_splits_lots_then_submits():
    strat = ExecutionStrategy(twap_slices=5, twap_interval_seconds=0)
    sell = _order("s1", "601107", "sell", 200)
    buy = _order("b1", "603201", "buy", 1000)
    exe = StubExecutor()
    report = await strat.execute_with_strategy([sell, buy], exe, strategy="twap_buy")
    assert report["failed"] == 0
    assert report["submitted"] == 1 + len(_lot_batches(1000, 5))
    assert any(o.order_id.endswith("_B0") for o in exe.submitted)


@pytest.mark.asyncio
async def test_twap_skips_odd_lot_buy():
    strat = ExecutionStrategy(twap_interval_seconds=0)
    buy = _order("b1", "000060", "buy", 80)
    report = await strat.execute_with_strategy([buy], StubExecutor(), strategy="twap_buy")
    assert report["failed"] == 1
    assert report["submitted"] == 0


@pytest.mark.asyncio
async def test_final_rejects_odd_lot_and_submits_rest():
    strat = ExecutionStrategy()
    odd = _order("b1", "000060", "buy", 150)
    ok = _order("b2", "603201", "buy", 300)
    sell = _order("s1", "601107", "sell", 200)
    exe = StubExecutor()
    report = await strat.execute_with_strategy([odd, ok, sell], exe, strategy="final")
    assert report["failed"] == 1
    assert report["submitted"] == 2


@pytest.mark.asyncio
async def test_closed_and_unknown_mark_all_failed():
    strat = ExecutionStrategy()
    orders = [_order("b1", "603201", "buy", 100)]
    closed = await strat.execute_with_strategy(orders, StubExecutor(), strategy="closed")
    assert closed["failed"] == 1
    unknown = await strat.execute_with_strategy(orders, StubExecutor(), strategy="mystery")
    assert unknown["failed"] == 1


def test_get_current_strategy_is_one_of_known():
    name = ExecutionStrategy().get_current_strategy()
    assert name in {"auction_sell", "twap_buy", "final", "closed"}


def test_stats_reset():
    strat = ExecutionStrategy()
    strat.execution_stats["twap_buy_count"] = 3
    stats = strat.get_strategy_stats()
    assert stats["twap_buy_count"] == 3
    strat.reset_stats()
    assert strat.get_strategy_stats()["twap_buy_count"] == 0
