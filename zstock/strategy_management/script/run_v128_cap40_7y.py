"""
跑 v128_cap40 (单票上限 0.40 打满) 2020~2026 全 7 年完整数据
并与 prod_v124 基准进行全历史逐年对照
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (
    _latest_trade_date_in_factors,
    _run_one,
    _segment_summary_ok,
    _setup_logging,
    _year_ranges,
    run_segments_parallel,
)

CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"
PARAMS_CAP40 = CONFIG / "strategy_params_v128_cap40.json"
PREV_2020_DIR = PROJECT_ROOT / "zstock" / "strategy_management" / "script" / "output" / "cap_compare_2020_20261008_215326" / "v128_cap40_2020"
PROD_7Y_DIR = PROJECT_ROOT / "zstock" / "strategy_management" / "script" / "output" / "path_a_7y_20261002_114148"

YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]


def _daily_stats(out_seg: Path) -> Dict[str, float]:
    curves = list(out_seg.glob("backtest_curve_*.csv"))
    trades = list(out_seg.glob("backtest_trades_*.csv"))
    if not curves or not trades:
        return {}
    curve = pd.read_csv(curves[0])
    tr = pd.read_csv(trades[0])
    curve["date"] = curve["date"].astype(str)
    tr["trade_date"] = tr["trade_date"].astype(str)
    m = curve[["date"]].merge(
        tr[["trade_date", "n_holdings"]].rename(columns={"trade_date": "date"}),
        on="date",
        how="left",
    )
    m["n_holdings"] = m["n_holdings"].ffill().fillna(0).astype(int)
    hd = list(out_seg.glob("backtest_holdings_*.csv"))
    avg_w = float("nan")
    if hd:
        h = pd.read_csv(hd[0])
        if not h.empty and "weight" in h.columns:
            avg_w = float(h.groupby("trade_date")["weight"].sum().mean())
    return {
        "days": len(m),
        "avg_n_holdings": float(m["n_holdings"].mean()),
        "pct_empty": float((m["n_holdings"] == 0).mean()),
        "avg_weight_sum_rebalance": avg_w,
    }


def _cagr(returns: List[float]) -> float:
    if not returns:
        return float("nan")
    p = 1.0
    for r in returns:
        p *= 1.0 + float(r)
    return p ** (1.0 / len(returns)) - 1.0


async def main() -> int:
    out = Path(__file__).resolve().parent / "output" / f"v128_cap40_7y_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")
    if "ZSTOCK_OHLCV_CONCURRENCY" not in os.environ:
        os.environ["ZSTOCK_OHLCV_CONCURRENCY"] = "4"

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    await init_zstock_database()

    end = await _latest_trade_date_in_factors()
    ranges = [(y, s, e) for y, s, e in _year_ranges(end) if str(y) in YEARS]

    jobs = []
    for year, start, end_y in ranges:
        seg = out / f"v128_cap40_{year}"
        seg.mkdir(parents=True, exist_ok=True)
        # 如果 2020 年已有完成数据，直接复制复用
        if str(year) == "2020" and PREV_2020_DIR.exists() and (PREV_2020_DIR / "summary.json").exists():
            for f in PREV_2020_DIR.iterdir():
                if f.is_file():
                    shutil.copy2(f, seg / f.name)
            print(f"✅ 2020 年直接复用已有成功回测结果: {PREV_2020_DIR}")
            continue

        jobs.append({
            "label": "v128_cap40",
            "start": start,
            "end": end_y,
            "params_path": PARAMS_CAP40,
            "out_seg": seg,
            "run": True,
        })

    t0 = time.time()
    if jobs:
        print(f"🚀 开始跑 2021~2026 年回测，共 {len(jobs)} 个年份，并发 workers=2...")
        await run_segments_parallel(jobs, workers=2)
    print(f"回测耗时: {(time.time()-t0)/60:.1f} min")

    # 提取并对比数据
    print("\n" + "="*80)
    print(f"📊 7年逐年对比：prod_v124 基准 vs v128_cap40 (单票上限0.40打满)")
    print(f"因子数据截止: {end}")
    print("="*80)

    prod_rets = []
    prod_mdds = []
    cap40_rets = []
    cap40_mdds = []
    cap40_stats = []

    rows = []
    for y in YEARS:
        # prod
        prod_f = PROD_7Y_DIR / f"prod_v124_{y}" / "summary.json"
        p_ret = float("nan")
        p_mdd = float("nan")
        p_shp = float("nan")
        if prod_f.exists():
            pd_d = json.loads(prod_f.read_text(encoding="utf-8"))
            p_ret = float(pd_d.get("total_return", float("nan")))
            p_mdd = float(pd_d.get("max_drawdown", float("nan")))
            p_shp = float(pd_d.get("sharpe", float("nan")))
            prod_rets.append(p_ret)
            prod_mdds.append(p_mdd)

        # cap40
        c40_seg = out / f"v128_cap40_{y}"
        c40_f = c40_seg / "summary.json"
        c_ret = float("nan")
        c_mdd = float("nan")
        c_shp = float("nan")
        if c40_f.exists():
            c_d = json.loads(c40_f.read_text(encoding="utf-8"))
            c_ret = float(c_d.get("total_return", float("nan")))
            c_mdd = float(c_d.get("max_drawdown", float("nan")))
            c_shp = float(c_d.get("sharpe", float("nan")))
            cap40_rets.append(c_ret)
            cap40_mdds.append(c_mdd)
            st = _daily_stats(c40_seg)
            if st:
                cap40_stats.append(st)

        diff = c_ret - p_ret if not (pd.isna(c_ret) or pd.isna(p_ret)) else float("nan")
        rows.append({
            "年份": y,
            "prod收益": f"{p_ret:+.2%}",
            "prod回撤": f"{p_mdd:+.2%}",
            "cap40收益": f"{c_ret:+.2%}",
            "cap40回撤": f"{c_mdd:+.2%}",
            "超额": f"{diff:+.2%}",
            "cap40夏普": f"{c_shp:.2f}",
        })

    df_report = pd.DataFrame(rows)
    print(df_report.to_string(index=False))

    prod_cagr = _cagr(prod_rets)
    cap40_cagr = _cagr(cap40_rets)
    prod_worst = min(prod_mdds) if prod_mdds else float("nan")
    cap40_worst = min(cap40_mdds) if cap40_mdds else float("nan")

    avg_n = sum(s["avg_n_holdings"] for s in cap40_stats) / len(cap40_stats) if cap40_stats else float("nan")
    pct_e = sum(s["pct_empty"] for s in cap40_stats) / len(cap40_stats) if cap40_stats else float("nan")
    avg_w = sum(s["avg_weight_sum_rebalance"] for s in cap40_stats) / len(cap40_stats) if cap40_stats else float("nan")

    summary_text = (
        f"\n{'='*80}\n"
        f"🎯 核心总结指标（全 7 年）：\n"
        f"prod_v124:    7y CAGR {prod_cagr:+.2%}, worst MDD {prod_worst:+.2%}\n"
        f"v128_cap40:   7y CAGR {cap40_cagr:+.2%}, worst MDD {cap40_worst:+.2%}\n"
        f"v128_cap40仓位统计: avg持仓只数 {avg_n:.2f}, 空仓日比例 {pct_e:.1%}, 再平衡平均总权重 {avg_w:.2%}\n"
        f"{'='*80}\n"
    )
    print(summary_text)

    # 写入 output summary
    (out / "7y_summary.txt").write_text(df_report.to_string(index=False) + "\n\n" + summary_text, encoding="utf-8")

    await close_zstock_database()
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(asyncio.run(main()))
