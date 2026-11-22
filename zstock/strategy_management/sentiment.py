"""市场情绪：红 / 黄 / 绿。

灯只看沪深300收盘相对 120 日均线的距离（trend_gap）：
高于带宽才亮绿灯，低于负带宽才亮红灯。
绿灯要等收盘回到均线才灭，红灯同样，避免贴着带宽来回跳。
中间是黄灯。
detect_style 仍是动量 / 反转 / 中性，供截面权重选配置档，不参与灯色。

带宽与仓位缩放经 get_market_factors_config(params)。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

import numpy as np
import pandas as pd

from zstock.common.config.strategy_config import (
    get_market_factors_config,
    get_neutral_style,
    get_style_detector_config,
)

logger = logging.getLogger(__name__)


def _band(params: Optional[Dict[str, Any]]) -> tuple[float, float]:
    mc = get_market_factors_config(params)
    band = float(mc["trend_band"])
    if band <= 0:
        band = 0.03
    return band, float(mc["trend_release"])


def _instant_grade(gap: float, band: float) -> str:
    if gap >= band:
        return "green"
    if gap <= -band:
        return "red"
    return "yellow"


def _hold_light(previous: str, gap: float, band: float, release: float) -> str:
    """已亮的灯保持到收盘穿过均线；未亮的灯仍要越过带宽才亮。"""
    if previous == "green":
        if gap <= -band:
            return "red"
        if gap > release:
            return "green"
        return "yellow"
    if previous == "red":
        if gap >= band:
            return "green"
        if gap < -release:
            return "red"
        return "yellow"
    return _instant_grade(gap, band)


def _light_result(
    gap: float,
    grade: str,
    detail: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    mc = get_market_factors_config(params)
    band, _release = _band(params)
    scale = {
        "green": mc["scale_green"],
        "yellow": mc["scale_yellow"],
        "red": mc["scale_red"],
    }[grade]
    score = float(np.clip(50.0 + 50.0 * (gap / band), 0.0, 100.0))
    return {
        "market_composite_score": score,
        "market_risk_level": grade,
        "position_scale_factor": scale,
        "allow_new_open": grade != "red",
        "trend_gap": float(gap),
        "detail": detail,
    }


def _neutral_market_result(
    index_name: str,
    reason: str = "",
    *,
    params: Optional[Dict[str, Any]] = None,
    data_missing: bool = False,
) -> Dict[str, Any]:
    logger.warning("[%s] 市场数据缺失 reason=%s，返回中性(yellow)", index_name, reason)
    return {
        "market_composite_score": 50.0,
        "market_risk_level": "yellow",
        "position_scale_factor": get_market_factors_config(params)["scale_yellow"],
        "allow_new_open": True,
        "data_missing": data_missing,
        "trend_gap": 0.0,
        "detail": {"reason": reason},
    }


def score_market_from_gap(
    trend_gap: float,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """trend_gap = 收盘 / MA − 1 → 灯色。"""
    gap = float(trend_gap)
    if gap != gap:
        return _neutral_market_result("沪深300", reason="trend_gap_nan", params=params)
    band, _release = _band(params)
    mc = get_market_factors_config(params)
    return _light_result(
        gap,
        _instant_grade(gap, band),
        {
            "trend_gap": gap,
            "ma_window": int(mc["ma_window"]),
            "trend_band": band,
        },
        params=params,
    )


def score_market_from_closes(
    closes: Sequence[float],
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """收盘序列（最后一根是判定日）→ 灯色。"""
    mc = get_market_factors_config(params)
    window = int(mc["ma_window"])
    series = []
    for value in closes or []:
        try:
            px = float(value)
        except (TypeError, ValueError):
            continue
        if px > 0 and px == px:
            series.append(px)
    if len(series) < window:
        return _neutral_market_result(
            "沪深300", reason="data_insufficient", params=params
        )
    band, release = _band(params)
    grade = "yellow"
    gap = float("nan")
    for end in range(window, len(series) + 1):
        ma = sum(series[end - window : end]) / window
        if ma <= 0:
            continue
        gap = series[end - 1] / ma - 1.0
        grade = _hold_light(grade, gap, band, release)
    if gap != gap:
        return _neutral_market_result("沪深300", reason="trend_gap_nan", params=params)
    mc = get_market_factors_config(params)
    return _light_result(
        gap,
        grade,
        {
            "trend_gap": gap,
            "ma_window": int(mc["ma_window"]),
            "trend_band": band,
            "trend_release": release,
        },
        params=params,
    )


def score_market_from_doc(
    doc: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """预计算 M1 文档 → 情绪结果。"""
    try:
        gap = float(doc.get("trend_gap"))
    except (TypeError, ValueError):
        gap = float("nan")
    if gap != gap:
        return _neutral_market_result(
            "沪深300",
            reason="no_trend_gap",
            params=params,
            data_missing=True,
        )
    return score_market_from_gap(gap, params=params)


def score_market_sentiment(
    index_ohlcv: pd.DataFrame,
    index_name: str = "沪深300",
    trade_date: str = None,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """现场：指数 OHLCV → raw → 情绪打分。"""
    from zstock.factor_management.factors_market import MarketFactors

    raw = MarketFactors.compute_market_raw(
        index_ohlcv, index_name=index_name, trade_date=trade_date
    )
    if "trend_gap" not in raw:
        return _neutral_market_result(
            index_name, reason=str(raw.get("reason") or "empty"), params=params
        )
    frame = index_ohlcv
    closes = []
    if frame is not None and "close" in getattr(frame, "columns", []):
        frame = frame.copy()
        if "trade_date" in frame.columns:
            if trade_date:
                frame = frame.loc[frame["trade_date"].astype(str) <= str(trade_date)]
            frame = frame.sort_values("trade_date")
        parsed = pd.to_numeric(frame["close"], errors="coerce").dropna()
        closes = [float(value) for value in parsed.tolist() if value > 0]
    out = score_market_from_closes(closes, params=params)
    out["detail"] = raw
    return out


def _classify_regime(
    autocorr: float,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, object]:
    if not np.isfinite(autocorr):
        return get_neutral_style(autocorr=float("nan"), strength=0.0)

    cfg = get_style_detector_config(params)
    mom_th = cfg["momentum_threshold"]
    rev_th = cfg["reversal_threshold"]
    strength = min(abs(autocorr) / max(abs(mom_th), 0.01), 1.0)

    if autocorr > mom_th:
        regime = "momentum"
        momentum_weight = min(0.5 + strength * 0.5, 1.0)
        reversal_weight = 1.0 - momentum_weight
    elif autocorr < rev_th:
        regime = "reversal"
        reversal_weight = min(0.5 + strength * 0.5, 1.0)
        momentum_weight = 1.0 - reversal_weight
    else:
        return get_neutral_style(autocorr=float(autocorr), strength=float(strength))

    return {
        "regime": regime,
        "autocorr": float(autocorr),
        "strength": float(strength),
        "momentum_weight": float(momentum_weight),
        "reversal_weight": float(reversal_weight),
    }


def detect_style(
    index_ohlcv: Optional[pd.DataFrame] = None,
    trade_date: Optional[str] = None,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, object]:
    """检测当前市场风格（动量 / 反转 / 中性），用于选 active_factors 档位。"""
    from zstock.factor_management.factors_market import MarketFactors

    if index_ohlcv is None or index_ohlcv.empty:
        logger.warning("detect_style: 无指数数据")
        return _classify_regime(float("nan"), params=params)

    df = index_ohlcv.copy()
    if "close" not in df.columns:
        logger.warning("detect_style: 缺少 close 列")
        return _classify_regime(float("nan"), params=params)

    if trade_date and "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str)
        df = df.loc[df["trade_date"] <= trade_date]

    autocorr = MarketFactors.rank_autocorr(df["close"].astype(float))
    return _classify_regime(autocorr, params=params)
