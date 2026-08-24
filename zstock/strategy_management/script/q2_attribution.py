"""
Q2 失败归因：对比 Q1/Q2/Q3 及跨年的 Q2，诊断 M1 灯号、regime、仓位、选股与收益贡献。

用法：
    cd E:\\TradingAgents-CN
    python -m zstock.strategy_management.script.q2_attribution

    python -m zstock.strategy_management.script.q2_attribution \\
        --output zstock/strategy_management/script/output/q2_attrib
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

SEGMENTS: List[Tuple[str, str, str]] = [
    ("2024_Q1", "2024-01-01", "2024-03-31"),
    ("2024_Q2", "2024-04-01", "2024-06-30"),
    ("2024_Q3", "2024-07-01", "2024-09-30"),
    ("2025_Q2", "2025-04-01", "2025-06-30"),
    ("2026_Q2", "2026-04-01", "2026-06-30"),
]


async def _trade_dates_in_range(factor_pipeline, start: str, end: str) -> List[str]:
    """优先用预加载 M1 缓存的实际交易日。"""
    cache = factor_pipeline._precomputed_cache
    if cache is not None and cache.loaded and cache._market:
        return sorted(d for d in cache._market.keys() if start <= d <= end)
    from zstock.strategy_management.script.backtester import Backtester

    return Backtester(fee_rate=0.0015)._gen_trade_dates(start, end)


async def _diagnose_segment(
    label: str,
    start: str,
    end: str,
    factor_pipeline,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

    dates = await _trade_dates_in_range(factor_pipeline, start, end)
    rows: List[Dict[str, Any]] = []

    for td in dates:
        row: Dict[str, Any] = {"trade_date": td, "segment": label}
        try:
            sig = await factor_pipeline.score_signals(td)
        except Exception as e:
            row.update({"status": "error", "error": str(e)[:120]})
            rows.append(row)
            continue

        grade = sig.attrs.get("market_grade", "?")
        regime = sig.attrs.get("regime", "neutral")
        scale = float(sig.attrs.get("position_scale", 1.0))
        n_universe = len(sig)
        n_buy = int((sig["signal_type"] == "buy").sum()) if "signal_type" in sig.columns else 0

        sectors = []
        if "sector_code" in sig.columns and n_buy > 0:
            buy = sig[sig["signal_type"] == "buy"] if "signal_type" in sig.columns else sig.head(5)
            sectors = buy["sector_code"].dropna().astype(str).tolist()

        top_scores = []
        avg_buy_score = None
        if n_buy > 0 and "final_score" in sig.columns:
            buy = sig[sig["signal_type"] == "buy"] if "signal_type" in sig.columns else sig.head(5)
            top_scores = buy["final_score"].head(3).tolist()
            avg_buy_score = float(buy["final_score"].mean())

        row.update({
            "status": "ok",
            "market_grade": grade,
            "regime": regime,
            "position_scale": scale,
            "n_universe": n_universe,
            "n_buy": n_buy,
            "top_sectors": "|".join(sectors[:5]),
            "avg_buy_score": avg_buy_score,
            "top3_scores": top_scores,
        })
        rows.append(row)

    df = pd.DataFrame(rows)
    ok = df[df["status"] == "ok"] if not df.empty else df

    summary: Dict[str, Any] = {
        "segment": label,
        "start": start,
        "end": end,
        "trade_days": len(dates),
        "signal_ok_days": len(ok),
        "error_days": int((df["status"] == "error").sum()) if not df.empty else 0,
    }
    if ok.empty:
        return df, summary

    summary.update({
        "grade_green_pct": float((ok["market_grade"] == "green").mean() * 100),
        "grade_yellow_pct": float((ok["market_grade"] == "yellow").mean() * 100),
        "grade_red_pct": float((ok["market_grade"] == "red").mean() * 100),
        "avg_position_scale": float(ok["position_scale"].mean()),
        "avg_n_buy": float(ok["n_buy"].mean()),
        "zero_buy_days": int((ok["n_buy"] == 0).sum()),
        "regime_counts": dict(Counter(ok["regime"].astype(str))),
        "avg_universe": float(ok["n_universe"].mean()),
    })

    sector_counter: Counter = Counter()
    for s in ok["top_sectors"].dropna():
        for x in str(s).split("|"):
            if x:
                sector_counter[x] += 1
    summary["top_sectors"] = sector_counter.most_common(8)

    return df, summary


async def _backtest_segment(start: str, end: str) -> Dict[str, Any]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.factor_management.script.网格搜索.grid_search_real import load_ohlcv
    from zstock.strategy_management.script.backtester import Backtester, make_ohlcv_provider_from_dict

    ohlcv = await load_ohlcv(start, end)
    provider = make_ohlcv_provider_from_dict(ohlcv)
    fp = CrossSectionStrategyPipeline()
    bt = Backtester(fee_rate=0.0015, factor_pipeline=fp)
    result = await bt.run(
        start_date=start,
        end_date=end,
        ohlcv_provider=provider,
        use_precomputed_factors=True,
        verbose=False,
    )
    m = result.metrics
    exp = 0.0
    for snap in result.holdings_log or []:
        hs = snap.get("holdings") or []
        exp += sum(float(h.get("weight", 0)) for h in hs) if hs else 0.0
    exp /= max(len(result.holdings_log), 1)
    return {
        "total_return": float(m.get("total_return", 0)),
        "sharpe": float(m.get("sharpe", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
        "total_cost": float(m.get("total_cost", 0)),
        "rebalance_count": int(m.get("rebalance_count", 0)),
        "avg_exposure": exp,
    }


def _build_report(summaries: List[Dict[str, Any]], bt_rows: Dict[str, Dict]) -> str:
    lines = [
        "",
        "=" * 90,
        "Q2 失败归因报告",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 90,
        "",
        f"{'分段':<12} {'收益':>8} {'夏普':>7} {'回撤':>8} {'仓位':>6} {'黄灯%':>6} {'regime主导':>16} {'零信号天':>8}",
        "─" * 90,
    ]
    for s in summaries:
        label = s["segment"]
        bt = bt_rows.get(label, {})
        regimes = s.get("regime_counts") or {}
        dom = max(regimes, key=regimes.get) if regimes else "?"
        lines.append(
            f"{label:<12} {bt.get('total_return', 0)*100:>7.2f}% "
            f"{bt.get('sharpe', 0):>7.3f} {bt.get('max_drawdown', 0)*100:>7.2f}% "
            f"{bt.get('avg_exposure', 0)*100:>5.1f}% {s.get('grade_yellow_pct', 0):>5.1f}% "
            f"{dom:>16} {s.get('zero_buy_days', 0):>8}"
        )

    q2_2024 = next((s for s in summaries if s["segment"] == "2024_Q2"), {})
    q1_2024 = next((s for s in summaries if s["segment"] == "2024_Q1"), {})
    q3_2024 = next((s for s in summaries if s["segment"] == "2024_Q3"), {})

    lines += ["", "── 2024 Q2 vs Q1/Q3 差异 ──"]
    if q2_2024 and q1_2024:
        lines.append(
            f"  黄灯占比: Q1={q1_2024.get('grade_yellow_pct', 0):.1f}%  "
            f"Q2={q2_2024.get('grade_yellow_pct', 0):.1f}%  "
            f"Q3={q3_2024.get('grade_yellow_pct', 0):.1f}%"
        )
        lines.append(
            f"  平均仓位scale: Q1={q1_2024.get('avg_position_scale', 0):.2f}  "
            f"Q2={q2_2024.get('avg_position_scale', 0):.2f}  "
            f"Q3={q3_2024.get('avg_position_scale', 0):.2f}"
        )
        lines.append(
            f"  平均买入数: Q1={q1_2024.get('avg_n_buy', 0):.1f}  "
            f"Q2={q2_2024.get('avg_n_buy', 0):.1f}  "
            f"Q3={q3_2024.get('avg_n_buy', 0):.1f}"
        )

    lines += ["", "── 归因结论（自动）──"]
    conclusions = []
    if q2_2024.get("grade_yellow_pct", 0) > q1_2024.get("grade_yellow_pct", 0) + 5:
        conclusions.append("Q2 黄灯天数明显多于 Q1 → M1 降仓 + 自适应 reb=3 合理")
    if q2_2024.get("avg_n_buy", 99) < q1_2024.get("avg_n_buy", 0) - 0.5:
        conclusions.append("Q2 可买标的减少 → 检查 M4 coop 过滤与龙头因子衰减")
    r24 = q2_2024.get("regime_counts") or {}
    if r24.get("reversal", 0) > r24.get("momentum", 0):
        conclusions.append("Q2 reversal regime 占主导 → 连板类动量因子权重应下调（factor_decay）")
    bt_q2 = bt_rows.get("2024_Q2", {})
    bt_q1 = bt_rows.get("2024_Q1", {})
    if bt_q2.get("total_cost", 0) > bt_q1.get("total_cost", 0) * 1.3:
        conclusions.append("Q2 交易成本偏高 → 自适应再平衡避免 reversal 期频繁换仓")
    if not conclusions:
        conclusions.append("信号层差异不极端，Q2 亏损可能来自持仓个股 beta/行业暴露，需结合 segments CSV 查板块集中度")
    for c in conclusions:
        lines.append(f"  - {c}")

    lines += [
        "",
        "── 建议动作 ──",
        "  1. strategy_params v1.14: factor_decay 下调 f33_consecutive_boards",
        "  2. adaptive_rebalance: yellow/reversal → reb=3, green/momentum → reb=5",
        "  3. 用 v1.14 重跑 2024 Q2 / 2026 Q2 验证改善幅度",
        "",
        "=" * 90,
        "",
    ]
    return "\n".join(lines)


async def async_main(args: argparse.Namespace) -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

    for name in ("zstock", "zstock.factor_management", "app"):
        logging.getLogger(name).setLevel(logging.WARNING)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        await init_zstock_database()
    except Exception:
        from app.core.database import db_manager
        await db_manager.init_mongodb()

    try:
        fp = CrossSectionStrategyPipeline()
        min_d, max_d = min(s[1] for s in SEGMENTS), max(s[2] for s in SEGMENTS)
        await fp.preload_precomputed_factors(min_d, max_d)

        all_daily: List[pd.DataFrame] = []
        summaries: List[Dict[str, Any]] = []
        bt_rows: Dict[str, Dict] = {}

        for label, start, end in SEGMENTS:
            logger.info("诊断 %s %s~%s", label, start, end)
            daily, summary = await _diagnose_segment(label, start, end, fp)
            all_daily.append(daily)
            summaries.append(summary)
            if args.with_backtest:
                logger.info("回测 %s", label)
                bt_rows[label] = await _backtest_segment(start, end)

        daily_df = pd.concat(all_daily, ignore_index=True)
        daily_df.to_csv(output_dir / "daily_diagnostics.csv", index=False, encoding="utf-8-sig")

        with open(output_dir / "segment_summary.json", "w", encoding="utf-8") as f:
            json.dump({"summaries": summaries, "backtest": bt_rows}, f, ensure_ascii=False, indent=2)

        report = _build_report(summaries, bt_rows)
        (output_dir / "q2_attribution_report.txt").write_text(report, encoding="utf-8")
        print(report)
        logger.info("输出: %s", output_dir)
        return 0
    finally:
        await close_zstock_database()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="Q2 失败归因")
    p.add_argument("--output", default=str(Path(__file__).resolve().parent / "output" / "q2_attribution"))
    p.add_argument("--with-backtest", action="store_true", help="每段跑快速回测（较慢）")
    return asyncio.run(async_main(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
