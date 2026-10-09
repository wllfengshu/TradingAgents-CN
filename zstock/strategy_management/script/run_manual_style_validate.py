"""prod vs v122 手工对齐 × 2020/2022/2024/2025 + 七年 v122。"""

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
BATCH: List[Tuple[str, Path]] = [
    ("prod", CONFIG / "strategy_params.json"),
    ("v122", CONFIG / "strategy_params_v122_manual_3stock.json"),
]
KEY = {"2020", "2022", "2024", "2025"}


def _cagr(rows: List[Dict[str, Any]]) -> float:
    ok = [r for r in rows if r.get("total_return") is not None]
    if not ok:
        return float("nan")
    p = 1.0
    for r in sorted(ok, key=lambda x: x["start"]):
        p *= 1.0 + float(r["total_return"])
    return p ** (1.0 / len(ok)) - 1.0


async def main() -> int:
    out = Path(__file__).resolve().parent / "output" / f"manual_style_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    t0 = time.time()
    rows: List[Dict[str, Any]] = []
    end = await _latest_trade_date_in_factors()
    jobs = [(y, s, e) for y, s, e in _year_ranges(end) if str(y) in KEY]
    for label, path in BATCH:
        for y, s, e in jobs:
            seg = out / f"{label}_{y}"
            seg.mkdir(parents=True, exist_ok=True)
            rows.append(await _run_one(label, s, e, path, seg))

    lines = ["prod vs v122 手工风格验证", f"MDD 约束目标: -20%", ""]
    for label in ("prod", "v122"):
        sub = [r for r in rows if r["label"] == label]
        by = {r["start"][:4]: r for r in sub}
        worst = min((float(r["max_drawdown"]) for r in sub if r.get("max_drawdown") is not None), default=0)
        parts = [f"{label} 4yCAGR {_cagr(sub):+.2%} worstMDD {worst:+.2%}"]
        for y in sorted(KEY):
            parts.append(f"{y} {float(by[y]['total_return']):+.2%}")
        lines.append(" | ".join(parts))
    lines.append(f"\n用时 {(time.time()-t0)/60:.1f} min")
    text = "\n".join(lines)
    (out / "summary.txt").write_text(text, encoding="utf-8")
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
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
