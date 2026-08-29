"""核对主力净流入：原始资金流 vs 预计算 fcoop1。用法: python -m zstock.strategy_management.script.verify_main_flow 601211 2024-04-01"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WAN_TO_YUAN = 10000.0


async def verify(code: str, trade_date: str) -> None:
    from zstock.common.utils.common_utils import WAN_TO_YUAN as W2Y
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import get_data_query_service, PERIOD_L2_DAILY
    from zstock.factor_management.force_factors import ForceFactors

    await init_zstock_database()
    try:
        qs = get_data_query_service()
        td = trade_date

        # 1. 原始 L2 日终资金流
        try:
            flow_doc, src = await qs.get_capital_flow(code, td, period=PERIOD_L2_DAILY)
        except ValueError as e:
            print(f"无资金流: {e}")
            flow_doc = None
            src = None

        # 2. 近 10 日资金流
        recent = await qs.get_capital_flow_recent_days([code], td, days=10, period=PERIOD_L2_DAILY)
        docs = list(recent.get(code, []))
        docs.sort(key=lambda x: x.get("trade_date", ""))

        # 3. OHLCV
        ohlcv, _ = await qs.get_ohlcv(code, "2024-03-15", "2024-04-15")
        ohlcv = ohlcv.sort_values("trade_date")

        # 4. 预计算 M4
        forces = await qs.get_factor_forces(td)
        pre = next((d for d in forces if str(d["code"]) == code), None)

        print(f"=== {code} @ {td} 主力净流入核对 ===\n")

        if flow_doc:
            print("--- MongoDB 原始 L2_daily 文档 (当日) ---")
            keys = [
                "trade_date", "code", "period",
                "main_net", "m_net", "s_net", "xl_net",
                "turnover", "turnover_rate",
            ]
            shown = {k: flow_doc.get(k) for k in keys if k in flow_doc}
            print(json.dumps(shown, ensure_ascii=False, indent=2, default=str))

            main_net_wan = float(flow_doc.get("main_net", 0) or 0)
            m_net = float(flow_doc.get("m_net", 0) or 0)
            s_net = float(flow_doc.get("s_net", 0) or 0)
            xl_net = float(flow_doc.get("xl_net", 0) or 0)
            turnover_flow = float(flow_doc.get("turnover", 0) or 0)

            main_yuan = main_net_wan * W2Y
            retail_yuan = (m_net + s_net) * W2Y
            xl_yuan = xl_net * W2Y

            row = ohlcv[ohlcv["trade_date"] == td]
            amount_ohlcv = float(row["amount"].iloc[0]) if not row.empty and "amount" in row.columns else 0
            close = float(row["close"].iloc[0]) if not row.empty else 0
            pct = float(row["pct_chg"].iloc[0]) if not row.empty and "pct_chg" in row.columns else None
            if pct is None and not row.empty:
                pct = row["close"].pct_change().iloc[0] * 100

            print("\n--- 手工换算 ---")
            print(f"  main_net (万):     {main_net_wan:+.4f}")
            print(f"  main_net (元):     {main_yuan:+,.0f}")
            print(f"  xl_net  超大单(万): {xl_net:+.4f}  → {xl_yuan:+,.0f} 元")
            print(f"  m_net+s_net 散(万): {m_net+s_net:+.4f}  → {retail_yuan:+,.0f} 元")
            print(f"  turnover 资金流字段: {turnover_flow:,.0f} 元")
            print(f"  amount  OHLCV成交额: {amount_ohlcv:,.0f} 元")
            print(f"  当日收盘/涨跌:      {close:.2f}  {pct:+.2f}%" if pct is not None else f"  收盘: {close:.2f}")

            if turnover_flow > 0:
                fcoop1_calc = main_yuan / turnover_flow
                print(f"\n  fcoop1 = main_net(元) / turnover = {fcoop1_calc:.4%}")
            if amount_ohlcv > 0:
                ratio_ohlcv = main_yuan / amount_ohlcv
                print(f"  若用 OHLCV amount 作分母: {ratio_ohlcv:.4%}")

            print(f"\n  主力是否净流入: {'是' if main_net_wan > 0 else '否'} (main_net 万)")

        print("\n--- 近 10 交易日 main_net (万) + 涨跌 ---")
        print(f"  {'日期':<12} {'main_net':>10} {'xl_net':>10} {'turnover(亿)':>12} {'close':>8} {'涨跌%':>8}")
        for d in docs:
            dtd = d.get("trade_date", "")
            mn = float(d.get("main_net", 0) or 0)
            xn = float(d.get("xl_net", 0) or 0)
            tv = float(d.get("turnover", 0) or 0) / 1e8
            r = ohlcv[ohlcv["trade_date"] == dtd]
            cl = float(r["close"].iloc[0]) if not r.empty else float("nan")
            pc = float(r["pct_chg"].iloc[0]) if not r.empty and "pct_chg" in r.columns else float("nan")
            mark = " <--" if dtd == td else ""
            print(f"  {dtd:<12} {mn:+10.2f} {xn:+10.2f} {tv:12.3f} {cl:8.2f} {pc:+8.2f}{mark}")

        if pre:
            print("\n--- 预计算 M4 (MongoDB zstock_factor_force) ---")
            for k in [
                "fcoop1_main_net_ratio", "fcoop7_super_large_net_ratio",
                "fcoop6_main_force_aggression", "fcoop8_main_flow_trend_5d",
                "fcoop3_sustained_days",
            ]:
                print(f"  {k}: {pre.get(k)}")

        # 用 ForceFactors 现场重算
        if docs:
            flow_map = {code: docs}
            ohlcv_map = {code: ohlcv}
            raw = ForceFactors.apply_cooperative_force_raw(
                [{"code": code, "sector_code": pre.get("sector_code", "?") if pre else "?"}],
                stock_flow_recent=flow_map,
                stock_ohlcv=ohlcv_map,
                trade_date=td,
            )
            if raw:
                print("\n--- ForceFactors.apply_cooperative_force_raw 现场重算 ---")
                r0 = raw[0]
                print(f"  fcoop1: {r0.get('fcoop1_main_net_ratio')}")
                print(f"  fcoop7: {r0.get('fcoop7_super_large_net_ratio')}")

        # 对比 600109
        print("\n=== 对比: 600109 国金证券 (同日龙头#1但 M4=0) ===")
        try:
            f109, _ = await qs.get_capital_flow("600109", td)
            print(f"  main_net(万): {float(f109.get('main_net',0)):+.4f}  turnover: {float(f109.get('turnover',0)):,.0f}")
            pre109 = next((d for d in forces if d["code"] == "600109"), None)
            if pre109:
                print(f"  预计算 fcoop1: {pre109.get('fcoop1_main_net_ratio')}")
        except ValueError:
            print("  600109 无当日资金流")

        print("\n--- 结论 ---")
        if flow_doc:
            mn = float(flow_doc.get("main_net", 0) or 0)
            if mn > 0:
                print("  原始 L2 数据: 主力净流入为正，与预计算 fcoop1>0 一致。")
                print("  但价格同期下跌 → 典型「价跌量/资金流入」背离，策略 M4 未校验价格方向。")
            else:
                print("  原始 L2 数据: 主力非净流入，与预计算矛盾 → 需查预计算/单位 bug。")
    finally:
        await close_zstock_database()


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "601211"
    td = sys.argv[2] if len(sys.argv) > 2 else "2024-04-01"
    asyncio.run(verify(code, td))


if __name__ == "__main__":
    main()
