"""XtQuantExecutor：回调状态机 / 幂等 / 撤单终态（真实 603201 价格）。"""

from __future__ import annotations

import pytest

from zstock.common.entity.order_entity import Order
from zstock.order_management.broker import XtQuantExecutor
from test.order_management.test.conftest import FakeDB, FakeQMT


@pytest.fixture
def executor(real_price_map, initial_capital):
    db = FakeDB()
    qmt = FakeQMT(real_price_map, capital=initial_capital)
    ex = XtQuantExecutor(qmt_util=qmt, allow_mock=True)
    ex._db_service = db
    ex.mock_mode = True
    return ex, db, qmt


@pytest.mark.asyncio
async def test_submit_idempotent_and_callbacks(executor, real_price_map):
    ex, db, qmt = executor
    px = real_price_map["603201"]
    order = Order("o-603201", "603201", "buy", 100, price_type="limit", price=px)
    xt = await ex.submit_order(order)
    assert xt and int(xt) > 0
    # 幂等：同 order_id 已 pending 不再下单
    before = len(qmt.submitted)
    again = await ex.submit_order(order)
    assert again == xt
    assert len(qmt.submitted) == before

    assert await ex.on_order_status(xt, "partial", filled_volume=50)
    doc = await db.query_one("zstock_orders", {"order_id": "o-603201"})
    assert doc["status"] == "partial_filled"
    assert doc["filled_volume"] == 50
    # 状态回报里的 50 是累计成交，同一笔 trade 不能再加一次
    assert await ex.on_trade(xt, filled_price=px, filled_volume=50, broker_trade_id="t1")
    doc = await db.query_one("zstock_orders", {"order_id": "o-603201"})
    assert doc["status"] == "partial_filled"
    assert doc["filled_volume"] == 50
    assert await ex.on_order_status(xt, "accepted") is True
    doc = await db.query_one("zstock_orders", {"order_id": "o-603201"})
    assert doc["status"] == "partial_filled"

    assert await ex.on_trade(xt, filled_price=px, filled_volume=50, broker_trade_id="t2")
    doc = await db.query_one("zstock_orders", {"order_id": "o-603201"})
    assert doc["status"] == "filled"
    assert doc["filled_volume"] == 100

    # 重复成交 id 忽略
    assert await ex.on_trade(xt, filled_price=px, filled_volume=10, broker_trade_id="t1") is False
    # 终态后状态回退忽略
    assert await ex.on_order_status(xt, "accepted") is False
    assert await ex.on_order_status(xt, "weird_status") is False


@pytest.mark.asyncio
async def test_cancel_terminal_and_unknown_order(executor, real_price_map):
    ex, db, qmt = executor
    px = real_price_map["000060"]
    order = Order("o-cancel", "000060", "sell", 200, price_type="limit", price=px)
    xt = await ex.submit_order(order)
    assert await ex.on_order_status(xt, "filled", filled_volume=200)
    assert await ex.cancel_order("o-cancel") is False
    assert await ex.cancel_order("missing") is False

    order2 = Order("o-pc", "601107", "sell", 100, price_type="limit", price=real_price_map["601107"])
    xt2 = await ex.submit_order(order2)
    assert await ex.cancel_order("o-pc") is True
    doc = await db.query_one("zstock_orders", {"order_id": "o-pc"})
    assert doc["status"] == "pending_cancel"


@pytest.mark.asyncio
async def test_buy_without_volume_kwarg(executor, real_price_map, monkeypatch):
    ex, db, qmt = executor
    px = real_price_map["603201"]

    def buy_no_vol(code, amount, price=None, remark=""):
        qmt.submitted.append({"side": "buy", "code": code, "amount": amount})
        return 20001

    monkeypatch.setattr(qmt, "buy", buy_no_vol)
    order = Order("o-amt", "603201", "buy", 100, price_type="limit", price=px)
    xt = await ex.submit_order(order)
    assert xt == 20001
