# -*- coding: utf-8 -*-
"""v136 按标的归因：再平衡持仓前向填充 × OHLCV 日收益。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pandas as pd

from zstock.common.book_codes import (
    BOND,
    BOND30,
    BOND_FALLBACK,
    GOLD,
    INDUSTRY_ETFS,
    POLICY,
)
from zstock.common.utils.db_utils import close_zstock_database, get_database, init_zstock_database

OUT = Path(__file__).resolve().parent
NAMES = {
    BOND: "十年国债ETF",
    BOND_FALLBACK: "五年国债ETF",
    BOND30: "三十年国债ETF",
    GOLD: "黄金ETF",
    POLICY: "政金债ETF",
    "512800": "银行ETF",
    "512010": "医药ETF",
    "512400": "有色ETF",
    "512480": "半导体ETF",
    "512880": "证券ETF",
    "512660": "军工ETF",
    "159928": "消费ETF",
}


def bucket(code: str) -> str:
    if code in {BOND, BOND_FALLBACK, BOND30, POLICY}:
        return "债基"
    if code == GOLD:
        return "黄金"
    if code in INDUSTRY_ETFS:
        return "行业ETF"
    return "个股"


def _norm_code(c: object) -> str:
    s = str(c).replace(".0", "")
    return s.zfill(6) if s.isdigit() else s


async def main() -> None:
    curve = pd.read_csv(sorted(OUT.glob("backtest_curve_*.csv"))[-1])
    holds = pd.read_csv(sorted(OUT.glob("backtest_holdings_*.csv"))[-1])
    curve["date"] = pd.to_datetime(curve["date"]).dt.strftime("%Y-%m-%d")
    holds["trade_date"] = pd.to_datetime(holds["trade_date"]).dt.strftime("%Y-%m-%d")
    holds["code"] = holds["code"].map(_norm_code)
    dates = curve["date"].tolist()

    # 每个再平衡日是完整仓位快照：未出现的标的权重=0，再按日 ffill
    all_codes = sorted(holds["code"].unique().tolist())
    snap_rows = []
    for td, g in holds.groupby("trade_date"):
        s = g.groupby("code")["weight"].sum().reindex(all_codes).fillna(0.0)
        s.name = td
        snap_rows.append(s)
    piv = pd.DataFrame(snap_rows).reindex(dates).ffill().fillna(0.0)
    codes = list(piv.columns)

    await init_zstock_database()
    db = get_database()
    rows_raw = await db["zstock_ohlcv"].find(
        {
            "code": {"$in": codes},
            "period": "D",
            "trade_date": {"$gte": dates[0], "$lte": dates[-1]},
        },
        {"_id": 0, "code": 1, "trade_date": 1, "close": 1, "pct_chg": 1},
    ).to_list(None)
    name_map = dict(NAMES)
    stock_codes = [c for c in codes if bucket(c) == "个股"]
    if stock_codes:
        coll_names = await db.list_collection_names()
        basic_coll = next(
            (
                n
                for n in (
                    "stock_basic_info",
                    "zstock_stock_basic",
                    "stock_basic",
                    "basic_info",
                )
                if n in coll_names
            ),
            None,
        )
        if basic_coll:
            basics = await db[basic_coll].find(
                {"code": {"$in": stock_codes}},
                {"_id": 0, "code": 1, "name": 1, "stock_name": 1},
            ).to_list(None)
            for b in basics:
                nm = b.get("name") or b.get("stock_name") or b["code"]
                name_map[_norm_code(b["code"])] = str(nm)
    await close_zstock_database()

    ohlcv = pd.DataFrame(rows_raw)
    ohlcv["trade_date"] = ohlcv["trade_date"].astype(str)
    ohlcv["code"] = ohlcv["code"].map(_norm_code)
    ohlcv["close"] = pd.to_numeric(ohlcv["close"], errors="coerce")
    ohlcv = ohlcv.sort_values(["code", "trade_date"])
    # 一律用收盘价环比，避免 pct_chg 缺测/口径不一
    ohlcv["ret"] = ohlcv.groupby("code")["close"].pct_change()

    close_wide = (
        ohlcv.pivot_table(index="trade_date", columns="code", values="close")
        .reindex(dates)
        .ffill()
    )
    ret_wide = close_wide.pct_change().fillna(0.0)
    w = piv.reindex(columns=ret_wide.columns).fillna(0.0)
    # 与回测主循环接近：持仓更新后按当日收盘收益计（再平衡日用新权重）
    contrib = w * ret_wide
    curve_gross = (
        pd.to_numeric(curve.set_index("date")["daily_return"], errors="coerce").fillna(0.0)
        + pd.to_numeric(curve.set_index("date")["cost"], errors="coerce").fillna(0.0)
    )
    explained = contrib.sum(axis=1)
    residual_daily = float((curve_gross - explained).sum())

    rows = []
    for code in w.columns:
        ww = w[code]
        held = ww > 1e-8
        if not held.any():
            continue
        days = int(held.sum())
        avg_w = float(ww[held].mean())
        max_w = float(ww.max())
        exposure = float(ww.sum() / len(dates))
        c_sum = float(contrib[code].sum())
        sub = ohlcv[ohlcv["code"] == code].sort_values("trade_date")
        held_dates = set(ww[held].index)
        sub_h = sub[sub["trade_date"].isin(held_dates)]
        bh = None
        if len(sub_h) >= 2 and sub_h["close"].notna().all():
            bh = float(sub_h["close"].iloc[-1] / float(sub_h["close"].iloc[0]) - 1.0)
        flags = held.astype(int).tolist()
        segments = 0
        prev = 0
        for f in flags:
            if f and not prev:
                segments += 1
            prev = f
        rows.append(
            {
                "code": code,
                "name": name_map.get(code, code),
                "bucket": bucket(code),
                "days_held": days,
                "segments": segments,
                "first": str(ww[held].index.min()),
                "last": str(ww[held].index.max()),
                "avg_weight": round(avg_w, 4),
                "max_weight": round(max_w, 4),
                "time_exposure": round(exposure, 4),
                "contrib_pct": round(c_sum * 100.0, 3),
                "held_bh_pct": None if bh is None else round(bh * 100.0, 2),
            }
        )

    df = pd.DataFrame(rows).sort_values("contrib_pct", ascending=False)
    port_ret = float(curve["equity"].iloc[-1] / curve["equity"].iloc[0] - 1.0) * 100.0
    cost = float(curve["cost"].sum()) * 100.0
    by_bucket = (
        df.groupby("bucket", as_index=False)
        .agg(
            codes=("code", "count"),
            exposure=("time_exposure", "sum"),
            contrib_pct=("contrib_pct", "sum"),
        )
        .sort_values("contrib_pct", ascending=False)
    )
    summary = {
        "period": f"{dates[0]} ~ {dates[-1]}",
        "n_days": len(dates),
        "nav_ret_pct": round(port_ret, 3),
        "cost_pct": round(cost, 3),
        "gross_arith_pct": round(float(curve_gross.sum()) * 100.0, 3),
        "sum_code_contrib_pct": round(float(df["contrib_pct"].sum()), 3),
        "residual_pct": round(residual_daily * 100.0, 3),
        "note": "持仓仅再平衡日快照并前向填充；硬止损未写入快照会产生残差。贡献为算术加总(权重×日收益)，与复利净值不可直接相加。",
    }
    payload = {
        "summary": summary,
        "by_bucket": by_bucket.to_dict(orient="records"),
        "by_code": df.to_dict(orient="records"),
    }
    (OUT / "per_code_attr.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    df.to_csv(OUT / "per_code_attr.csv", index=False, encoding="utf-8-sig")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("--- by_bucket ---")
    print(by_bucket.to_string(index=False))
    print("--- by_code ---")
    print(df.to_string(index=False))


if __name__ == "__main__":
    asyncio.run(main())
