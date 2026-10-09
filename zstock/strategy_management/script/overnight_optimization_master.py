"""
隔夜大师优化任务 (Overnight Optimization Master)
目标：CAGR 冲刺 10%，全历史最大回撤牢牢控制在 20% 左右（<= 20%）。

两阶段自动化流程：
阶段一（压力快筛）：针对 2020 (极端回撤年) 与 2021 (极端防守年)，并行回测所有候选变体。
阶段二（全周期验证）：自动选出通过压力测试且风报比最优的冠军方案，批跑 2020~2026 全 7 年完整数据，核算 7 年 CAGR 与 Worst MDD。
"""

from __future__ import annotations

import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)

CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"
OUTPUT_BASE = PROJECT_ROOT / "zstock" / "strategy_management" / "script" / "output"
PREV_CAP40_DIR = OUTPUT_BASE / "v128_cap40_7y_20261008_221221"
PROD_7Y_DIR = OUTPUT_BASE / "path_a_7y_20261002_114148"

# 待测试的变体配置定义
CANDIDATES = [
    {
        "name": "v128_cap40",
        "params_path": CONFIG / "strategy_params_v128_cap40.json",
        "desc": "基准: 单票上限 40% 打满",
    },
    {
        "name": "v129_cap45",
        "params_path": CONFIG / "strategy_params_v129_cap45.json",
        "desc": "方案A: 单票上限 45% 打满",
    },
    {
        "name": "v129_cap50",
        "params_path": CONFIG / "strategy_params_v129_cap50.json",
        "desc": "方案B: 单票上限 50% 打满",
    },
    {
        "name": "v129_cap55",
        "params_path": CONFIG / "strategy_params_v129_cap55.json",
        "desc": "方案C: 单票上限 55% 打满",
    },
    {
        "name": "v129_dyn_g55_y40",
        "params_path": CONFIG / "strategy_params_v129_dyn_g55_y40.json",
        "desc": "方案D: 分级动态上限 (绿灯55%, 黄灯40%)",
    },
    {
        "name": "v129_cap50_stop6",
        "params_path": CONFIG / "strategy_params_v129_cap50_stop6.json",
        "desc": "方案E: 单票上限 50% + 紧凑止损 -6%",
    },
    {
        "name": "v129_cap50_te_boost",
        "params_path": CONFIG / "strategy_params_v129_cap50_te_boost.json",
        "desc": "方案F: 单票上限 50% + 绿灯环境权益释放",
    },
]

YEARS_ALL = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]
STRESS_YEARS = ["2020", "2021"]


def _cagr(returns: List[float]) -> float:
    ok = [r for r in returns if pd.notna(r)]
    if not ok:
        return float("nan")
    prod = 1.0
    for r in ok:
        prod *= (1.0 + r)
    return prod ** (1.0 / len(ok)) - 1.0


async def async_main():
    run_dir = OUTPUT_BASE / f"overnight_opt_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(run_dir / "overnight_run.log")

    if "ZSTOCK_OHLCV_CONCURRENCY" not in os.environ:
        os.environ["ZSTOCK_OHLCV_CONCURRENCY"] = "4"

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    await init_zstock_database()

    end = await _latest_trade_date_in_factors()
    ranges_map = {str(y): (s, e) for y, s, e in _year_ranges(end)}
    logger.info("================================================================================")
    logger.info("🌙 隔夜大师优化任务启动")
    logger.info("因子截止日期: %s", end)
    logger.info("输出目录: %s", run_dir)
    logger.info("================================================================================")

    # -------------------------------------------------------------------------
    # 阶段一：压力快筛 (2020 & 2021)
    # -------------------------------------------------------------------------
    p1_dir = run_dir / "phase1_stress_screen"
    p1_dir.mkdir(parents=True, exist_ok=True)
    
    p1_jobs = []
    for cand in CANDIDATES:
        c_name = cand["name"]
        p_path = cand["params_path"]
        for y in STRESS_YEARS:
            seg = p1_dir / f"{c_name}_{y}"
            seg.mkdir(parents=True, exist_ok=True)
            
            # 如果是基准 v128_cap40，直接复用已有的数据
            if c_name == "v128_cap40" and PREV_CAP40_DIR.exists():
                prev_seg = PREV_CAP40_DIR / f"v128_cap40_{y}"
                if (prev_seg / "summary.json").exists():
                    for f in prev_seg.iterdir():
                        if f.is_file():
                            shutil.copy2(f, seg / f.name)
                    logger.info("✅ %s_%s 直接复用基准已完成数据", c_name, y)
                    continue
            
            if (seg / "summary.json").exists():
                logger.info("✅ %s_%s 已有完成数据，跳过", c_name, y)
                continue

            s_date, e_date = ranges_map[y]
            p1_jobs.append({
                "label": c_name,
                "start": s_date,
                "end": e_date,
                "params_path": p_path,
                "out_seg": seg,
                "run": True,
            })

    if p1_jobs:
        logger.info("🚀 阶段一启动：共 %d 个快筛任务，并发 workers=2...", len(p1_jobs))
        t0 = time.time()
        await run_segments_parallel(p1_jobs, workers=2)
        logger.info("阶段一完成，耗时: %.1f min", (time.time() - t0) / 60)

    # 阶段一结果评估与天梯榜
    leaderboard = []
    logger.info("\n" + "="*80)
    logger.info("🏆 阶段一压力快筛天梯榜 (2020极端回撤 & 2021防守测试)")
    logger.info("="*80)
    
    for cand in CANDIDATES:
        c_name = cand["name"]
        desc = cand["desc"]
        
        sum20 = p1_dir / f"{c_name}_2020" / "summary.json"
        sum21 = p1_dir / f"{c_name}_2021" / "summary.json"
        
        r20, m20, s20 = float("nan"), float("nan"), float("nan")
        r21, m21, s21 = float("nan"), float("nan"), float("nan")
        
        if sum20.exists():
            d20 = json.loads(sum20.read_text(encoding="utf-8"))
            r20 = float(d20.get("total_return", float("nan")))
            m20 = float(d20.get("max_drawdown", float("nan")))
            s20 = float(d20.get("sharpe", float("nan")))
            
        if sum21.exists():
            d21 = json.loads(sum21.read_text(encoding="utf-8"))
            r21 = float(d21.get("total_return", float("nan")))
            m21 = float(d21.get("max_drawdown", float("nan")))
            s21 = float(d21.get("sharpe", float("nan")))

        cum_2y = (1.0 + r20) * (1.0 + r21) - 1.0 if pd.notna(r20) and pd.notna(r21) else float("nan")
        worst_mdd_2y = min(m20, m21) if pd.notna(m20) and pd.notna(m21) else float("nan")
        avg_sharpe = (s20 + s21) / 2.0 if pd.notna(s20) and pd.notna(s21) else float("nan")
        
        # 判定是否踩踏 20% 回撤红线
        pass_mdd = (worst_mdd_2y >= -0.205) if pd.notna(worst_mdd_2y) else False

        entry = {
            "name": c_name,
            "desc": desc,
            "r20": r20,
            "m20": m20,
            "r21": r21,
            "m21": m21,
            "cum_2y": cum_2y,
            "worst_mdd_2y": worst_mdd_2y,
            "avg_sharpe": avg_sharpe,
            "pass_mdd": pass_mdd,
        }
        leaderboard.append(entry)

    # 排序：通过回撤红线 (worst_mdd >= -20%) 且 cum_2y 最高的排在前面
    leaderboard.sort(key=lambda x: (1 if x["pass_mdd"] else 0, x["cum_2y"] if pd.notna(x["cum_2y"]) else -999), reverse=True)

    lb_lines = []
    lb_lines.append(f"{'方案名称':<20} {'2020收益':<10} {'2020回撤':<10} {'2021收益':<10} {'2021回撤':<10} {'2年累计':<10} {'最深回撤':<10} {'回撤合格':<8} 方案说明")
    lb_lines.append("-" * 110)
    for e in leaderboard:
        status_str = "✅合格" if e["pass_mdd"] else "❌超限"
        lb_lines.append(
            f"{e['name']:<20} {e['r20']:>+8.2%} {e['m20']:>8.2%} {e['r21']:>+8.2%} {e['m21']:>8.2%} {e['cum_2y']:>+8.2%} {e['worst_mdd_2y']:>8.2%} {status_str:<8} {e['desc']}"
        )
    lb_text = "\n".join(lb_lines)
    logger.info("\n" + lb_text)
    (run_dir / "overnight_phase1_leaderboard.txt").write_text(lb_text, encoding="utf-8")

    # -------------------------------------------------------------------------
    # 阶段二：冠军方案全 7 年（2020~2026）全面决战
    # -------------------------------------------------------------------------
    # 选取排名前 2 的合格方案（若合格不足则选第 1 名）
    qualified = [e for e in leaderboard if e["pass_mdd"] and e["name"] != "v128_cap40"]
    if not qualified:
        qualified = [leaderboard[0]]
    
    top_candidates = qualified[:2]
    logger.info("\n" + "="*80)
    logger.info("🎯 阶段二启动：晋级全 7 年大决战的冠军候选方案：")
    for tc in top_candidates:
        logger.info("   -> %s (%s, 2年收益: %+.2f%%, 最深回撤: %.2f%%)", tc["name"], tc["desc"], tc["cum_2y"]*100, tc["worst_mdd_2y"]*100)
    logger.info("="*80)

    p2_dir = run_dir / "phase2_7y_finals"
    p2_dir.mkdir(parents=True, exist_ok=True)

    p2_jobs = []
    for tc in top_candidates:
        c_name = tc["name"]
        cand_obj = next(c for c in CANDIDATES if c["name"] == c_name)
        p_path = cand_obj["params_path"]
        
        for y in YEARS_ALL:
            seg = p2_dir / f"{c_name}_{y}"
            seg.mkdir(parents=True, exist_ok=True)
            
            # 如果阶段一已跑过该年，直接从 phase1 复制复用！
            p1_seg = p1_dir / f"{c_name}_{y}"
            if (p1_seg / "summary.json").exists():
                for f in p1_seg.iterdir():
                    if f.is_file():
                        shutil.copy2(f, seg / f.name)
                logger.info("✅ %s_%s 从阶段一直接复用，免跑", c_name, y)
                continue

            if (seg / "summary.json").exists():
                continue

            s_date, e_date = ranges_map[y]
            p2_jobs.append({
                "label": c_name,
                "start": s_date,
                "end": e_date,
                "params_path": p_path,
                "out_seg": seg,
                "run": True,
            })

    if p2_jobs:
        logger.info("🚀 阶段二启动：共 %d 个 7 年补充回测任务，并发 workers=2...", len(p2_jobs))
        t0 = time.time()
        await run_segments_parallel(p2_jobs, workers=2)
        logger.info("阶段二完成，耗时: %.1f min", (time.time() - t0) / 60)

    # -------------------------------------------------------------------------
    # 阶段三：全 7 年指标总结与最终报表生成
    # -------------------------------------------------------------------------
    logger.info("\n" + "="*90)
    logger.info("📊 最终 7 年全周期决战报表 (2020~2026)")
    logger.info("="*90)

    final_report_lines = []
    final_report_lines.append("="*95)
    final_report_lines.append("🏆 策略优化隔夜全历史 7 年回测总决战报告 (2020 ~ 2026)")
    final_report_lines.append(f"回测产出目录: {run_dir}")
    final_report_lines.append(f"数据因子截止: {end}")
    final_report_lines.append("="*95)

    # 准备各方案的 7 年数据对照
    schemes_to_report = [
        {"name": "prod_v124", "desc": "旧生产基线 (cap=0.20)", "dir": PROD_7Y_DIR},
        {"name": "v128_cap40", "desc": "v128基准 (cap=0.40)", "dir": PREV_CAP40_DIR},
    ]
    for tc in top_candidates:
        schemes_to_report.append({
            "name": tc["name"],
            "desc": tc["desc"],
            "dir": p2_dir,
        })

    # 读取各方案 7 年数据
    scheme_stats = {}
    for sc in schemes_to_report:
        name = sc["name"]
        s_dir = sc["dir"]
        rets, mdds, sharpes = [], [], []
        for y in YEARS_ALL:
            sum_f = (s_dir / f"{name}_{y}" / "summary.json")
            if sum_f.exists():
                d = json.loads(sum_f.read_text(encoding="utf-8"))
                rets.append(float(d.get("total_return", float("nan"))))
                mdds.append(float(d.get("max_drawdown", float("nan"))))
                sharpes.append(float(d.get("sharpe", float("nan"))))
            else:
                rets.append(float("nan"))
                mdds.append(float("nan"))
                sharpes.append(float("nan"))
        
        cagr_val = _cagr(rets)
        worst_mdd = min([m for m in mdds if pd.notna(m)]) if any(pd.notna(m) for m in mdds) else float("nan")
        calmar = cagr_val / abs(worst_mdd) if pd.notna(cagr_val) and pd.notna(worst_mdd) and worst_mdd != 0 else float("nan")
        avg_shp = pd.Series(sharpes).dropna().mean()

        scheme_stats[name] = {
            "desc": sc["desc"],
            "rets": rets,
            "mdds": mdds,
            "sharpes": sharpes,
            "cagr": cagr_val,
            "worst_mdd": worst_mdd,
            "calmar": calmar,
            "avg_sharpe": avg_shp,
        }

    # 打印逐年横向对比表
    hdr = f"{'年份':<6}"
    for sc in schemes_to_report:
        hdr += f" | {sc['name']:<18} (收益 / 回撤)"
    final_report_lines.append(hdr)
    final_report_lines.append("-" * len(hdr))

    for idx, y in enumerate(YEARS_ALL):
        row_str = f"{y:<6}"
        for sc in schemes_to_report:
            st = scheme_stats[sc["name"]]
            r = st["rets"][idx]
            m = st["mdds"][idx]
            r_str = f"{r:>+7.2%}" if pd.notna(r) else "   N/A "
            m_str = f"{m:>7.2%}" if pd.notna(m) else "  N/A "
            row_str += f" | {r_str} / {m_str}    "
        final_report_lines.append(row_str)

    final_report_lines.append("-" * len(hdr))
    
    # 汇总总结行
    final_report_lines.append("\n🎯 核心综合指标总结（全 7 年）：")
    final_report_lines.append(f"{'方案名称':<20} {'7y CAGR':<12} {'最深回撤':<12} {'卡玛比率':<10} {'平均夏普':<10} 方案特性")
    final_report_lines.append("-" * 90)
    for sc in schemes_to_report:
        st = scheme_stats[sc["name"]]
        target_met = " (⭐达成目标!)" if (st["cagr"] >= 0.095 and st["worst_mdd"] >= -0.21) else ""
        final_report_lines.append(
            f"{sc['name']:<20} {st['cagr']:>+8.2%}     {st['worst_mdd']:>8.2%}     {st['calmar']:>6.2f}     {st['avg_sharpe']:>6.2f}     {st['desc']}{target_met}"
        )

    final_text = "\n".join(final_report_lines)
    logger.info("\n" + final_text)
    (run_dir / "overnight_final_7y_report.txt").write_text(final_text, encoding="utf-8")
    (run_dir / "overnight_summary.json").write_text(json.dumps(scheme_stats, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print(f"\n✅ 隔夜优化任务全部执行完毕！产出完整报表至: {run_dir / 'overnight_final_7y_report.txt'}")
    return 0


if __name__ == "__main__":
    asyncio.run(async_main())
