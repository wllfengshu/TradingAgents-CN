"""
v124 推广前检查：2021 专项 + 7y 摘要（优先读 overnight_round2 已有 CSV）。

    python -m zstock.strategy_management.script.run_v124_promotion_check
    python -m zstock.strategy_management.script.run_v124_promotion_check --round2-dir output/overnight_round2_*
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SCRIPT_OUT = Path(__file__).resolve().parent / "output"
CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"

LABELS_2021 = ("prod", "v123f", "v123_naked")
CONFIG_MAP = {
    "prod": CONFIG / "strategy_params.json",
    "v123f": CONFIG / "strategy_params_v123f_momentum_full_te.json",
    "v123_naked": CONFIG / "strategy_params_v123_naked_select.json",
    "v124": CONFIG / "strategy_params_v124_candidate.json",
}


def _find_latest_round2() -> Optional[Path]:
    dirs = sorted(SCRIPT_OUT.glob("overnight_round2_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0] if dirs else None


def _load_summary(round2: Path, label: str, year: str) -> Optional[Dict[str, Any]]:
    p = round2 / f"{label}_{year}" / "summary.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _holdings_csv(round2: Path, label: str, year: str) -> Optional[Path]:
    seg = round2 / f"{label}_{year}"
    if not seg.is_dir():
        return None
    hits = sorted(seg.glob("backtest_holdings_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def _curve_csv(round2: Path, label: str, year: str) -> Optional[Path]:
    seg = round2 / f"{label}_{year}"
    hits = sorted(seg.glob("backtest_curve_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def _avg_gross_exposure(holdings_path: Path) -> Tuple[float, int]:
    import pandas as pd

    df = pd.read_csv(holdings_path)
    if df.empty or "trade_date" not in df.columns or "weight" not in df.columns:
        return float("nan"), 0
    daily = df.groupby("trade_date")["weight"].sum()
    return float(daily.mean()), int(len(daily))


def _worst_drawdown_from_curve(curve_path: Path) -> float:
    import pandas as pd

    df = pd.read_csv(curve_path)
    if "drawdown" in df.columns and not df.empty:
        return float(df["drawdown"].min())
    return float("nan")


def _cagr7(round2: Path, label: str) -> Tuple[float, float]:
    years = ("2020", "2021", "2022", "2023", "2024", "2025", "2026")
    p = 1.0
    worst = 0.0
    n = 0
    for y in years:
        row = _load_summary(round2, label, y)
        if not row or row.get("total_return") is None:
            continue
        p *= 1.0 + float(row["total_return"])
        worst = min(worst, float(row.get("max_drawdown") or 0))
        n += 1
    cagr = p ** (1.0 / n) - 1.0 if n else float("nan")
    return cagr, worst


async def _maybe_run_2021(round2: Path) -> None:
    """缺分段时补跑 2021。"""
    import asyncio

    from zstock.strategy_management.script.run_history_2020_2026 import (
        _latest_trade_date_in_factors,
        _run_one,
        _setup_logging,
    )
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    need = [
        lb
        for lb in LABELS_2021
        if _load_summary(round2, lb, "2021") is None and CONFIG_MAP.get(lb, Path()).is_file()
    ]
    if not need:
        return
    _setup_logging(round2 / "promotion_check_rerun.log")
    await init_zstock_database()
    for lb in need:
        seg = round2 / f"{lb}_2021"
        seg.mkdir(parents=True, exist_ok=True)
        await _run_one(lb, "2021-01-01", "2021-12-31", CONFIG_MAP[lb], seg)
    await close_zstock_database()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="v124 推广前检查")
    p.add_argument("--round2-dir", type=Path, default=None)
    p.add_argument("--rerun-missing-2021", action="store_true")
    return p.parse_args()


async def async_main() -> int:
    args = _parse_args()
    round2 = args.round2_dir.resolve() if args.round2_dir else _find_latest_round2()
    if round2 is None or not round2.is_dir():
        print("未找到 overnight_round2_* 目录")
        return 1

    if args.rerun_missing_2021:
        await _maybe_run_2021(round2)

    lines = [
        "v124 推广前检查",
        f"生成: {datetime.now():%Y-%m-%d %H:%M}",
        f"round2 数据: {round2}",
        f"候选配置: {CONFIG_MAP['v124']}",
        "",
        "── 7y（v124 ≡ v123f）──",
    ]
    for lb in ("prod", "v123f", "v124"):
        label = "v123f" if lb == "v124" else lb
        cagr, worst = _cagr7(round2, label)
        tag = " (=v123f)" if lb == "v124" else ""
        lines.append(f"  {lb}{tag}: CAGR {cagr:+.2%}  worstMDD {worst:+.2%}")

    lines.extend(["", "── 2021 专项（naked 事故对照）──"])
    lines.append(f"{'配置':<12} {'收益':>8} {'MDD':>8} {'均仓位':>8} {'曲线MDD':>8}")
    for lb in LABELS_2021:
        row = _load_summary(round2, lb, "2021")
        h = _holdings_csv(round2, lb, "2021")
        c = _curve_csv(round2, lb, "2021")
        ret = float(row["total_return"]) if row else float("nan")
        mdd = float(row.get("max_drawdown") or 0) if row else float("nan")
        avg_w, n_days = _avg_gross_exposure(h) if h else (float("nan"), 0)
        curve_mdd = _worst_drawdown_from_curve(c) if c else float("nan")
        lines.append(
            f"  {lb:<10} {ret:>+7.2%} {mdd:>+7.2%} {avg_w:>7.1%} {curve_mdd:>+7.2%}  (holdings日={n_days})"
        )

    lines.extend(["", "── 晋级建议（规则）──"])
    c_p, m_p = _cagr7(round2, "prod")
    c_f, m_f = _cagr7(round2, "v123f")
    row_n = _load_summary(round2, "v123_naked", "2021")
    row_f = _load_summary(round2, "v123f", "2021")
    if c_f > c_p + 0.003 and m_f >= -0.20:
        lines.append(f"✓ 7y: v123f/v124 CAGR {c_f:+.2%} > prod {c_p:+.2%}，MDD {m_f:+.2%} 在预算内。")
    else:
        lines.append("✗ 7y 未达相对 prod +0.3% 或 MDD 超预算。")
    if row_f and row_n:
        if float(row_f.get("max_drawdown") or 0) > float(row_n.get("max_drawdown") or 0) + 0.05:
            lines.append("✓ 2021: v123f MDD 明显浅于 naked，无 naked 式弱段事故。")
        else:
            lines.append("? 2021: 请人工看 v123f 与 naked 持仓/曲线。")
    lines.extend(
        [
            "",
            "下一步: shadow 小仓对账 → 合并 target_equity 进 prod 或整包替换 v1.24.0",
            "回滚: strategy_params_v1201_prod_backup.json（推广前自行备份）",
        ]
    )

    out_dir = SCRIPT_OUT / f"v124_promotion_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    (out_dir / "promotion_check.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> None:
    import asyncio

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
