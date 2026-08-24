"""
分 regime / 市场灯号 的自适应再平衡频率。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def resolve_rebalance_freq(
    cfg: Optional[Dict[str, Any]],
    *,
    regime: str = "neutral",
    market_grade: str = "green",
    default_freq: int = 5,
) -> int:
    """根据 regime 与 M1 灯号决定下一次再平衡间隔（交易日）。"""
    if not cfg or not cfg.get("enabled"):
        return max(1, int(default_freq))

    base = int(cfg.get("default_freq", default_freq))
    by_regime = cfg.get("by_regime") or {}
    by_grade = cfg.get("by_market_grade") or {}

    r_freq = int(by_regime.get(regime, by_regime.get("neutral", base)))
    g_freq = int(by_grade.get(market_grade, by_grade.get("green", base)))

    if cfg.get("use_min_freq", True):
        freq = min(r_freq, g_freq)
    else:
        freq = max(r_freq, g_freq)

    lo = int(cfg.get("min_freq", 2))
    hi = int(cfg.get("max_freq", 10))
    return max(lo, min(hi, freq))
