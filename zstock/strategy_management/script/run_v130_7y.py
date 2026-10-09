"""
跑 v130 (fill_open_slots=True + cap40 / cap50) 2020~2026 全 7 年完整回测
冲刺 7年 10% CAGR + 20% MDD 目标！
"""

from __future__ import annotations

import argparse
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
    _segment_summary_ok,
    _setup_logging,
    _year_ranges,
    run_segments_parallel,
)

CONFIG_DIR = PROJECT_ROOT / "zstock" / "common" / "config"
PARAMS_CAP40_FILL = CONFIG_DIR / "strategy_params_v130_cap40_fill_slots.json"
PARAMS_CAP50_FILL = CONFIG_DIR / "strategy_params_v130_cap50_fill_slots.json"

PROD_7Y_DIR = PROJECT_ROOT / "zstock" / "strategy_management" / "script" / "output" / "path_a_7y_20261002_114148"
V128_7Y_DIR = PROJECT_ROOT / "zstock" / "strategy_management" / "script" / "output" / "v128_cap40_7y_20261008_221221"
V129_7Y_DIR = PROJECT_ROOT / "zstock" / "strategy_management" / "script" / "output" / "overnight_opt_20261008_225018" / "phase2_7y_finals"

# 之前压力测试已跑出的 2020/2021 目录，可复用
STRESS_DIR = PROJECT_ROOT / "zstock" / "strategy_management" / "script" / "output" / "v130_stress_20261009_083825"

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


async def run_7y_for_variant(
    variant_label: str,
    params_path: Path,
    out_root: Path,
    reuse_stress_dir: Path | None = None,
    workers: int = 2,
) -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print(f"🚀 开始跑 {variant_label} 全 7 年回测 (2020~2026)...")
    print(f"配置文件: {params_path}")
    print("=" * 80)

    end = await _latest_trade_date_in_factors()
    ranges = [(y, s, e) for y, s, e in _year_ranges(end) if str(y) in YEARS]

    jobs = []
    for year, start, end_y in ranges:
        seg = out_root / f"{variant_label}_{year}"
        seg.mkdir(parents=True, exist_ok=True)

        # 检查是否可以从 stress_dir 复用
        stress_src = None
        if reuse_stress_dir and reuse_stress_dir.exists():
            cand = reuse_stress_dir / f"{variant_label}_{year}"
            if cand.exists() and (cand / "summary.json").exists():
                stress_src = cand

        if stress_src:
            for f in stress_src.iterdir():
                if f.is_file():
                    shutil.copy2(f, seg / f.name)
            print(f"✅ {year} 年直接复用已有结果: {stress_src}")
            continue

        if (seg / "summary.json").exists():
            print(f"✅ {year} 年已有 summary.json，跳过回测")
            continue

        jobs.append({
            "label": variant_label,
            "start": start,
            "end": end_y,
            "params_path": params_path,
            "out_seg": seg,
            "run": True,
        })

    t0 = time.time()
    if jobs:
        print(f"🚀 需计算 {len(jobs)} 个年份，启动并发 workers={workers}...")
        await run_segments_parallel(jobs, workers=workers)
    print(f"回测耗时: {(time.time()-t0)/60:.1f} min")

    # 收集 7 年指标
    rets, mdds, shps, excesses, stats = [], [], [], [], []
    for y in YEARS:
        seg = out_root / f"{variant_label}_{y}"
        f = seg / "summary.json"
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            rets.append(float(d.get("total_return", float("nan"))))
            mdds.append(float(d.get("max_drawdown", float("nan"))))
            shps.append(float(d.get("sharpe", float("nan"))))
            excesses.append(float(d.get("excess_vs_index", float("nan"))))
            st = _daily_stats(seg)
            if st:
                stats.append(st)
        else:
            rets.append(float("nan"))
            mdds.append(float("nan"))
            shps.append(float("nan"))
            excesses.append(float("nan"))

    cagr = _cagr([r for r in rets if not pd.isna(r)])
    worst_mdd = min([m for m in mdds if not pd.isna(m)]) if any(not pd.isna(m) for m in mdds) else float("nan")
    avg_n = sum(s["avg_n_holdings"] for s in stats) / len(stats) if stats else float("nan")
    pct_e = sum(s["pct_empty"] for s in stats) / len(stats) if stats else float("nan")
    avg_w = sum(s["avg_weight_sum_rebalance"] for s in stats) / len(stats) if stats else float("nan")

    return {
        "variant": variant_label,
        "years": YEARS,
        "returns": rets,
        "mdds": mdds,
        "sharpes": shps,
        "excesses": excesses,
        "cagr": cagr,
        "worst_mdd": worst_mdd,
        "avg_holdings": avg_n,
        "pct_empty": pct_e,
        "avg_weight": avg_w,
    }


def _load_baseline(folder: Path, prefix: str) -> Dict[str, Any]:
    rets, mdds = [], []
    for y in YEARS:
        f = folder / f"{prefix}_{y}" / "summary.json"
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            rets.append(float(d.get("total_return", float("nan"))))
            mdds.append(float(d.get("max_drawdown", float("nan"))))
        else:
            rets.append(float("nan"))
            mdds.append(float("nan"))
    cagr = _cagr([r for r in rets if not pd.isna(r)])
    worst = min([m for m in mdds if not pd.isna(m)]) if any(not pd.isna(m) for m in mdds) else float("nan")
    return {"returns": rets, "mdds": mdds, "cagr": cagr, "worst_mdd": worst}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["cap40", "cap50", "both"], default="cap40")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    timestamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    out_dir = PROJECT_ROOT / "zstock" / "strategy_management" / "script" / "output" / f"v130_7y_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    if "ZSTOCK_OHLCV_CONCURRENCY" not in os.environ:
        os.environ["ZSTOCK_OHLCV_CONCURRENCY"] = "4"

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    await init_zstock_database()

    variants_to_run = []
    if args.variant in ("cap40", "both"):
        variants_to_run.append(("v130_cap40_fill", PARAMS_CAP40_FILL))
    if args.variant in ("cap50", "both"):
        variants_to_run.append(("v130_cap50_fill", PARAMS_CAP50_FILL))

    results = []
    for label, param_path in variants_to_run:
        res = await run_7y_for_variant(
            variant_label=label,
            params_path=param_path,
            out_root=out_dir,
            reuse_stress_dir=STRESS_DIR,
            workers=args.workers,
        )
        results.append(res)

    # 加载生产基准 prod_v124 与 v128_cap40, v129_cap50
    prod_base = _load_baseline(PROD_7Y_DIR, "prod_v124")
    v128_base = _load_baseline(V128_7Y_DIR, "v128_cap40")
    v129_base = _load_baseline(V129_7Y_DIR, "v129_cap50")

    # 构建汇总报表
    print("\n" + "=" * 100)
    print("🏆 【全 7 年历史大决战】最终对比报表（2020 ~ 2026）")
    print("目标：7 年 CAGR 冲刺 10%，全历史 Worst MDD 控制在 -20% 左右")
    print("=" * 100)

    rows = []
    for idx, y in enumerate(YEARS):
        r_dict = {
            "年份": y,
            "prod_v124": f"{prod_base['returns'][idx]:+.2%} (mdd {prod_base['mdds'][idx]:+.2%})" if not pd.isna(prod_base['returns'][idx]) else "N/A",
            "v128_cap40": f"{v128_base['returns'][idx]:+.2%} (mdd {v128_base['mdds'][idx]:+.2%})" if not pd.isna(v128_base['returns'][idx]) else "N/A",
            "v129_cap50": f"{v129_base['returns'][idx]:+.2%} (mdd {v129_base['mdds'][idx]:+.2%})" if not pd.isna(v129_base['returns'][idx]) else "N/A",
        }
        for res in results:
            ret = res["returns"][idx]
            mdd = res["mdds"][idx]
            r_dict[res["variant"]] = f"{ret:+.2%} (mdd {mdd:+.2%})" if not pd.isna(ret) else "N/A"
        rows.append(r_dict)

    # 汇总行
    cagr_row = {
        "年份": "【7年 CAGR】",
        "prod_v124": f"{prod_base['cagr']:+.2%}",
        "v128_cap40": f"{v128_base['cagr']:+.2%}",
        "v129_cap50": f"{v129_base['cagr']:+.2%}",
    }
    mdd_row = {
        "年份": "【Worst MDD】",
        "prod_v124": f"{prod_base['worst_mdd']:+.2%}",
        "v128_cap40": f"{v128_base['worst_mdd']:+.2%}",
        "v129_cap50": f"{v129_base['worst_mdd']:+.2%}",
    }
    for res in results:
        cagr_row[res["variant"]] = f"{res['cagr']:+.2%}"
        mdd_row[res["variant"]] = f"{res['worst_mdd']:+.2%}"

    rows.append(cagr_row)
    rows.append(mdd_row)

    df_report = pd.DataFrame(rows)
    report_text = df_report.to_string(index=False)
    print(report_text)

    # 核心目标达成评估
    verdict_lines = ["\n🎯 核心目标达成度评定："]
    for res in results:
        cagr = res["cagr"]
        worst = res["worst_mdd"]
        cagr_ok = "✅ 达标 (>=10%)" if cagr >= 0.10 else f"⚠️ 差距 {(0.10 - cagr):.2%}"
        mdd_ok = "✅ 达标 (<=20%)" if worst >= -0.20 else f"❌ 超标 {(abs(worst) - 0.20):.2%}"
        verdict_lines.append(
            f"- {res['variant']}: 7y CAGR = {cagr:+.2%} ({cagr_ok}), Worst MDD = {worst:+.2%} ({mdd_ok})\n"
            f"  [持仓统计] 平均持仓只数: {res['avg_holdings']:.2f} 只, 空仓日比例: {res['pct_empty']:.1%}, 再平衡平均总权重: {res['avg_weight']:.2%}"
        )

    verdict_text = "\n".join(verdict_lines)
    print(verdict_text)

    final_summary_file = out_dir / "7y_final_summary.txt"
    final_summary_file.write_text(report_text + "\n" + verdict_text + "\n", encoding="utf-8")
    print(f"\n📁 报表已落盘: {final_summary_file}")

    await close_zstock_database()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
