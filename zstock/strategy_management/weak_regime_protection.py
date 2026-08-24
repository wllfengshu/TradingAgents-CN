"""
弱段保护：reversal+yellow 只减不加、组合回撤节流。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import pandas as pd

logger = __import__("logging").getLogger(__name__)


def should_reduce_only(cfg: Optional[Dict[str, Any]], regime: str, market_grade: str) -> bool:
    """是否启用只减不加（禁止新开仓）。"""
    root = cfg or {}
    if not root.get("enabled", False):
        return False
    ro = root.get("reduce_only") or {}
    if not ro.get("enabled", False):
        return False
    regimes = {str(x).lower() for x in (ro.get("when_regime") or ["reversal"])}
    grades = {str(x).lower() for x in (ro.get("when_market_grade") or ["yellow"])}
    return str(regime).lower() in regimes and str(market_grade).lower() in grades


def apply_reduce_only_filter(
    target_holdings: pd.DataFrame,
    current_positions: Optional[pd.DataFrame],
    force_exit_codes: Optional[Set[str]] = None,
) -> pd.DataFrame:
    """
    只减不加：无持仓时不建仓；有持仓时仅保留当前持仓 ∩ 目标持仓（强制退出除外）。
    """
    force_exit_codes = force_exit_codes or set()
    if current_positions is None or current_positions.empty:
        return pd.DataFrame(columns=["code", "weight", "score"])

    cur_codes = set(current_positions["code"].astype(str))
    if target_holdings is None or target_holdings.empty:
        # 无新目标：保留非强制退出的旧仓（仅减仓由下游 buffer 处理）
        keep = current_positions[~current_positions["code"].isin(force_exit_codes)].copy()
        return keep if not keep.empty else pd.DataFrame(columns=["code", "weight", "score"])

    df = target_holdings.copy()
    df["code"] = df["code"].astype(str)
    filtered = df[df["code"].isin(cur_codes) & ~df["code"].isin(force_exit_codes)].copy()
    return filtered.reset_index(drop=True)


def compute_drawdown_scale(
    equity_series: List[float],
    cfg: Optional[Dict[str, Any]],
) -> float:
    """根据近期峰值回撤计算仓位乘数。"""
    root = cfg or {}
    if not root.get("enabled", False):
        return 1.0
    dt = root.get("drawdown_throttle") or {}
    if not dt.get("enabled", False):
        return 1.0
    if not equity_series:
        return 1.0

    lookback = int(dt.get("lookback_days", 20))
    threshold = float(dt.get("drawdown_threshold", 0.10))
    scale = float(dt.get("scale_factor", 0.7))
    window = equity_series[-lookback:] if lookback > 0 else equity_series
    peak = max(window)
    current = equity_series[-1]
    if peak <= 0:
        return 1.0
    dd = current / peak - 1.0
    if dd <= -threshold:
        return max(0.0, min(1.0, scale))
    return 1.0
