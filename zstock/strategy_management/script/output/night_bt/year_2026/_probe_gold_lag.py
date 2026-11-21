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
            "trade_date": {"$gte": "2026-07-01", "$lte": "2026-11-13"},
        },
        {"_id": 0, "trade_date": 1, "close": 1},
    ).to_list(None)
    print("n", len(rows), "sample", rows[:1])
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
    for act, d in switches:
        i = idx.index(d)
        sig = idx[i - 1]
        print(
            f"{d} {act}: signal={sig} "
            f"close={float(g.loc[sig, 'close']):.3f} "
            f"ma20={float(g.loc[sig, 'ma20']):.3f} "
            f"above={bool(g.loc[sig, 'above'])} | "
            f"exec_close={float(g.loc[d, 'close']):.3f} "
            f"exec_above={bool(g.loc[d, 'above'])}"
        )
    await close_zstock_database()


if __name__ == "__main__":
    asyncio.run(main())
