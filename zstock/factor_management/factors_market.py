"""
市场因子模块（M1）

职责：基于指数 OHLCV 计算 mf* 原始特征（本模块不查库、不打分）。
情绪合成 / 红绿灯 / 风格分类见 strategy_management.sentiment。
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

# 特征工程与算子数学常数（非策略交易参数）
_MA_WINDOW = 20
_BOLL_STD = 2.0
_SLOPE_WINDOW = 5
_MOMENTUM_WINDOW = 5
_ATR_WINDOW = 20
_VOL_MA_WINDOW = 20

_TREND_MA_WINDOWS = (5, 10, 20)
_BOLL_MA_WINDOWS = (10, 20, 30)
_VOL_MA_WINDOWS = (5, 10, 20)
_MOMENTUM_WINDOWS = (3, 5, 10)
_ATR_WINDOWS = (10, 20, 30)
_MA_WINDOWS_FOR_ATR = (10, 20, 30)
_MIN_BARS = 31
# 风格特征：收益率排名自相关窗口（分类阈值在 strategy 层）
_STYLE_LOOKBACK = 20
_STYLE_MIN_OBS = 15


class MarketFactors:
    """M1 市场因子：单因子算子（mf1~mf5）+ 批量 raw；不含打分。"""

    @staticmethod
    def min_bars() -> int:
        return _MIN_BARS

    @staticmethod
    def compute_market_raw(
        index_ohlcv: pd.DataFrame,
        index_name: str = "沪深300",
        trade_date: str = None,
    ) -> Dict[str, Any]:
        """指数 OHLCV → mf* 原始值字典（含多窗口）；数据不足时仅含 reason。"""
        if index_ohlcv is None or len(index_ohlcv) < _MIN_BARS:
            logger.warning(
                f"⚠️ [{index_name}] 数据不足（{len(index_ohlcv) if index_ohlcv is not None else 0} 根 "
                f"< {_MIN_BARS}）"
            )
            return {"reason": "data_insufficient"}

        df = ensure_ohlcv_sorted(index_ohlcv.copy()).reset_index(drop=True)
        if trade_date:
            asof = ohlcv_asof(df, trade_date, require_exact=True)
            if asof is None or len(asof) < _MIN_BARS:
                logger.warning(
                    f"⚠️ [{index_name}] trade_date={trade_date} 无当日指数 bar 或数据不足"
                )
                return {"reason": "asof_mismatch_or_insufficient"}
            df = asof.reset_index(drop=True)
        for col in ("close", "high", "low", "volume"):
            if col not in df.columns:
                logger.warning(f"⚠️ [{index_name}] 缺少列 '{col}'")
                return {"reason": f"missing_col_{col}"}
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["close", "high", "low", "volume"])
        if len(df) < _MIN_BARS:
            return {"reason": "data_insufficient_after_clean"}

        mf1_raw = MarketFactors.mf1_slope_pct(df)
        mf2_raw = MarketFactors.mf2_boll_pct(df)
        mf3_raw = MarketFactors.mf3_vol_ratio(df)
        mf4_raw = MarketFactors.mf4_momentum(df)
        mf5_raw = MarketFactors.mf5_atr_ratio(df)

        atr_inv = (
            (1.0 / mf5_raw)
            if (mf5_raw is not None and mf5_raw == mf5_raw and mf5_raw > 0)
            else float("nan")
        )
        detail: Dict[str, Any] = {
            "mf1_slope_pct": mf1_raw,
            "mf2_boll_pct": mf2_raw,
            "mf3_vol_ratio": mf3_raw,
            "mf4_momentum_5d": mf4_raw,
            "mf5_atr_ratio": mf5_raw,
            "mf5_atr_ratio_inv": atr_inv,
        }

        for w in _TREND_MA_WINDOWS:
            if w != _MA_WINDOW:
                detail[f"mf1_slope_pct_{w}d"] = MarketFactors.mf1_slope_pct(df, ma_window=w)

        for w in _BOLL_MA_WINDOWS:
            if w != _MA_WINDOW:
                detail[f"mf2_boll_pct_{w}d"] = MarketFactors.mf2_boll_pct(df, ma_window=w)

        for w in _VOL_MA_WINDOWS:
            if w != _VOL_MA_WINDOW:
                detail[f"mf3_vol_ratio_{w}d"] = MarketFactors.mf3_vol_ratio(
                    df, vol_ma_window=w
                )

        for w in _MOMENTUM_WINDOWS:
            detail[f"mf4_momentum_{w}d"] = MarketFactors.mf4_momentum(df, window=w)

        for atr_w, ma_w in zip(_ATR_WINDOWS, _MA_WINDOWS_FOR_ATR):
            if atr_w != _ATR_WINDOW or ma_w != _MA_WINDOW:
                detail[f"mf5_atr_ratio_{atr_w}d_{ma_w}d"] = MarketFactors.mf5_atr_ratio(
                    df, atr_window=atr_w, ma_window=ma_w
                )

        return detail

    @staticmethod
    @factor(
        FactorSpec(
            field="mf1_slope_pct",
            name_en="ma_slope_pct",
            name_cn="MA趋势强度",
            definition="均线斜率 / 最新均线",
            inputs=("index_ohlcv", "ma_window"),
            outputs=("float",),
            layer="market",
            default_polarity="positive",
            regimes=("momentum", "reversal"),
            catalog=False,
        )
    )
    def mf1_slope_pct(df: pd.DataFrame, ma_window: int = None) -> float:
        """MF1：MA 百分比斜率（原始值）。"""
        if ma_window is None:
            ma_window = _MA_WINDOW

        close = df["close"].values
        ma = pd.Series(close).rolling(ma_window, min_periods=ma_window).mean().values
        valid_ma = ma[~np.isnan(ma)][-_SLOPE_WINDOW:]
        if len(valid_ma) < 2:
            return float("nan")

        x = np.arange(len(valid_ma), dtype=float)
        slope = float(np.polyfit(x, valid_ma, 1)[0])
        if valid_ma[-1] <= 0 or np.isnan(valid_ma[-1]):
            return float("nan")
        return slope / valid_ma[-1]

    @staticmethod
    @factor(
        FactorSpec(
            field="mf2_boll_pct",
            name_en="boll_position",
            name_cn="布林带位置",
            definition="收盘在布林带中的相对位置 0~1",
            inputs=("index_ohlcv", "ma_window"),
            outputs=("float",),
            layer="market",
            default_polarity="positive",
            regimes=("momentum", "reversal"),
            catalog=False,
        )
    )
    def mf2_boll_pct(df: pd.DataFrame, ma_window: int = None) -> float:
        """MF2：布林带位置原始值（0~1）。"""
        if ma_window is None:
            ma_window = _MA_WINDOW

        close = df["close"].values
        ma = pd.Series(close).rolling(ma_window, min_periods=ma_window).mean()
        std = pd.Series(close).rolling(ma_window, min_periods=ma_window).std()
        upper = ma + _BOLL_STD * std
        lower = ma - _BOLL_STD * std

        last_close = float(close[-1])
        last_upper = float(upper.iloc[-1])
        last_lower = float(lower.iloc[-1])

        if np.isnan(last_upper) or np.isnan(last_lower):
            return float("nan")

        band_width = last_upper - last_lower
        if band_width <= 0:
            return float("nan")

        boll_pct = (last_close - last_lower) / band_width
        return float(np.clip(boll_pct, 0.0, 1.0))

    @staticmethod
    @factor(
        FactorSpec(
            field="mf3_vol_ratio",
            name_en="volume_ratio",
            name_cn="量能状态",
            definition="成交量 / 均量",
            inputs=("index_ohlcv", "vol_ma_window"),
            outputs=("float",),
            layer="market",
            default_polarity="positive",
            regimes=("momentum", "reversal"),
            catalog=False,
        )
    )
    def mf3_vol_ratio(df: pd.DataFrame, vol_ma_window: int = None) -> float:
        """MF3：量比原始值。"""
        if vol_ma_window is None:
            vol_ma_window = _VOL_MA_WINDOW

        vol = df["volume"].values
        vol_ma = pd.Series(vol).rolling(vol_ma_window, min_periods=vol_ma_window).mean().values
        last_vol = float(vol[-1])
        last_vol_ma = float(vol_ma[-1]) if not np.isnan(vol_ma[-1]) else 0.0

        if last_vol_ma <= 0 or np.isnan(last_vol):
            return float("nan")
        return last_vol / last_vol_ma

    @staticmethod
    @factor(
        FactorSpec(
            field="mf4_momentum",
            name_en="momentum",
            name_cn="近期动量",
            definition="N 日涨跌幅",
            inputs=("index_ohlcv", "window"),
            outputs=("float",),
            layer="market",
            default_polarity="positive",
            regimes=("momentum", "reversal"),
            catalog=False,
        )
    )
    def mf4_momentum(df: pd.DataFrame, window: int = None) -> float:
        """MF4：N 日涨跌幅原始值。"""
        w = _MOMENTUM_WINDOW if window is None else window
        close = df["close"].values
        if len(close) < w + 1:
            return float("nan")

        last_close = float(close[-1])
        if last_close <= 0 or np.isnan(last_close):
            return float("nan")

        ref_close = float(close[-w - 1])
        if ref_close <= 0 or np.isnan(ref_close):
            return float("nan")

        return (last_close - ref_close) / ref_close

    @staticmethod
    @factor(
        FactorSpec(
            field="mf5_atr_ratio",
            name_en="atr_ratio",
            name_cn="波动率压制",
            definition="ATR / MA",
            inputs=("index_ohlcv", "atr_window", "ma_window"),
            outputs=("float",),
            layer="market",
            default_polarity="positive",
            regimes=("momentum", "reversal"),
            catalog=False,
        )
    )
    def mf5_atr_ratio(
        df: pd.DataFrame, atr_window: int = None, ma_window: int = None
    ) -> float:
        """MF5：ATR/MA 原始值。"""
        if atr_window is None:
            atr_window = _ATR_WINDOW
        if ma_window is None:
            ma_window = _MA_WINDOW

        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum(
            high - low,
            np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
        )

        atr_series = pd.Series(tr).rolling(atr_window, min_periods=atr_window).mean()
        ma_series = pd.Series(close).rolling(ma_window, min_periods=ma_window).mean()

        atr = float(atr_series.iloc[-1])
        ma = float(ma_series.iloc[-1])

        if np.isnan(atr) or np.isnan(ma) or ma <= 0 or atr < 0:
            return float("nan")
        return atr / ma

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
