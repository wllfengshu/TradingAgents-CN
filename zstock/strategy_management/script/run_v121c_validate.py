"""prod vs v121c × 2020,2021,2022,2024,2025。"""
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
    _year_ranges,
)

CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"
BATCH = [
    ("prod", CONFIG / "strategy_params.json"),
    ("v121c", CONFIG / "strategy_params_v121c_mg_m4_off.json"),
]
YEARS = {"2020", "2021", "2022", "2024", "2025"}


async def main() -> int:
    out = Path(__file__).resolve().parent / "output" / f"v121c_validate_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    t0 = time.time()
    rows: List[Dict[str, Any]] = []
    end = await _latest_trade_date_in_factors()
    for label, path in BATCH:
        for y, s, e in _year_ranges(end):
            if str(y) not in YEARS:
                continue
            seg = out / f"{label}_{y}"
            seg.mkdir(parents=True, exist_ok=True)
            rows.append(await _run_one(label, s, e, path, seg))
    await close_zstock_database()

    def cagr(lbl: str) -> float:
        ok = [r for r in rows if r["label"] == lbl and r.get("total_return") is not None]
        p = 1.0
        for r in sorted(ok, key=lambda x: x["start"]):
            p *= 1 + float(r["total_return"])
        return p ** (1 / len(ok)) - 1 if ok else float("nan")

    lines = ["prod vs v121c (momentum+green M4 off)", ""]
    for lbl in ("prod", "v121c"):
        by = {r["start"][:4]: r for r in rows if r["label"] == lbl}
        parts = [f"{lbl} CAGR5y {cagr(lbl):+.2%}"]
        for y in sorted(YEARS):
            parts.append(f"{y} {float(by[y]['total_return']):+.2%}")
        lines.append(" | ".join(parts))
    lines.append(f"\n用时 {(time.time()-t0)/60:.1f} min")
    text = "\n".join(lines)
    (out / "summary.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(asyncio.run(main()))
