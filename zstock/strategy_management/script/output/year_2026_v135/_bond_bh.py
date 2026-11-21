# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pandas as pd

from zstock.common.book_codes import (
    BOND,
    BOND30,
    BOND_FALLBACK,
    GOLD,
    INDEX_CODE,
    INDUSTRY_ETFS,
    POLICY,
)
from zstock.common.utils.db_utils import close_zstock_database, get_database, init_zstock_database


async def main() -> None:
    await init_zstock_database()
    db = get_database()
    codes = [BOND, BOND_FALLBACK, BOND30, GOLD, POLICY, INDEX_CODE, *INDUSTRY_ETFS]
    rows = await db["zstock_ohlcv"].find(
        {
            "code": {"$in": codes},
            "period": "D",
            "trade_date": {"$gte": "2026-01-05", "$lte": "2026-11-13"},
        },
        {"_id": 0, "code": 1, "trade_date": 1, "close": 1},
    ).to_list(None)
    df = pd.DataFrame(rows)
    df["trade_date"] = df["trade_date"].astype(str)
    for c in [BOND, BOND_FALLBACK, BOND30, POLICY, GOLD, INDEX_CODE]:
        s = df[df.code == c].sort_values("trade_date")
        if s.empty:
            print(c, "MISSING")
            continue
        r = float(s.close.iloc[-1] / s.close.iloc[0] - 1)
        print(
            f"{c} {s.trade_date.iloc[0]}->{s.trade_date.iloc[-1]} "
            f"n={len(s)} ret={r*100:+.2f}% {s.close.iloc[0]:.3f}->{s.close.iloc[-1]:.3f}"
        )
    b = df[df.code == BOND].sort_values("trade_date").set_index("trade_date")["close"].astype(float)
    b30 = df[df.code == BOND30].sort_values("trade_date").set_index("trade_date")["close"].astype(float)
    pol = df[df.code == POLICY].sort_values("trade_date").set_index("trade_date")["close"].astype(float)
    g = df[df.code == GOLD].sort_values("trade_date").set_index("trade_date")["close"].astype(float)
    common = b.index.intersection(b30.index).intersection(pol.index)
    basket = (
        0.35 * (b.loc[common] / b.loc[common].iloc[0])
        + 0.35 * (b30.loc[common] / b30.loc[common].iloc[0])
        + 0.30 * (pol.loc[common] / pol.loc[common].iloc[0])
    )
    print("BH defense 35/35/30 policy", f"{(float(basket.iloc[-1]) - 1) * 100:+.2f}%")
    common2 = common.intersection(g.index)
    basket_g = (
        0.35 * (b.loc[common2] / b.loc[common2].iloc[0])
        + 0.35 * (b30.loc[common2] / b30.loc[common2].iloc[0])
        + 0.30 * (g.loc[common2] / g.loc[common2].iloc[0])
    )
    print("BH defense 35/35/30 gold", f"{(float(basket_g.iloc[-1]) - 1) * 100:+.2f}%")
    print("BH 511260 only", f"{(float(b.iloc[-1] / b.iloc[0] - 1)) * 100:+.2f}%")
    await close_zstock_database()


if __name__ == "__main__":
    asyncio.run(main())
