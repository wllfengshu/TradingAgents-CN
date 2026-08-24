"""
v1.14 A/B：factor_decay × adaptive_rebalance 四组合对比。

用法：
    python -m zstock.strategy_management.script.ab_v14_compare
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

_PARAMS = PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"

SEGMENTS = [
    ("2024_full", "2024-01-01", "2024-12-31"),
    ("2024_Q2", "2024-04-01", "2024-06-30"),
    ("2026_Q2", "2026-04-01", "2026-06-30"),
]

VARIANTS = [
    ("baseline", False, False),
    ("decay_only", True, False),
    ("adaptive_only", False, True),
    ("both_v14", True, True),
]


def _load_base() -> Dict[str, Any]:
    with open(_PARAMS, "r", encoding="utf-8") as f:
        return json.load(f)


def _make_configs(base: Dict[str, Any], decay: bool, adaptive: bool) -> Tuple[Dict, Dict]:
    cfg = copy.deepcopy(base)
    cfg.setdefault("factor_decay", {})["enabled"] = decay
    cfg.setdefault("adaptive_rebalance", {})["enabled"] = adaptive
    fp_override = {
        "factor_decay": cfg["factor_decay"],
    }
    return cfg, fp_override


async def _run_one(
    label: str,
    seg: str,
    start: str,
    end: str,
    cfg: Dict[str, Any],
    fp_override: Dict[str, Any],
    ohlcv: dict,
) -> Dict[str, Any]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester, make_ohlcv_provider_from_dict

    StrategyPipeline._config_cache = None
    fp = CrossSectionStrategyPipeline(config_override=fp_override)
    bt = Backtester(fee_rate=0.0015, factor_pipeline=fp)
    provider = make_ohlcv_provider_from_dict(ohlcv)
    reb = int(cfg.get("backtest", {}).get("rebalance_freq", 5))
    result = await bt.run(
        start_date=start,
        end_date=end,
        ohlcv_provider=provider,
        strategy_config=cfg,
        rebalance_freq=reb,
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
        "variant": label,
        "segment": seg,
        "total_return": float(m.get("total_return", 0)),
        "total_return_gross": float(m.get("total_return_gross", 0)),
        "sharpe": float(m.get("sharpe", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
        "total_cost": float(m.get("total_cost", 0)),
        "rebalance_count": int(m.get("rebalance_count", 0)),
        "avg_exposure": exp,
    }


async def async_main() -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.script.网格搜索.grid_search_real import load_ohlcv

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger(__name__).setLevel(logging.INFO)

    try:
        await init_zstock_database()
    except Exception:
        from app.core.database import db_manager
        await db_manager.init_mongodb()

    base = _load_base()
    out_dir = Path(__file__).resolve().parent / "output" / "ab_v14"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    try:
        for seg_name, start, end in SEGMENTS:
            logger.info("加载 OHLCV %s %s~%s", seg_name, start, end)
            ohlcv = await load_ohlcv(start, end)
            for vname, decay, adaptive in VARIANTS:
                cfg, fp_ov = _make_configs(base, decay, adaptive)
                logger.info("  回测 %s / %s", seg_name, vname)
                rows.append(await _run_one(vname, seg_name, start, end, cfg, fp_ov, ohlcv))

        df = pd.DataFrame(rows)
        df.to_csv(out_dir / "ab_v14_compare.csv", index=False, encoding="utf-8-sig")

        lines = ["", "=" * 88, "v1.14 A/B: factor_decay x adaptive_rebalance", "=" * 88, ""]
        for seg in df["segment"].unique():
            sub = df[df["segment"] == seg].sort_values("total_return", ascending=False)
            lines.append(f"── {seg} ──")
            lines.append(f"{'variant':<16} {'return':>8} {'gross':>8} {'sharpe':>7} {'mdd':>8} {'reb':>4} {'exp':>6}")
            for _, r in sub.iterrows():
                lines.append(
                    f"{r['variant']:<16} {r['total_return']*100:>7.2f}% "
                    f"{r['total_return_gross']*100:>7.2f}% {r['sharpe']:>7.3f} "
                    f"{r['max_drawdown']*100:>7.2f}% {int(r['rebalance_count']):>4} "
                    f"{r['avg_exposure']*100:>5.1f}%"
                )
            best = sub.iloc[0]
            lines.append(f"  -> best: {best['variant']} ({best['total_return']*100:.2f}%)")
            lines.append("")

        # 推荐组合
        full = df[df["segment"] == "2024_full"].sort_values("total_return", ascending=False)
        q2 = df[df["segment"] == "2024_Q2"].sort_values("total_return", ascending=False)
        lines += ["── 推荐 ──"]
        lines.append(f"  2024全年最优: {full.iloc[0]['variant']} ({full.iloc[0]['total_return']*100:.2f}%)")
        lines.append(f"  2024 Q2最优:  {q2.iloc[0]['variant']} ({q2.iloc[0]['total_return']*100:.2f}%)")
        lines.append("=" * 88)

        report = "\n".join(lines)
        (out_dir / "ab_v14_report.txt").write_text(report, encoding="utf-8")
        print(report)
        return 0
    finally:
        await close_zstock_database()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
