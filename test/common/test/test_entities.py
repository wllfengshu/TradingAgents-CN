"""实体：AccountInfo / Position / Order 序列化与计算属性。"""

from __future__ import annotations

from zstock.common.entity import AccountInfo, Order, Position


def test_account_and_position_from_bridge_shapes():
    acc = AccountInfo(cash=1000.0, total_value=5000.0, frozen_cash=10.0)
    assert acc.cash == 1000.0
    assert acc.total_value == 5000.0

    pos = Position(
        code="600000.SH",
        name="浦发银行",
        volume=1000,
        cost_price=10.0,
        current_price=10.5,
    )
    assert pos.market_value == 10500.0
    assert pos.unrealized_pnl == 500.0


def test_order_to_dict_and_repr():
    order = Order(
        order_id="o1",
        stock_code="603201.SH",
        direction="buy",
        volume=100,
        price_type="limit",
        price=12.3,
    )
    d = order.to_dict()
    assert d["order_id"] == "o1"
    assert d["stock_code"] == "603201.SH"
    assert d["direction"] == "buy"
    assert d["volume"] == 100
    assert d["price"] == 12.3
    assert d["status"] == "created"
    assert "Order(o1" in repr(order)
