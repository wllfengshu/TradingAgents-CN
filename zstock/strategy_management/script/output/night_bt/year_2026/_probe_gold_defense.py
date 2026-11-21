# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pandas as pd

from zstock.common.book_codes import BOND, BOND30, GOLD, INDEX_CODE, POLICY
from zstock.common.utils.db_utils import close_zstock_database, get_database, init_zstock_database


async def main() -> None:
    await init_zstock_database()
    db = get_database()
    codes = [GOLD, POLICY, BOND, BOND30, INDEX_CODE, "511010"]
    rows = await db["zstock_ohlcv"].find(
        {
            "code": {"$in": codes},
            "period": "D",
            "trade_date": {"$gte": "2026-01-01", "$lte": "2026-11-13"},
        },
        {"_id": 0, "code": 1, "trade_date": 1, "close": 1},
    ).to_list(None)
    df = pd.DataFrame(rows)
    df["trade_date"] = df["trade_date"].astype(str)
    g = df[df.code == GOLD].sort_values("trade_date").set_index("trade_date")
    p = df[df.code == POLICY].sort_values("trade_date").set_index("trade_date")
    ix = df[df.code == INDEX_CODE].sort_values("trade_date").set_index("trade_date")
    print(
        "GOLD",
        float(g.close.iloc[0]),
        "->",
        float(g.close.iloc[-1]),
        "ret",
        float(g.close.iloc[-1] / g.close.iloc[0] - 1),
    )
    print(
        "POLICY",
        float(p.close.iloc[0]),
        "->",
        float(p.close.iloc[-1]),
        "ret",
        float(p.close.iloc[-1] / p.close.iloc[0] - 1),
    )
    print(
        "HS300",
        float(ix.close.iloc[0]),
        "->",
        float(ix.close.iloc[-1]),
        "ret",
        float(ix.close.iloc[-1] / ix.close.iloc[0] - 1),
    )
    g["ma20"] = g.close.rolling(20).mean()
    switch_days = [
        "2026-07-22",
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
        "2026-08-06",
        "2026-09-02",
        "2026-11-06",
        "2026-11-09",
        "2026-11-10",
        "2026-11-12",
    ]
    print("--- gold vs ma20 on switch days ---")
    for d in switch_days:
        if d not in g.index:
            print(d, "missing")
            continue
        i = g.index.get_loc(d)
        prev = float(g.close.iloc[i - 1]) if i > 0 else None
        close = float(g.close.loc[d])
        ma20 = float(g.ma20.loc[d])
        dret = None if prev is None else close / prev - 1
        print(
            d,
            f"close={close:.3f}",
            f"ma20={ma20:.3f}",
            f"above={close > ma20}",
            f"dret={dret:+.3%}" if dret is not None else "",
            f"policy={float(p.close.loc[d]):.3f}" if d in p.index else "",
        )
    # count days gold above ma20 in Jul-Nov
    sub = g.loc["2026-07-01":"2026-11-13"]
    above = (sub.close > sub.ma20).fillna(False)
    print("Jul-Nov gold above ma20 days", int(above.sum()), "/", len(above))
    # flips of above/below
    flips = above.astype(int).diff().abs().fillna(0).sum()
    print("Jul-Nov ma20 cross flips", int(flips))
    await close_zstock_database()


if __name__ == "__main__":
    asyncio.run(main())
