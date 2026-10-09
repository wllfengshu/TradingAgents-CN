"""成交对账：标的与价格用 2024-01-02 真实截面。"""

import pandas as pd

from zstock.order_management.order_generator import OrderGenerator
from zstock.order_management.trade_settlement import TradeSettlement


def test_handle_buy_then_sell_updates_position():
    ts = TradeSettlement()
    assert ts._handle_trade_report("o1", 500, 12.36, "buy", "603201")
    assert ts._get_current_positions()["603201"] == 500
    assert ts._handle_trade_report("o2", 200, 12.40, "sell", "603201")
    assert ts._get_current_positions()["603201"] == 300
    assert ts._handle_trade_report("o3", 10, 1.0, "hold", "603201")
    assert ts._get_current_positions()["603201"] == 300


def test_slippage_and_export():
    ts = TradeSettlement()
    ts._handle_trade_report("o1", 100, 4.172, "buy", "000060")
    assert ts._calculate_slippage(4.172, 4.172) == 0.0
    assert ts._calculate_slippage(0, 4.172) == 0.0
    slip = ts._calculate_slippage(4.00, 4.172)
    assert slip > 0
    trades = ts._get_trades()
    assert len(trades) == 1
    tid = trades[0]["trade_id"]
    assert ts._get_trades(tid)[0]["trade_id"] == tid
    assert ts._get_trades("missing") == []
    assert not ts._export_trades("dataframe").empty
    assert isinstance(ts._export_trades("list"), list)
    assert ts._export_trades("xml") is None
    assert not ts._export_positions("dataframe").empty
    assert "000060" in ts._export_positions("dict")
    assert ts._export_positions("xml") is None


def test_reconcile_local_vs_broker_ok_and_error():
    ts = TradeSettlement()
    ts.sync_positions_from_broker([{"code": "603201", "volume": 400}])
    ok = ts._reconcile_positions([{"code": "603201", "volume": 400}])
    assert ok["status"] == "ok"
    warn = ts._reconcile_positions([{"code": "603201", "volume": 500}])
    assert warn["status"] in {"warning", "error"}
    extra = ts._reconcile_positions(
        [{"code": "603201", "volume": 400}, {"code": "000060", "volume": 200}]
    )
    assert extra["discrepancy_count"] >= 1


def test_reconcile_target_vs_broker_real_weights(target_holdings, real_price_map, initial_capital):
    ts = TradeSettlement()
    code = str(target_holdings["code"].iloc[0])
    row = target_holdings.iloc[0]
    lots = OrderGenerator().lots_from_positions(
        pd.DataFrame([{"code": code, "weight": float(row["weight"])}]),
        real_price_map,
        initial_capital,
    )
    target_vol = int(lots.iloc[0]["volume"])
    broker = [{"code": code, "volume": target_vol}]
    matched = ts.reconcile_target_vs_broker(lots, broker)
    assert matched["status"] == "ok"
    assert matched["matched_count"] == 1

    extra = ts.reconcile_target_vs_broker(
        lots,
        [{"code": code, "volume": target_vol}, {"code": "600000", "volume": 100}],
    )
    assert extra["discrepancy_count"] >= 1

    unpriced = OrderGenerator().lots_from_positions(
        pd.DataFrame([{"code": code, "weight": 0.2}]),
        {},
        initial_capital,
    )
    assert unpriced.empty
    leftover = ts.reconcile_target_vs_broker(unpriced, [{"code": code, "volume": 100}])
    assert leftover["discrepancy_count"] >= 1
    assert leftover["discrepancies"][0].get("status") == "extra_holding"
