"""日内风控：回撤卖出、T+1 可卖数量、14:00 重挂、14:50 买回。"""

from datetime import datetime

from zstock.order_management.intraday import IntradayBook, risk_row_for


def _book() -> IntradayBook:
    book = IntradayBook()
    book.note_targets(["000001", "512880"])
    book.sync("000001", volume=1000, can_use=1000, cost=10.0)
    book.sync("512880", volume=1000, can_use=1000, cost=1.0)
    book.sync("511260", volume=1000, can_use=1000, cost=100.0)
    book.on_open({"000001": 10.0, "512880": 1.0, "511260": 100.0}, "2026-11-20")
    return book


def test_stock_position_drawdown_sells_all():
    book = _book()
    book.lots["000001"].peak = 10.0
    actions = book.on_prices(datetime(2026, 11, 20, 10, 0), {"000001": 8.9})
    assert actions == [
        {"action": "sell", "code": "000001", "volume": 1000, "price": 8.9, "reason": "持仓回撤"}
    ]


def test_intraday_drawdown_uses_open_not_peak():
    book = _book()
    book.lots["000001"].peak = 10.0
    actions = book.on_prices(datetime(2026, 11, 20, 10, 5), {"000001": 9.2})
    assert actions[0]["reason"] == "日内回撤"
    assert actions[0]["code"] == "000001"


def test_profit_giveback_waits_until_next_bar():
    book = _book()
    book.lots["000001"].peak = 160.0
    book.lots["000001"].day_open = 160.0
    first = book.on_prices(datetime(2026, 11, 20, 10, 0), {"000001": 160.0})
    assert first == []
    assert book.lots["000001"].profit_on
    second = book.on_prices(datetime(2026, 11, 20, 10, 5), {"000001": 152.0})
    assert second[0]["reason"] == "盈利回撤"


def test_t1_locked_shares_are_not_sold():
    book = _book()
    book.lots["000001"].can_use = 0
    book.lots["000001"].peak = 10.0
    actions = book.on_prices(datetime(2026, 11, 20, 10, 0), {"000001": 8.0})
    assert actions == []


def test_industry_and_bond_use_their_own_rows():
    book = _book()
    assert risk_row_for("512880", book.rows).code == "512880"
    assert risk_row_for("512880", book.rows).position_dd == 0.08
    assert risk_row_for("518880", book.rows).code == "518880"
    assert risk_row_for("600000", book.rows).name == "股票"
    assert risk_row_for("600000", book.rows).code == ""
    book.lots["512880"].peak = 1.0
    actions = book.on_prices(datetime(2026, 11, 20, 10, 0), {"512880": 0.91})
    assert actions[0]["code"] == "512880"
    assert actions[0]["reason"] == "持仓回撤"


def test_bond_intraday_is_tighter_than_stock():
    book = _book()
    book.lots["511260"].peak = 100.0
    actions = book.on_prices(datetime(2026, 11, 20, 10, 0), {"511260": 98.9})
    assert actions[0]["code"] == "511260"
    assert actions[0]["reason"] == "日内回撤"


def test_close_buys_back_only_names_the_strategy_wanted():
    book = _book()
    book.lots["000001"].peak = 10.0
    book.lots["511260"].peak = 100.0
    book.on_prices(
        datetime(2026, 11, 20, 10, 0),
        {"000001": 8.9, "511260": 97.0},
    )
    actions = book.tick(
        datetime(2026, 11, 20, 14, 50),
        {"000001": 9.0, "511260": 97.5},
        open_orders=[{"code": "000001", "order_id": 7, "side": "sell", "volume": 100}],
    )
    buys = [a for a in actions if a["action"] == "buy"]
    assert [a["code"] for a in buys] == ["000001"]
    assert buys[0]["volume"] == 1000
    assert actions[0]["action"] == "cancel"


def test_resubmit_once_at_latest_price():
    book = _book()
    orders = [{"code": "000001", "order_id": 3, "side": "buy", "volume": 200}]
    first = book.tick(datetime(2026, 11, 20, 14, 0), {"000001": 10.2}, orders)
    second = book.tick(datetime(2026, 11, 20, 14, 5), {"000001": 10.4}, orders)
    assert [a["action"] for a in first if a["action"] != "sell"] == ["cancel", "resubmit"]
    assert first[-1]["price"] == 10.2
    assert all(a["action"] != "resubmit" for a in second)


def test_same_five_minute_slot_does_not_sell_twice():
    book = _book()
    book.lots["000001"].peak = 10.0
    now = datetime(2026, 11, 20, 10, 1)
    assert book.on_prices(now, {"000001": 8.9})
    assert book.on_prices(datetime(2026, 11, 20, 10, 3), {"000001": 8.9}) == []
