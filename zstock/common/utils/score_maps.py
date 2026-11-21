"""通用连续值 → 0~100 分映射（与具体因子/策略层无关）。"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

# float64 下 np.exp 安全域约 ±709
_EXP_CLIP = 709.0

# 市场斜率 / 动量 sigmoid 陡峭度（raw 量级很小，需放大才有区分度）
DEFAULT_SIGMOID_K_SLOPE = 800.0
DEFAULT_SIGMOID_K_MOMENTUM = 30.0
DEFAULT_ATR_RATIO_SEGMENTS: tuple[float, ...] = (0.01, 0.03, 0.06)


def sigmoid_score(x: float, *, k: float) -> float:
    """sigmoid：x↑ → 分↑，输出夹在 [0, 100]。"""
    exp_arg = np.clip(-k * x, -_EXP_CLIP, _EXP_CLIP)
    return float(np.clip(100.0 / (1.0 + np.exp(exp_arg)), 0.0, 100.0))


def map_slope_to_score(
    slope_pct: float, k: Optional[float] = None
) -> float:
    return sigmoid_score(
        slope_pct, k=DEFAULT_SIGMOID_K_SLOPE if k is None else float(k)
    )


def map_momentum_to_score(
    momentum: float, k: Optional[float] = None
) -> float:
    return sigmoid_score(
        momentum, k=DEFAULT_SIGMOID_K_MOMENTUM if k is None else float(k)
    )


def map_vol_ratio_to_score(ratio: float) -> float:
    """成交量相对均值：过低/过高都扣分，中等偏高最优。"""
    if ratio < 0.5:
        score = 10.0
    elif ratio <= 1.0:
        score = 10.0 + 80.0 * (ratio - 0.5) / 0.5
    elif ratio <= 1.5:
        score = 90.0 + 10.0 * (ratio - 1.0) / 0.5
    elif ratio <= 3.0:
        score = 100.0 - 80.0 * (ratio - 1.5) / 1.5
    else:
        decay = min((ratio - 3.0) / 2.0, 1.0)
        score = 20.0 - 10.0 * decay
    return float(np.clip(score, 0.0, 100.0))


def map_atr_ratio_to_score(
    atr_ratio: float,
    segments: Optional[Sequence[float]] = None,
) -> float:
    """波动率比：低波动高分，分段线性衰减。"""
    segs = tuple(segments) if segments is not None else DEFAULT_ATR_RATIO_SEGMENTS
    a, b, c = float(segs[0]), float(segs[1]), float(segs[2])
    if atr_ratio <= a:
        score = 90.0
    elif atr_ratio <= b:
        score = 90.0 - 40.0 * (atr_ratio - a) / (b - a)
    elif atr_ratio <= c:
        score = 50.0 - 40.0 * (atr_ratio - b) / (c - b)
    else:
        score = 10.0
    return float(np.clip(score, 0.0, 100.0))
