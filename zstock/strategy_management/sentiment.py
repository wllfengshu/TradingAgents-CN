"""市场情绪：红 / 黄 / 绿。

只消费 market 因子 raw（或指数 OHLCV 现场算 raw），输出：
- market_risk_level: red | yellow | green
- position_scale_factor / allow_new_open
- detect_style：动量 / 反转 / 中性（供截面权重选配置档）

阈值一律经 get_*_config(params)；传入 pipeline.config 时与漏斗同源。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from zstock.common.config.strategy_config import (
    get_market_factors_config,
    get_neutral_style,
    get_style_detector_config,
)
from zstock.common.utils.score_maps import (
    map_atr_ratio_to_score,
    map_momentum_to_score,
    map_slope_to_score,
    map_vol_ratio_to_score,
)

logger = logging.getLogger(__name__)


def _compose_sentiment(
    mf1: float,
    mf2: float,
    mf3: float,
    mf4: float,
    mf5: float,
    detail: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    mc = get_market_factors_config(params)
    score = float(
        np.clip(
            mc["w_trend"] * mf1
            + mc["w_bollinger"] * mf2
            + mc["w_volume"] * mf3
            + mc["w_momentum"] * mf4
            + mc["w_volatility"] * mf5,
            0.0,
            100.0,
        )
    )
    if score >= mc["grade_green"]:
        grade, scale = "green", mc["scale_green"]
    elif score >= mc["grade_yellow"]:
        grade, scale = "yellow", mc["scale_yellow"]
    else:
        grade, scale = "red", mc["scale_red"]
    return {
        "market_composite_score": score,
        "market_risk_level": grade,
        "position_scale_factor": scale,
        "allow_new_open": grade != "red",
        "market_trend_strength": mf1,
        "market_bollinger_position": mf2,
        "market_volume_state": mf3,
        "market_5day_momentum": mf4,
        "market_volatility_suppression": mf5,
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
        "market_trend_strength": 50.0,
        "market_bollinger_position": 50.0,
        "market_volume_state": 50.0,
        "market_5day_momentum": 50.0,
        "market_volatility_suppression": 50.0,
        "detail": {"reason": reason},
    }


def score_market_from_raw(
    mf1_slope_pct: float,
    mf2_boll_pct: float,
    mf3_vol_ratio: float,
    mf4_momentum_5d: float,
    mf5_atr_ratio: float,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """五个子因子 raw → 情绪分 / 红绿灯 / 仓位缩放。"""
    mf1 = map_slope_to_score(mf1_slope_pct) if not np.isnan(mf1_slope_pct) else 50.0
    mf2 = mf2_boll_pct * 100.0 if not np.isnan(mf2_boll_pct) else 50.0
    mf3 = map_vol_ratio_to_score(mf3_vol_ratio) if not np.isnan(mf3_vol_ratio) else 50.0
    mf4 = (
        map_momentum_to_score(mf4_momentum_5d)
        if not np.isnan(mf4_momentum_5d)
        else 50.0
    )
    mf5 = map_atr_ratio_to_score(mf5_atr_ratio) if not np.isnan(mf5_atr_ratio) else 50.0
    atr_inv = (
        (1.0 / mf5_atr_ratio)
        if (
            mf5_atr_ratio is not None
            and not np.isnan(mf5_atr_ratio)
            and mf5_atr_ratio > 0
        )
        else float("nan")
    )
    return _compose_sentiment(
        mf1,
        mf2,
        mf3,
        mf4,
        mf5,
        {
            "mf1_slope_pct": mf1_slope_pct,
            "mf2_boll_pct": mf2_boll_pct,
            "mf3_vol_ratio": mf3_vol_ratio,
            "mf4_momentum_5d": mf4_momentum_5d,
            "mf5_atr_ratio": mf5_atr_ratio,
            "mf5_atr_ratio_inv": atr_inv,
        },
        params=params,
    )


def score_market_from_doc(
    doc: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """预计算 M1 文档 → 情绪结果。"""
    nan = float("nan")
    return score_market_from_raw(
        mf1_slope_pct=float(doc.get("mf1_slope_pct", nan)),
        mf2_boll_pct=float(doc.get("mf2_boll_pct", nan)),
        mf3_vol_ratio=float(doc.get("mf3_vol_ratio", nan)),
        mf4_momentum_5d=float(doc.get("mf4_momentum_5d", nan)),
        mf5_atr_ratio=float(doc.get("mf5_atr_ratio", nan)),
        params=params,
    )


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
    if "mf1_slope_pct" not in raw:
        return _neutral_market_result(
            index_name, reason=str(raw.get("reason") or "empty"), params=params
        )
    out = score_market_from_raw(
        mf1_slope_pct=float(raw["mf1_slope_pct"]),
        mf2_boll_pct=float(raw["mf2_boll_pct"]),
        mf3_vol_ratio=float(raw["mf3_vol_ratio"]),
        mf4_momentum_5d=float(raw["mf4_momentum_5d"]),
        mf5_atr_ratio=float(raw["mf5_atr_ratio"]),
        params=params,
    )
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
