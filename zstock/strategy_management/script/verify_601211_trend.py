"""验证 601211 在 2024-04-01 的价格趋势 vs 因子信号。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def main() -> None:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import get_data_query_service
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

    td = "2024-04-01"
    code = "601211"
    await init_zstock_database()
    try:
        qs = get_data_query_service()
        df, _ = await qs.get_ohlcv(code, "2024-02-01", "2024-04-30")
        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100
        df["ma5"] = df["close"].rolling(5).mean()
        df["ma20"] = df["close"].rolling(20).mean()
        df["ret_5d"] = df["close"].pct_change(5)
        df["ret_20d"] = df["close"].pct_change(20)

        print("=== 601211 国泰君安 K线 (2024-03-01 ~ 2024-04-15) ===")
        sub = df[(df["trade_date"] >= "2024-03-01") & (df["trade_date"] <= "2024-04-15")]
        cols = ["trade_date", "open", "high", "low", "close", "pct_chg", "ma5", "ma20", "ret_5d", "ret_20d"]
        print(sub[cols].to_string(index=False, float_format=lambda x: f"{x:.2f}"))

        row = df[df["trade_date"] == td].iloc[0]
        print(f"\n=== {td} 技术快照 ===")
        print(f"  close={row['close']:.2f}  ma5={row['ma5']:.2f}  ma20={row['ma20']:.2f}")
        print(f"  close < ma5: {row['close'] < row['ma5']}  close < ma20: {row['close'] < row['ma20']}")
        print(f"  ma5 < ma20 (空头排列): {row['ma5'] < row['ma20']}")
        print(f"  5日涨跌: {row['ret_5d']*100:.2f}%  20日涨跌: {row['ret_20d']*100:.2f}%")

        # 3/15 -> 4/12 段（买入后至4/8大跌前）
        seg = df[(df["trade_date"] >= "2024-03-15") & (df["trade_date"] <= "2024-04-12")]
        print(f"\n=== 3/15~4/12 走势 ===")
        print(f"  {seg.iloc[0]['trade_date']} close {seg.iloc[0]['close']:.2f} -> {seg.iloc[-1]['trade_date']} close {seg.iloc[-1]['close']:.2f}")
        print(f"  区间涨跌: {(seg.iloc[-1]['close']/seg.iloc[0]['close']-1)*100:+.2f}%")

        # 买入后表现 T+1 起
        post = df[df["trade_date"] >= td].head(10)
        print(f"\n=== 4/1 信号后 10 个交易日 ===")
        base = float(row["close"])
        for _, r in post.iterrows():
            chg = (r["close"] / base - 1) * 100
            print(f"  {r['trade_date']} close={r['close']:.2f} vs信号日 {chg:+.2f}%")

        # 因子 vs 趋势
        pipe = CrossSectionStrategyPipeline()
        sig = await pipe.score_signals(td)
        d = sig[sig["code"].astype(str) == code].iloc[0]
        dragons = await qs.get_factor_dragons(td)
        raw = next(x for x in dragons if x["code"] == code)

        print(f"\n=== 信号层 ({td}) ===")
        print(f"  regime={sig.attrs.get('regime')}  grade={sig.attrs.get('market_grade')}")
        print(f"  final_score={d['final_score']:.1f}  dragon={d['dragon_score']:.1f}  force={d['force_composite_score']:.1f}")

        print(f"\n=== M3 原始因子 vs 实际趋势 ===")
        keys = [
            "f31_excess_return_5d", "f31_excess_return_10d", "f31_excess_return_20d",
            "f33_consecutive_boards", "f35_bollinger_pass", "f35_bollinger_trend",
            "f37_relative_strength", "f36_identity_premium",
        ]
        for k in keys:
            print(f"  {k}: {raw.get(k)}")

        print("\n=== 结论核对 ===")
        downtrend = row["close"] < row["ma20"] and row["ret_20d"] < 0
        excess_neg = float(raw.get("f31_excess_return_5d", 0)) < 0
        boll_fail = float(raw.get("f35_bollinger_pass", 0)) == 0
        print(f"  K线下降趋势: {downtrend}")
        print(f"  因子超额收益为负: {excess_neg}")
        print(f"  布林过滤未通过(f35_pass=0): {boll_fail}")
        print(f"  策略仍买入原因: M4主力流入强(fcoop1=9.85%) + 板块内M4幸存者 + 合力分归一化#1")
    finally:
        await close_zstock_database()


if __name__ == "__main__":
    asyncio.run(main())
