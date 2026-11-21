"""5 分钟回放：隔夜仓回撤卖出后尾盘买回；当天买入的 T+1 不卖。"""

import pandas as pd

from zstock.order_management.intraday import IntradayBook
from zstock.order_management.intraday_sim import SharePos, simulate_day


def _bars(code: str, points: list) -> pd.DataFrame:
    rows = []
    for hhmm, price in points:
        rows.append({
            "trade_date": f"2026-11-20 {hhmm}:00",
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 100000,
        })
    return pd.DataFrame(rows)


def test_overnight_drawdown_sells_and_buys_back_at_close():
    book = IntradayBook()
    positions = {"000001": SharePos(shares=1000, cost=12.0, can_use=1000)}
    result = simulate_day(
        book,
        positions,
        cash=0.0,
        target_weights={"000001": 1.0},
        bars_by_code={"000001": _bars("000001", [("09:35", 12.0), ("10:00", 10.7), ("14:50", 10.8), ("15:00", 10.8)])},
        prev_close={"000001": 11.0},
        trade_date="2026-11-20",
        fee_rate=0.0,
        stamp_tax=0.0,
    )
    sells = [t for t in result.trades if t["side"] == "sell"]
    buys = [t for t in result.trades if t["side"] == "buy"]
    assert sells and sells[0]["reason"] == "持仓回撤"
    assert buys and buys[0]["reason"] == "尾盘买回"
    assert "000001" in result.positions


def test_same_day_t1_buy_is_not_sold():
    book = IntradayBook()
    result = simulate_day(
        book,
        {},
        cash=100000.0,
        target_weights={"000001": 1.0},
        bars_by_code={"000001": _bars("000001", [("09:35", 10.0), ("10:00", 9.2), ("15:00", 9.2)])},
        prev_close={"000001": 10.0},
        trade_date="2026-11-20",
        fee_rate=0.0,
        stamp_tax=0.0,
    )
    assert [t["side"] for t in result.trades] == ["buy"]
    assert result.positions["000001"].shares == 10000


def test_rebalance_sell_is_not_bought_back():
    book = IntradayBook()
    positions = {"000001": SharePos(shares=1000, cost=10.0, can_use=1000)}
    result = simulate_day(
        book,
        positions,
        cash=0.0,
        target_weights={"511260": 1.0},
        bars_by_code={
            "000001": _bars("000001", [("09:35", 10.0), ("15:00", 10.0)]),
            "511260": _bars("511260", [("09:35", 100.0), ("15:00", 100.0)]),
        },
        prev_close={"000001": 10.0, "511260": 100.0},
        trade_date="2026-11-20",
        fee_rate=0.0,
        stamp_tax=0.0,
    )
    assert [t for t in result.trades if t["code"] == "000001" and t["side"] == "buy"] == []
    assert result.trades[0]["reason"] == "调仓"
