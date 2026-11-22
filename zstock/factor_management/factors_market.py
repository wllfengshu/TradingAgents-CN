"""
市场因子模块（M1）

职责：基于指数收盘计算趋势缺口 raw（本模块不查库、不打灯）。
红黄绿见 strategy_management.sentiment。风格自相关仍在本模块。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from scipy import stats

from zstock.common.utils.common_utils import ensure_ohlcv_sorted, ohlcv_asof
from zstock.common.entity.factor_spec import FactorSpec, factor

logger = logging.getLogger(__name__)

# 灯用的均线长度。情绪层的带宽在 strategy_params.market_factors。
_TREND_MA = 120

# 风格特征：收益率排名自相关窗口（分类阈值在 strategy 层）
_STYLE_LOOKBACK = 20
_STYLE_MIN_OBS = 15


class MarketFactors:
    """M1：指数相对长均线的位置。不含打灯。"""

    @staticmethod
    def min_bars() -> int:
        return _TREND_MA

    @staticmethod
    def compute_market_raw(
        index_ohlcv: pd.DataFrame,
        index_name: str = "沪深300",
        trade_date: str = None,
        ma_window: int = None,
    ) -> Dict[str, Any]:
        """指数 OHLCV → trend_gap。数据不足时仅含 reason。"""
        window = _TREND_MA if ma_window is None else int(ma_window)
        if index_ohlcv is None or len(index_ohlcv) < window:
            logger.warning(
                f"⚠️ [{index_name}] 数据不足（{len(index_ohlcv) if index_ohlcv is not None else 0} 根 "
                f"< {window}）"
            )
            return {"reason": "data_insufficient"}

        df = ensure_ohlcv_sorted(index_ohlcv.copy()).reset_index(drop=True)
        if trade_date:
            asof = ohlcv_asof(df, trade_date, require_exact=True)
            if asof is None or len(asof) < window:
                logger.warning(
                    f"⚠️ [{index_name}] trade_date={trade_date} 无当日指数 bar 或数据不足"
                )
                return {"reason": "asof_mismatch_or_insufficient"}
            df = asof.reset_index(drop=True)
        if "close" not in df.columns:
            logger.warning(f"⚠️ [{index_name}] 缺少列 'close'")
            return {"reason": "missing_col_close"}
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"])
        if len(df) < window:
            return {"reason": "data_insufficient_after_clean"}

        gap = MarketFactors.trend_gap(df, ma_window=window)
        if gap != gap:
            return {"reason": "trend_gap_nan"}
        return {"trend_gap": gap, "ma_window": window}

    @staticmethod
    @factor(
        FactorSpec(
            field="trend_gap",
            name_en="trend_gap",
            name_cn="指数相对长均线",
            definition="收盘 / MA120 − 1",
            inputs=("index_ohlcv", "ma_window"),
            outputs=("float",),
            layer="market",
            default_polarity="positive",
            regimes=("momentum", "reversal"),
            catalog=False,
        )
    )
    def trend_gap(df: pd.DataFrame, ma_window: int = None) -> float:
        """收盘相对均线的百分比距离。正值在均线上方。"""
        window = _TREND_MA if ma_window is None else int(ma_window)
        close = pd.to_numeric(df["close"], errors="coerce")
        ma = close.rolling(window, min_periods=window).mean()
        last = float(close.iloc[-1])
        last_ma = float(ma.iloc[-1])
        if not np.isfinite(last) or not np.isfinite(last_ma) or last <= 0 or last_ma <= 0:
            return float("nan")
        return last / last_ma - 1.0

    @staticmethod
    def rank_autocorr(close_prices: pd.Series) -> float:
        """滚动收益率排名的 1 阶 Spearman 自相关（风格特征 raw）。"""
        if close_prices is None or len(close_prices) < _STYLE_MIN_OBS:
            return float("nan")

        tail = close_prices.iloc[-_STYLE_LOOKBACK:]
        if len(tail) < _STYLE_MIN_OBS:
            return float("nan")

        returns = tail.pct_change(fill_method=None).dropna()
        if len(returns) < _STYLE_MIN_OBS:
            return float("nan")

        ranks = returns.rank()
        rp = ranks.iloc[:-1].to_numpy()
        rc = ranks.iloc[1:].to_numpy()
        valid = np.isfinite(rp) & np.isfinite(rc)
        if valid.sum() < 10:
            return float("nan")

        corr, _ = stats.spearmanr(rp[valid], rc[valid])
        return float(corr) if np.isfinite(corr) else float("nan")
