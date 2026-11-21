"""截面持仓的强制卖出。

排名掉出分位，或趋势走坏（一段时间没创新高，并且布林趋势变弱），返回必须卖掉的股票代码。
行业 ETF 和低波策略不用这套规则。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import normalize_date


def rank_force_exit_codes(
    current_positions: Optional[pd.DataFrame],
    signals_df: pd.DataFrame,
    exit_rules: Dict[str, Any],
) -> set:
    """持仓仍在候选宇宙但排名分位劣于阈值 → 强制卖出。"""
    if (
        current_positions is None
        or current_positions.empty
        or signals_df is None
        or signals_df.empty
    ):
        return set()
    if "rank" not in signals_df.columns or "code" not in signals_df.columns:
        return set()
    thr = float(exit_rules.get("rank_percentile_threshold", 0.85))
    n = len(signals_df)
    rank_map = (
        signals_df.drop_duplicates(subset="code", keep="first")
        .set_index("code")["rank"]
        .to_dict()
    )
    force = set()
    for code in current_positions["code"].astype(str):
        r = rank_map.get(code)
        # 已不在观察池/信号表：视为漏斗淘汰，强制退出（不再静默持有）
        if r is None:
            force.add(code)
            continue
        # n<=1 无法定义有意义的分位，仅保留「缺席强制出」
        if n <= 1:
            continue
        pct = float(r) / float(n)
        if pct > thr + 1e-12:
            force.add(code)
    return force


def f35_map_from_signals(
    signals_df: Optional[pd.DataFrame],
    field: str = "f35_bollinger_trend",
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if signals_df is None or signals_df.empty or field not in signals_df.columns:
        return out
    for _, row in signals_df.drop_duplicates("code").iterrows():
        try:
            out[str(row["code"])] = float(row.get(field, float("nan")))
        except (TypeError, ValueError):
            continue
    return out


def f35_map_from_ohlcv(
    ohlcv_by_code: Dict[str, pd.DataFrame],
    trade_date: str,
    codes: Set[str],
) -> Dict[str, float]:
    """无漏斗缓存时，用截至 trade_date 的日线现场算 f35（与 DragonFactors 同源）。"""
    if not codes or not ohlcv_by_code:
        return {}
    from zstock.factor_management.factors_dragon import DragonFactors

    td = normalize_date(trade_date)
    sliced: Dict[str, pd.DataFrame] = {}
    code_list = [str(c) for c in codes]
    for code in code_list:
        df = ohlcv_by_code.get(code)
        if df is None or df.empty:
            continue
        frame = df
        if "trade_date" in frame.columns:
            frame = frame.copy()
            frame["trade_date"] = frame["trade_date"].astype(str).map(normalize_date)
            frame = frame[frame["trade_date"] <= td]
        if frame is None or frame.empty:
            continue
        sliced[code] = frame
    if not sliced:
        return {}
    trend, _passed = DragonFactors.f35_bollinger_trend(sliced, list(sliced.keys()))
    out: Dict[str, float] = {}
    for code, val in (trend or {}).items():
        try:
            fv = float(val)
        except (TypeError, ValueError):
            continue
        if np.isfinite(fv):
            out[str(code)] = fv
    return out


def trend_cfg(exit_rules: Dict[str, Any]) -> Dict[str, Any]:
    return (exit_rules or {}).get("trend_exit") or {}


def trend_exit_enabled(
    exit_rules: Dict[str, Any],
    *,
    regime: Optional[str] = None,
) -> bool:
    te = trend_cfg(exit_rules)
    if not te.get("enabled"):
        return False
    when = te.get("when_regimes")
    if when and regime is not None:
        allowed = {str(x) for x in when}
        if str(regime) not in allowed:
            return False
    return True


def no_new_high(
    ohlcv: pd.DataFrame,
    trade_date: str,
    lookback: int,
) -> bool:
    if ohlcv is None or ohlcv.empty or lookback < 2:
        return False
    df = ohlcv.copy()
    if "trade_date" in df.columns:
        df = df.sort_values("trade_date")
        df = df[df["trade_date"].astype(str) <= str(trade_date)]
    else:
        df = df.sort_index()
    if len(df) < lookback:
        return False
    tail = df.tail(lookback)
    highs = (
        tail["high"].astype(float)
        if "high" in tail.columns
        else tail["close"].astype(float)
    )
    closes = tail["close"].astype(float)
    today_close = float(closes.iloc[-1])
    prior_peak = (
        float(highs.iloc[:-1].max()) if len(highs) > 1 else float(highs.iloc[0])
    )
    return today_close < prior_peak - 1e-9


def bollinger_weak(
    f35: float,
    *,
    below: float,
    prev_f35: Optional[float],
    require_declining: bool,
) -> bool:
    if not np.isfinite(f35):
        return False
    if f35 < below:
        return True
    if require_declining and prev_f35 is not None and np.isfinite(prev_f35):
        return f35 < prev_f35 - 1e-9
    return False


def should_trend_exit(
    *,
    ohlcv: Optional[pd.DataFrame],
    trade_date: str,
    f35: float,
    prev_f35: Optional[float],
    trend_cfg: Dict[str, Any],
) -> bool:
    if not trend_cfg.get("enabled"):
        return False
    lookback = int(trend_cfg.get("no_new_high_lookback", 20))
    below = float(trend_cfg.get("bollinger_trend_below", 40.0))
    require_both = bool(trend_cfg.get("require_both", True))
    require_declining = bool(trend_cfg.get("bollinger_require_declining", False))

    nnh = no_new_high(ohlcv, trade_date, lookback) if ohlcv is not None else False
    boll = bollinger_weak(
        f35,
        below=below,
        prev_f35=prev_f35,
        require_declining=require_declining,
    )
    if require_both:
        return nnh and boll
    return nnh or boll


def trend_force_exit_codes(
    held_codes: Set[str],
    trade_date: str,
    exit_rules: Dict[str, Any],
    *,
    ohlcv_by_code: Dict[str, pd.DataFrame],
    f35_by_code: Dict[str, float],
    prev_f35_by_code: Optional[Dict[str, float]] = None,
    regime: Optional[str] = None,
) -> Set[str]:
    if not trend_exit_enabled(exit_rules, regime=regime) or not held_codes:
        return set()
    cfg = trend_cfg(exit_rules)
    out: Set[str] = set()
    prev_f35_by_code = prev_f35_by_code or {}
    f35_by_code = dict(f35_by_code or {})
    missing: Set[str] = set()
    for c in held_codes:
        try:
            fv = float(f35_by_code.get(str(c), float("nan")))
        except (TypeError, ValueError):
            fv = float("nan")
        if not np.isfinite(fv):
            missing.add(str(c))
    if missing and ohlcv_by_code:
        f35_by_code.update(f35_map_from_ohlcv(ohlcv_by_code, trade_date, missing))
    for code in held_codes:
        f35 = float(f35_by_code.get(code, float("nan")))
        prev = prev_f35_by_code.get(code)
        if should_trend_exit(
            ohlcv=ohlcv_by_code.get(code),
            trade_date=trade_date,
            f35=f35,
            prev_f35=prev,
            trend_cfg=cfg,
        ):
            out.add(str(code))
    return out
