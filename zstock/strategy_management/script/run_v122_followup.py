"""v122 七年存档 + prod/v122/v122b 2024/2025 快验。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (  # noqa: E402
    _latest_trade_date_in_factors,
    _run_one,
    _setup_logging,
    _write_report,
    _year_ranges,
)

CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"
FAST: List[Tuple[str, Path]] = [
    ("prod", CONFIG / "strategy_params.json"),
    ("v122", CONFIG / "strategy_params_v122_manual_3stock.json"),
    ("v122b", CONFIG / "strategy_params_v122b_hybrid.json"),
]
FAST_YEARS = {"2024", "2025"}
V122_PATH = CONFIG / "strategy_params_v122_manual_3stock.json"


def _cagr(rows: List[Dict[str, Any]]) -> float:
    ok = [r for r in rows if r.get("total_return") is not None]
    if not ok:
        return float("nan")
    p = 1.0
    for r in sorted(ok, key=lambda x: x["start"]):
        p *= 1.0 + float(r["total_return"])
    return p ** (1.0 / len(ok)) - 1.0


async def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).resolve().parent / "output" / f"v122_followup_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    t0 = time.time()
    end = await _latest_trade_date_in_factors()
    all_rows: List[Dict[str, Any]] = []

    hist_dir = out / "history_v122_7y"
    hist_dir.mkdir(parents=True, exist_ok=True)
    hist_rows: List[Dict[str, Any]] = []
    for i, (year, start, end_y) in enumerate(_year_ranges(end), 1):
        seg = hist_dir / f"v122_{year}"
        seg.mkdir(parents=True, exist_ok=True)
        print(f"[7y {i}/7] v122 {year}")
        row = await _run_one("v122", start, end_y, V122_PATH, seg)
        hist_rows.append(row)
        all_rows.append(row)
        _write_report(hist_dir, hist_rows, end)

    fast_rows: List[Dict[str, Any]] = []
    jobs = [(y, s, e) for y, s, e in _year_ranges(end) if str(y) in FAST_YEARS]
    for label, path in FAST:
        for y, s, e in jobs:
            seg = out / f"fast_{label}_{y}"
            seg.mkdir(parents=True, exist_ok=True)
            print(f"[fast] {label} {y}")
            fast_rows.append(await _run_one(label, s, e, path, seg))
    all_rows.extend(fast_rows)

    lines = [
        "v122 followup",
        f"因子末交易日: {end}",
        "",
        "=== v122 七年（逐年连乘 CAGR） ===",
    ]
    p7 = 1.0
    worst7 = 0.0
    for r in sorted(hist_rows, key=lambda x: x["start"]):
        y = r["start"][:4]
        tr = float(r["total_return"])
        mdd = float(r.get("max_drawdown") or 0)
        p7 *= 1.0 + tr
        worst7 = min(worst7, mdd)
        lines.append(f"  {y} ret {tr:+.2%} MDD {mdd:+.2%}")
    n7 = len(hist_rows)
    cagr7 = p7 ** (1.0 / n7) - 1.0 if n7 else float("nan")
    lines.append(f"  7y geo-mean/年 {cagr7:+.2%}  worstMDD {worst7:+.2%}")
    lines.append("")
    lines.append("=== 2024/2025 prod vs v122 vs v122b ===")
    for label in ("prod", "v122", "v122b"):
        sub = [r for r in fast_rows if r["label"] == label]
        by = {r["start"][:4]: r for r in sub}
        worst = min((float(r["max_drawdown"]) for r in sub if r.get("max_drawdown") is not None), default=0)
        parts = [f"{label} 2yCAGR {_cagr(sub):+.2%} worstMDD {worst:+.2%}"]
        for y in sorted(FAST_YEARS):
            parts.append(f"{y} {float(by[y]['total_return']):+.2%}")
        lines.append(" | ".join(parts))
    lines.append(f"\n用时 {(time.time() - t0) / 60:.1f} min")
    text = "\n".join(lines)
    (out / "summary.txt").write_text(text, encoding="utf-8")
    (out / "results.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
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
