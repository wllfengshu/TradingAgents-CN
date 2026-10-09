"""prod vs v128 打满权重 × 2020/2024/2025 + 持仓/空仓统计。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (  # noqa: E402
    _latest_trade_date_in_factors,
    _run_one,
    _segment_summary_ok,
    _setup_logging,
    _year_ranges,
    run_segments_parallel,
)

CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"
BATCH = [
    ("prod_v124", CONFIG / "strategy_params.json"),
    ("v128", CONFIG / "strategy_params_v128_full_deploy.json"),
]
KEY = ("2020", "2024", "2025")


def _has_error_summary(sp: Path) -> bool:
    if not sp.is_file():
        return False
    try:
        return bool(json.loads(sp.read_text(encoding="utf-8")).get("error"))
    except Exception:
        return False


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


def _cagr(rows: List[Dict[str, Any]]) -> Tuple[float, float]:
    ok = [r for r in rows if r.get("total_return") is not None and not r.get("error")]
    if not ok:
        return float("nan"), float("nan")
    p = 1.0
    worst = 0.0
    for r in sorted(ok, key=lambda x: x["start"]):
        p *= 1.0 + float(r["total_return"])
        worst = min(worst, float(r.get("max_drawdown") or 0))
    return p ** (1.0 / len(ok)) - 1.0, worst


async def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--resume", type=Path, default=None)
    p.add_argument("--workers", type=int, default=2)
    args = p.parse_args()
    if args.resume:
        out = args.resume.resolve()
    else:
        out = Path(__file__).resolve().parent / "output" / f"v128_deploy_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")
    if "ZSTOCK_OHLCV_CONCURRENCY" not in os.environ:
        os.environ["ZSTOCK_OHLCV_CONCURRENCY"] = "4"

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    end = await _latest_trade_date_in_factors()
    ranges = [(y, s, e) for y, s, e in _year_ranges(end) if str(y) in KEY]
    jobs = []
    for label, path in BATCH:
        for year, start, end_y in ranges:
            seg = out / f"{label}_{year}"
            seg.mkdir(parents=True, exist_ok=True)
            jobs.append(
                {
                    "label": label,
                    "start": start,
                    "end": end_y,
                    "params_path": path,
                    "out_seg": seg,
                    "run": not _segment_summary_ok(seg / "summary.json")
                    or _has_error_summary(seg / "summary.json"),
                }
            )
    t0 = time.time()
    pending = [j for j in jobs if j["run"]]
    if pending:
        await run_segments_parallel(pending, workers=max(1, args.workers))

    lines = [f"v128 打满权重 vs prod | 因子截止 {end}", ""]
    for label, _ in BATCH:
        rows = []
        stats_acc = []
        for year, start, end_y in ranges:
            seg = out / f"{label}_{year}"
            sp = seg / "summary.json"
            if _segment_summary_ok(sp):
                rows.append(json.loads(sp.read_text(encoding="utf-8")))
                st = _daily_stats(seg)
                if st:
                    stats_acc.append(st)
        cagr, worst = _cagr(rows)
        if stats_acc:
            avg_n = sum(s["avg_n_holdings"] for s in stats_acc) / len(stats_acc)
            pct_e = sum(s["pct_empty"] for s in stats_acc) / len(stats_acc)
            avg_w = sum(s["avg_weight_sum_rebalance"] for s in stats_acc) / len(stats_acc)
        else:
            avg_n = pct_e = avg_w = float("nan")
        yr = {r["start"][:4]: float(r["total_return"]) for r in rows if r.get("total_return") is not None}
        lines.append(
            f"{label}: 3yCAGR {cagr:+.2%} worstMDD {worst:+.2%} | "
            f"avg只数 {avg_n:.2f} 空仓日 {pct_e:.1%} 再平衡均权重 {avg_w:.2%} | "
            + " ".join(f"{y}{yr.get(y, float('nan')):+.1%}" for y in KEY)
        )
    lines.append(f"\n用时 {(time.time()-t0)/60:.1f} min\n{out}")
    text = "\n".join(lines)
    (out / "summary.txt").write_text(text, encoding="utf-8")
    print(text)
    await close_zstock_database()
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(asyncio.run(main()))
