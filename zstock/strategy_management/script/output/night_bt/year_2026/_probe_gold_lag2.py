# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pandas as pd

from zstock.common.book_codes import GOLD
from zstock.common.utils.db_utils import close_zstock_database, get_database, init_zstock_database


async def main() -> None:
    await init_zstock_database()
    db = get_database()
    rows = await db["zstock_ohlcv"].find(
        {
            "code": GOLD,
            "period": "D",
            "trade_date": {"$gte": "2025-10-01", "$lte": "2026-11-13"},
        },
        {"_id": 0, "trade_date": 1, "close": 1},
    ).to_list(None)
    df = pd.DataFrame(rows)
    df["trade_date"] = df["trade_date"].astype(str)
    g = df.sort_values("trade_date").set_index("trade_date")
    g["ma20"] = g["close"].rolling(20).mean()
    g["above"] = g["close"] > g["ma20"]
    switches = [
        ("buy_gold", "2026-07-22"),
        ("sell_gold", "2026-07-27"),
        ("buy_gold", "2026-07-28"),
        ("sell_gold", "2026-07-29"),
        ("buy_gold", "2026-08-06"),
        ("sell_gold", "2026-09-02"),
        ("buy_gold", "2026-11-06"),
        ("sell_gold", "2026-11-09"),
        ("buy_gold", "2026-11-10"),
        ("sell_gold", "2026-11-12"),
    ]
    idx = list(g.index)
    print("gold 2026-01-05", float(g.loc["2026-01-05", "close"]), "-> 2026-11-13", float(g.loc["2026-11-13", "close"]))
    for act, d in switches:
        i = idx.index(d)
        sig = idx[i - 1]
        print(
            f"{d} {act}: T-1={sig} "
            f"px={float(g.loc[sig, 'close']):.3f} ma20={float(g.loc[sig, 'ma20']):.3f} "
            f"above={bool(g.loc[sig, 'above'])} | "
            f"T px={float(g.loc[d, 'close']):.3f} aboveT={bool(g.loc[d, 'above'])} "
            f"dret={float(g.loc[d, 'close'] / g.loc[sig, 'close'] - 1):+.2%}"
        )
    # Holding periods approximate from ledger: bought on days, sold on days
    # compute naive PnL of 30% sleeve if followed switches
    pos = 0
    entry = None
    pnl = 1.0
    for act, d in switches:
        px = float(g.loc[d, "close"])
        if act == "buy_gold" and pos == 0:
            pos = 1
            entry = px
        elif act == "sell_gold" and pos == 1 and entry:
            r = px / entry - 1
            pnl *= 1 + r
            print(f"  roundtrip {entry:.3f}->{px:.3f} {r:+.2%}")
            pos = 0
            entry = None
    print("compounded gold-sleeve roundtrips (price only)", pnl - 1)
    await close_zstock_database()


if __name__ == "__main__":
    asyncio.run(main())
