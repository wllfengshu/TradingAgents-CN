"""截面信号表。

把漏斗排出的候选收成 DataFrame：代码、得分、排名、买或观察，以及当天的灯和仓位比例。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def make_candidate(
    code: str,
    sector_code: str,
    dragon_score: float,
    **extra_fields,
) -> Dict:
    return {
        "code": code,
        "sector_code": sector_code,
        "dragon_score": dragon_score,
        **extra_fields,
    }


def dedupe_candidates_by_code(candidates: List[Dict]) -> List[Dict]:
    """同一代码跨板块重复时保留龙头分更高的一条。"""
    best: Dict[str, Dict] = {}
    order: List[str] = []
    for c in candidates:
        code = c.get("code")
        if not code:
            continue
        score = float(c.get("dragon_score", 0.0) or 0.0)
        prev = best.get(code)
        if prev is None:
            best[code] = c
            order.append(code)
            continue
        prev_score = float(prev.get("dragon_score", 0.0) or 0.0)
        if score > prev_score:
            best[code] = c
    return [best[code] for code in order]


def ranked_to_signals_df(
    ranked: List[Dict[str, Any]],
    trade_date: str,
    market_grade: str,
    position_scale: float,
    *,
    top_k: int,
    regime: str = "neutral",
    allow_new_open: bool = True,
) -> pd.DataFrame:
    td = trade_date
    grade = market_grade
    scale = float(position_scale)
    top_k_final = int(top_k)
    now_iso = datetime.now().astimezone().isoformat()
    rows = [
        {
            "trade_date": td,
            "code": sig.get("code"),
            "sector_code": sig.get("sector_code"),
            "final_score": float(sig.get("final_score", 0.0) or 0.0),
            "dragon_score": float(sig.get("dragon_score", 0.0) or 0.0),
            "force_composite_score": float(sig.get("force_composite_score", 0.0) or 0.0),
            "f35_bollinger_trend": float(sig.get("f35_bollinger_trend", float("nan"))),
            "rank": i,
            "signal_type": (
                "buy" if (allow_new_open and i <= top_k_final) else "watch"
            ),
            "market_grade": grade,
            "position_scale": scale,
            "created_at": now_iso,
        }
        for i, sig in enumerate(ranked, start=1)
    ]
    df = pd.DataFrame(rows)
    df.attrs["market_grade"] = grade
    df.attrs["position_scale"] = scale
    df.attrs["regime"] = regime
    df.attrs["top_k"] = int(top_k_final)
    df.attrs["m1_allow_new_open"] = allow_new_open
    logger.info(
        f"✅ {td} 信号 DataFrame: buy={sum(1 for r in rows if r['signal_type']=='buy')} "
        f"universe={len(df)} (grade={grade}, scale={scale:.2f}, allow_new={allow_new_open})"
    )
    return df


def empty_signals_df(
    trade_date: str,
    market_grade: str = "red",
    position_scale: float = 0.0,
    funnel: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    grade = market_grade
    scale = float(position_scale)
    df = pd.DataFrame(
        columns=[
            "trade_date",
            "code",
            "sector_code",
            "final_score",
            "dragon_score",
            "force_composite_score",
            "f35_bollinger_trend",
            "rank",
            "signal_type",
            "market_grade",
            "position_scale",
            "created_at",
        ]
    )
    df.attrs["market_grade"] = grade
    df.attrs["position_scale"] = scale
    df.attrs["top_k"] = 0
    extra = funnel or {}
    df.attrs["funnel_n_m3"] = int(extra.get("n_m3", 0))
    df.attrs["funnel_n_m4"] = int(extra.get("n_m4", 0))
    df.attrs["funnel_n_buy"] = int(extra.get("n_buy", 0))
    df.attrs["funnel_stop"] = str(extra.get("stop", "empty"))
    if extra.get("regime") is not None:
        df.attrs["regime"] = extra.get("regime")
    return df


def result_to_signals_df(
    result: Dict[str, Any],
    *,
    exit_universe_n: int,
    top_k: int,
    allow_new_open: bool,
) -> pd.DataFrame:
    """漏斗结果 → 策略层 signals DataFrame。"""
    td = str(result.get("trade_date") or "")
    grade = str(result.get("market_risk_level", "unknown"))
    regime = str(result.get("regime", "neutral"))
    scale = float(result.get("position_scale_factor", 0.0))
    funnel = dict(result.get("funnel") or {})
    if result.get("early_exit"):
        return empty_signals_df(
            td, market_grade=grade, position_scale=scale, funnel=funnel
        )
    ranked_all = result.get("ranked_all") or []
    if not ranked_all:
        return empty_signals_df(
            td, market_grade=grade, position_scale=scale, funnel=funnel
        )
    signals = ranked_all[: int(exit_universe_n)]
    df = ranked_to_signals_df(
        signals,
        td,
        grade,
        scale,
        top_k=top_k,
        regime=regime,
        allow_new_open=allow_new_open,
    )
    n_buy = (
        int((df["signal_type"] == "buy").sum())
        if not df.empty and "signal_type" in df.columns
        else 0
    )
    df.attrs["funnel_n_m3"] = int(funnel.get("n_m3", 0))
    df.attrs["funnel_n_m4"] = int(funnel.get("n_m4", 0))
    df.attrs["funnel_n_buy"] = n_buy
    df.attrs["funnel_stop"] = str(funnel.get("stop", "ok"))
    return df
