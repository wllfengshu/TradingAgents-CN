"""
2024 Q2 专项调参：yellow 降仓 / reversal top_k / regime 条件 f33 衰减。

选型规则（仅用 2024）：
  - 约束：2024 全年年化 >= 10%
  - 目标：最大化 2024 Q2 累计收益

用法：
    python -m zstock.strategy_management.script.q2_tune_2024
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
FULL_START, FULL_END = "2024-01-01", "2024-12-31"
Q2_START, Q2_END = "2024-04-01", "2024-06-30"
MIN_ANN = 0.10


def _load_base() -> Dict[str, Any]:
    with open(_PARAMS, "r", encoding="utf-8") as f:
        return json.load(f)


def _variants(base: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = [("baseline", copy.deepcopy(base))]

    for yscale in (0.5, 0.55):
        c = copy.deepcopy(base)
        c.setdefault("market_overlay", {})["position_scale_yellow"] = yscale
        out.append((f"yellow_{yscale}", c))

    c = copy.deepcopy(base)
    c.setdefault("market_overlay", {})["by_regime"] = {
        "reversal": {"position_scale_yellow": 0.5}
    }
    out.append(("yellow_rev_0.5", c))

    for tk in (3, 4):
        c = copy.deepcopy(base)
        c.setdefault("final_score", {})["by_regime"] = {
            "reversal": {"top_k": tk}
        }
        out.append((f"topk_rev_{tk}", c))

    c = copy.deepcopy(base)
    c["factor_decay"] = {
        **base.get("factor_decay", {}),
        "enabled": True,
        "by_regime": {
            "reversal": {"field_overrides": {"f33_consecutive_boards": 0.35}}
        },
    }
    out.append(("decay_f33_rev", c))

    c = copy.deepcopy(base)
    c.setdefault("market_overlay", {})["by_regime"] = {
        "reversal": {"position_scale_yellow": 0.5}
    }
    c.setdefault("final_score", {})["by_regime"] = {"reversal": {"top_k": 3}}
    out.append(("combo_a", c))

    c = copy.deepcopy(base)
    c.setdefault("market_overlay", {})["position_scale_yellow"] = 0.5
    c.setdefault("final_score", {})["by_regime"] = {"reversal": {"top_k": 3}}
    c["factor_decay"] = {
        **base.get("factor_decay", {}),
        "enabled": True,
        "by_regime": {
            "reversal": {"field_overrides": {"f33_consecutive_boards": 0.35}}
        },
    }
    out.append(("combo_b", c))

    c = copy.deepcopy(base)
    c.setdefault("market_overlay", {})["by_regime"] = {
        "reversal": {"position_scale_yellow": 0.5}
    }
    c.setdefault("final_score", {})["by_regime"] = {"reversal": {"top_k": 3}}
    c["factor_decay"] = {
        **base.get("factor_decay", {}),
        "enabled": True,
        "by_regime": {
            "reversal": {"field_overrides": {"f33_consecutive_boards": 0.35}}
        },
    }
    out.append(("combo_c", c))

    return out


def _fp_override(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        k: cfg.get(k)
        for k in (
            "active_factors",
            "sector_layer",
            "dragon_layer",
            "cooperative_force",
            "final_score",
            "factor_decay",
            "market_overlay",
        )
        if cfg.get(k) is not None
    }


async def _run_bt(cfg: Dict[str, Any], ohlcv: dict, start: str, end: str) -> Dict[str, float]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester, make_ohlcv_provider_from_dict

    StrategyPipeline._config_cache = None
    fp = CrossSectionStrategyPipeline(config_override=_fp_override(cfg))
    bt = Backtester(fee_rate=0.0015, factor_pipeline=fp)
    r = await bt.run(
        start_date=start,
        end_date=end,
        ohlcv_provider=make_ohlcv_provider_from_dict(ohlcv),
        strategy_config=cfg,
        rebalance_freq=int(cfg["backtest"]["rebalance_freq"]),
        use_precomputed_factors=True,
        verbose=False,
    )
    m = r.metrics
    return {
        "total_return": float(m.get("total_return", 0)),
        "annualized_return": float(m.get("annualized_return", 0)),
        "sharpe": float(m.get("sharpe", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
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
    out_dir = Path(__file__).resolve().parent / "output" / "q2_tune_2024"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("加载 OHLCV 2024 全年")
    ohlcv_full = await load_ohlcv(FULL_START, FULL_END)
    ohlcv_q2 = await load_ohlcv(Q2_START, Q2_END)

    rows: List[Dict[str, Any]] = []
    try:
        for label, cfg in _variants(base):
            logger.info("回测 %s", label)
            full_m = await _run_bt(cfg, ohlcv_full, FULL_START, FULL_END)
            q2_m = await _run_bt(cfg, ohlcv_q2, Q2_START, Q2_END)
            rows.append({
                "variant": label,
                "full_ann": full_m["annualized_return"],
                "full_ret": full_m["total_return"],
                "full_sharpe": full_m["sharpe"],
                "full_mdd": full_m["max_drawdown"],
                "q2_ret": q2_m["total_return"],
                "q2_sharpe": q2_m["sharpe"],
                "q2_mdd": q2_m["max_drawdown"],
                "passes_min_ann": full_m["annualized_return"] >= MIN_ANN,
            })

        df = pd.DataFrame(rows)
        eligible = df[df["passes_min_ann"]].sort_values("q2_ret", ascending=False)
        df.to_csv(out_dir / "q2_tune_2024.csv", index=False, encoding="utf-8-sig")

        lines = [
            "",
            "=" * 96,
            "2024 Q2 专项调参（约束：全年年化 >= 10%）",
            "=" * 96,
            f"{'variant':<16} {'full_ann':>8} {'q2_ret':>8} {'full_mdd':>8} {'q2_mdd':>8} {'ok':>4}",
        ]
        for _, r in df.sort_values("q2_ret", ascending=False).iterrows():
            lines.append(
                f"{r['variant']:<16} {r['full_ann']*100:>7.2f}% {r['q2_ret']*100:>7.2f}% "
                f"{r['full_mdd']*100:>7.2f}% {r['q2_mdd']*100:>7.2f}% "
                f"{'Y' if r['passes_min_ann'] else 'N':>4}"
            )
        if not eligible.empty:
            best = eligible.iloc[0]
            lines += [
                "",
                f"-> 推荐: {best['variant']}  Q2={best['q2_ret']*100:.2f}%  全年年化={best['full_ann']*100:.2f}%",
            ]
        else:
            lines += ["", "-> 无方案满足全年年化 >= 10% 约束"]
        lines.append("=" * 96)
        report = "\n".join(lines)
        (out_dir / "q2_tune_2024_report.txt").write_text(report, encoding="utf-8")
        print(report)
        return 0
    finally:
        await close_zstock_database()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
