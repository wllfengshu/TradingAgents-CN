"""prod vs v123_exposure vs v123d 折中 × 2020–2026 全历史。"""

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
BATCH: List[Tuple[str, Path]] = [
    ("prod", CONFIG / "strategy_params.json"),
    ("v123_exposure", CONFIG / "strategy_params_v123_exposure.json"),
    ("v123d", CONFIG / "strategy_params_v123d_compromise.json"),
]
KEY = ("2020", "2024", "2025")


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


def _parse_args():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--resume", type=Path, default=None)
    return p.parse_args()


async def main() -> int:
    args = _parse_args()
    if args.resume is not None:
        out = args.resume.resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(__file__).resolve().parent / "output" / f"v123d_validate_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    t0 = time.time()
    end = await _latest_trade_date_in_factors()
    all_rows: List[Dict[str, Any]] = []

    for label, path in BATCH:
        if not path.is_file():
            print("missing", path)
            continue
        cfg_rows: List[Dict[str, Any]] = []
        for year, start, end_y in _year_ranges(end):
            seg = out / f"{label}_{year}"
            seg.mkdir(parents=True, exist_ok=True)
            summary_path = seg / "summary.json"
            if summary_path.is_file():
                cfg_rows.append(json.loads(summary_path.read_text(encoding="utf-8")))
                print(f"skip {label} {year}")
                continue
            print(f"{label} {year}")
            cfg_rows.append(await _run_one(label, start, end_y, path, seg))
        all_rows.extend(cfg_rows)
        label_dir = out / label
        label_dir.mkdir(parents=True, exist_ok=True)
        _write_report(label_dir, cfg_rows, end)

    lines = ["v123d 折中验证 vs prod / v123_exposure", f"截止 {end}", ""]
    for label, _ in BATCH:
        sub = [r for r in all_rows if r["label"] == label]
        cagr, worst = _cagr(sub)
        by = {r["start"][:4]: r for r in sub}
        parts = [f"{label} 7yCAGR {cagr:+.2%} worstMDD {worst:+.2%}"]
        for y in KEY:
            parts.append(f"{y} {float(by[y]['total_return']):+.2%}")
        lines.append(" | ".join(parts))
    lines.append(f"\n用时 {(time.time()-t0)/60:.1f} min")
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
