"""
市场因子模块（M1）

职责：基于沪深300指数评估当日宏观环境，包含两个维度：
  1. 情绪强弱（红黄绿灯 + 仓位缩放）
  2. 风格运行（动量 / 反转 / 中性）

黑盒门面：MarketFactors（指数行情由调用方传入，本模块不查库）
- calculate_market_sentiment(index_ohlcv)  现场：OHLCV → 情绪/红绿灯
- score_from_raw(...)                      回测：预计算原始值 → 情绪/红绿灯
- detect_style(index_ohlcv)                风格：OHLCV → momentum/reversal/neutral

策略权重、红绿灯阈值、仓位缩放、风格阈值一律从
strategy_config.get_market_factors_config / get_style_detector_config 读取。
本模块只保留特征算子的数学常数与离线预计算窗口。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from scipy import stats

from zstock.common.utils.common_utils import ensure_ohlcv_sorted, ohlcv_asof
from zstock.common.config.strategy_config import (
    get_market_factors_config,
    get_neutral_style,
    get_style_detector_config,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# 特征工程与算子数学常数（非策略交易参数）
# 策略权重 / 红绿灯 / 仓位缩放 / 风格阈值 → strategy_config.py
# ==============================================================================

# 默认打分窗口
_MA_WINDOW = 20
_BOLL_STD = 2.0
_SLOPE_WINDOW = 5
_MOMENTUM_WINDOW = 5
_ATR_WINDOW = 20
_VOL_MA_WINDOW = 20

# Sigmoid / ATR 折线映射（特征公式内生）
_SIGMOID_K_SLOPE = 800.0
_SIGMOID_K_MOMENTUM = 30.0
_ATR_RATIO_SEGMENTS = [0.01, 0.03, 0.06]
_ATR_RATIO_SCORES = [90.0, 50.0, 10.0, 10.0]

# 离线预计算多窗口（落库供网格搜索订阅）
_TREND_MA_WINDOWS = (5, 10, 20)
_BOLL_MA_WINDOWS = (10, 20, 30)
_VOL_MA_WINDOWS = (5, 10, 20)
_MOMENTUM_WINDOWS = (3, 5, 10)
_ATR_WINDOWS = (10, 20, 30)
_MA_WINDOWS_FOR_ATR = (10, 20, 30)
_MIN_BARS = 31


class MarketFactors:
    """M1 黑盒门面：市场情绪评估 + 动量/反转风格检测。"""

    # ===================== 公开接口 =====================

    @staticmethod
    def calculate_market_sentiment(
        index_ohlcv: pd.DataFrame,
        index_name: str = '沪深300',
        trade_date: str = None,
    ) -> Dict:
        """
        【公开接口】根据指数 OHLCV 计算市场情绪得分与风险等级。

        Args:
            index_ohlcv: 指数日线 DataFrame，必须含列：
                         close, high, low, volume（按日期升序）
            index_name:  指数名称，仅用于日志标识
            trade_date: 可选，指定交易日（YYYY-MM-DD），若不指定则使用最新日期

        Returns:
            {
                'market_composite_score': float,        # 市场综合得分 0~100
                'market_risk_level':      str,          # 'green'(强势) / 'yellow'(震荡) / 'red'(弱势)
                'position_scale_factor':  float,        # 仓位缩放因子 0.0 / 0.5 / 1.0
                'allow_new_open':         bool,         # 是否允许开新仓
                'market_trend_strength':      float,    # 市场趋势强度得分 0~100
                'market_bollinger_position':  float,    # 市场布林位置得分 0~100
                'market_volume_state':       float,     # 市场成交量状态得分 0~100
                'market_5day_momentum':      float,     # 市场5日动量得分 0~100
                'market_volatility_suppression': float, # 市场波动率抑制得分 0~100
                'detail':                 dict,         # 各因子原始值，便于调试
            }

        若数据不足（< _MIN_BARS 根），返回 grade='yellow'，score=50（中性），
        避免因数据缺失而误判关闭开仓。
        """
        if index_ohlcv is None or len(index_ohlcv) < _MIN_BARS:
            logger.warning(
                f"⚠️ [{index_name}] 数据不足（{len(index_ohlcv) if index_ohlcv is not None else 0} 根 "
                f"< {_MIN_BARS}），返回中性评估"
            )
            return MarketFactors._neutral_result(index_name, reason='data_insufficient')

        df = ensure_ohlcv_sorted(index_ohlcv.copy()).reset_index(drop=True)
        if trade_date:
            asof = ohlcv_asof(df, trade_date, require_exact=True)
            if asof is None or len(asof) < _MIN_BARS:
                logger.warning(
                    f"⚠️ [{index_name}] trade_date={trade_date} 无当日指数 bar 或数据不足，"
                    f"返回中性评估"
                )
                return MarketFactors._neutral_result(
                    index_name, reason="asof_mismatch_or_insufficient"
                )
            df = asof.reset_index(drop=True)
        for col in ('close', 'high', 'low', 'volume'):
            if col not in df.columns:
                logger.warning(f"⚠️ [{index_name}] 缺少列 '{col}'，返回中性评估")
                return MarketFactors._neutral_result(index_name, reason=f'missing_col_{col}')
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.dropna(subset=['close', 'high', 'low', 'volume'])
        if len(df) < _MIN_BARS:
            return MarketFactors._neutral_result(index_name, reason='data_insufficient_after_clean')

        mf1, mf1_raw = MarketFactors._score_trend_strength(df)
        mf2, mf2_raw = MarketFactors._score_boll_position(df)
        mf3, mf3_raw = MarketFactors._score_volume_state(df)
        mf4, mf4_raw = MarketFactors._score_momentum(df)
        mf5, mf5_raw = MarketFactors._score_volatility(df)

        atr_inv = (
            (1.0 / mf5_raw)
            if (mf5_raw is not None and mf5_raw == mf5_raw and mf5_raw > 0)
            else float("nan")
        )
        detail = {
            "mf1_slope_pct": mf1_raw,
            "mf2_boll_pct": mf2_raw,
            "mf3_vol_ratio": mf3_raw,
            "mf4_momentum_5d": mf4_raw,
            "mf5_atr_ratio": mf5_raw,
            "mf5_atr_ratio_inv": atr_inv,
        }

        # 多窗口原始值（网格搜索用）
        # MF1 趋势强度多窗口
        for w in _TREND_MA_WINDOWS:
            if w != _MA_WINDOW:
                _, raw_w = MarketFactors._score_trend_strength(df, ma_window=w)
                detail[f"mf1_slope_pct_{w}d"] = raw_w

        # MF2 布林位置多窗口
        for w in _BOLL_MA_WINDOWS:
            if w != _MA_WINDOW:
                _, raw_w = MarketFactors._score_boll_position(df, ma_window=w)
                detail[f"mf2_boll_pct_{w}d"] = raw_w

        # MF3 成交量多窗口
        for w in _VOL_MA_WINDOWS:
            if w != _VOL_MA_WINDOW:
                _, raw_w = MarketFactors._score_volume_state(df, vol_ma_window=w)
                detail[f"mf3_vol_ratio_{w}d"] = raw_w

        # MF4 动量多窗口（所有窗口，包括默认）
        for w in _MOMENTUM_WINDOWS:
            _, raw_w = MarketFactors._score_momentum(df, window=w)
            detail[f"mf4_momentum_{w}d"] = raw_w

        # MF5 波动率多窗口（ATR 与 MA 同窗口配对，后续可扩展为交叉组合）
        for atr_w, ma_w in zip(_ATR_WINDOWS, _MA_WINDOWS_FOR_ATR):
            if atr_w != _ATR_WINDOW or ma_w != _MA_WINDOW:
                _, raw_w = MarketFactors._score_volatility(df, atr_window=atr_w, ma_window=ma_w)
                detail[f"mf5_atr_ratio_{atr_w}d_{ma_w}d"] = raw_w

        return MarketFactors._compose_sentiment(mf1, mf2, mf3, mf4, mf5, detail)

    @staticmethod
    def score_from_raw(
        mf1_slope_pct: float,
        mf2_boll_pct: float,
        mf3_vol_ratio: float,
        mf4_momentum_5d: float,
        mf5_atr_ratio: float,
    ) -> Dict:
        """
        【公开接口】从 M1 5个子因子原始值直接打分。

        用于 pipeline.score_signals 从预计算原始值重建市场情绪结果。
        映射逻辑与 calculate_market_sentiment() 完全一致（Single Source of Truth）。

        Args:
            mf1_slope_pct:   20日MA 5日百分比斜率，范围 (-inf, +inf)，通常 [-0.01, 0.01]
                             负值=下降趋势，正值=上升趋势
            mf2_boll_pct:    布林带位置百分比，范围 [0.0, 1.0]
                             0.0=下轨(超卖)，0.5=中轨，1.0=上轨(超买)
            mf3_vol_ratio:   当日成交量 / 20日均量，范围 [0, +inf)，通常 [0.2, 3.0]
                             <1=缩量，=1=平量，>1=放量
            mf4_momentum_5d: 5日涨跌幅百分比，范围 (-inf, +inf)，通常 [-0.05, 0.05]
                             负值=下跌，正值=上涨
            mf5_atr_ratio:   ATR(20) / MA(20)，范围 [0, +inf)，通常 [0.005, 0.1]
                             低=波动平稳，高=波动剧烈

        Returns:
            与 calculate_market_sentiment() 返回结构一致的 dict
        """
        mf1 = MarketFactors._map_slope_to_score(mf1_slope_pct) if not np.isnan(mf1_slope_pct) else 50.0
        mf2 = mf2_boll_pct * 100.0 if not np.isnan(mf2_boll_pct) else 50.0
        mf3 = MarketFactors._map_vol_ratio_to_score(mf3_vol_ratio) if not np.isnan(mf3_vol_ratio) else 50.0
        mf4 = MarketFactors._map_momentum_to_score(mf4_momentum_5d) if not np.isnan(mf4_momentum_5d) else 50.0
        mf5 = MarketFactors._map_atr_ratio_to_score(mf5_atr_ratio) if not np.isnan(mf5_atr_ratio) else 50.0
        atr_inv = (
            (1.0 / mf5_atr_ratio)
            if (mf5_atr_ratio is not None and not np.isnan(mf5_atr_ratio) and mf5_atr_ratio > 0)
            else float("nan")
        )
        return MarketFactors._compose_sentiment(
            mf1, mf2, mf3, mf4, mf5,
            {
                "mf1_slope_pct": mf1_slope_pct,
                "mf2_boll_pct": mf2_boll_pct,
                "mf3_vol_ratio": mf3_vol_ratio,
                "mf4_momentum_5d": mf4_momentum_5d,
                "mf5_atr_ratio": mf5_atr_ratio,
                "mf5_atr_ratio_inv": atr_inv,
            },
        )

    @staticmethod
    def score_from_doc(doc: Dict[str, Any]) -> Dict:
        """预计算 M1 文档 → 情绪结果（字段名由本类持有）。"""
        nan = float("nan")
        return MarketFactors.score_from_raw(
            mf1_slope_pct=float(doc.get("mf1_slope_pct", nan)),
            mf2_boll_pct=float(doc.get("mf2_boll_pct", nan)),
            mf3_vol_ratio=float(doc.get("mf3_vol_ratio", nan)),
            mf4_momentum_5d=float(doc.get("mf4_momentum_5d", nan)),
            mf5_atr_ratio=float(doc.get("mf5_atr_ratio", nan)),
        )

    # ===================== 私有方法（实现细节，对外隐藏）=====================

    @staticmethod
    def _compose_sentiment(
        mf1: float, mf2: float, mf3: float, mf4: float, mf5: float, detail: Dict[str, Any],
    ) -> Dict[str, Any]:
        """加权合成情绪分并映射红黄绿灯（现场计算与预计算打分共用）。"""
        mc = get_market_factors_config()
        score = float(np.clip(
            mc["w_trend"] * mf1
            + mc["w_bollinger"] * mf2
            + mc["w_volume"] * mf3
            + mc["w_momentum"] * mf4
            + mc["w_volatility"] * mf5,
            0.0, 100.0,
        ))
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

    @staticmethod
    def _map_slope_to_score(slope_pct: float, k: float = None) -> float:
        """sigmoid 映射：百分比斜率 → 0~100 得分"""
        if k is None:
            k = _SIGMOID_K_SLOPE
        exp_arg = np.clip(-k * slope_pct, -709, 709)
        return float(np.clip(100.0 / (1.0 + np.exp(exp_arg)), 0.0, 100.0))

    @staticmethod
    def _map_vol_ratio_to_score(ratio: float) -> float:
        """折线映射：量比 → 0~100 得分"""
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

    @staticmethod
    def _map_momentum_to_score(momentum: float, k: float = None) -> float:
        """sigmoid 映射：5日涨跌幅 → 0~100 得分"""
        if k is None:
            k = _SIGMOID_K_MOMENTUM
        exp_arg = np.clip(-k * momentum, -709, 709)
        return float(np.clip(100.0 / (1.0 + np.exp(exp_arg)), 0.0, 100.0))

    @staticmethod
    def _map_atr_ratio_to_score(atr_ratio: float) -> float:
        """折线映射：ATR/MA 比率 → 0~100 得分（低波动=高分）"""
        if atr_ratio <= _ATR_RATIO_SEGMENTS[0]:      # 0.01
            score = 90.0
        elif atr_ratio <= _ATR_RATIO_SEGMENTS[1]:    # 0.03
            score = 90.0 - 40.0 * (atr_ratio - _ATR_RATIO_SEGMENTS[0]) / (_ATR_RATIO_SEGMENTS[1] - _ATR_RATIO_SEGMENTS[0])
        elif atr_ratio <= _ATR_RATIO_SEGMENTS[2]:    # 0.06
            score = 50.0 - 40.0 * (atr_ratio - _ATR_RATIO_SEGMENTS[1]) / (_ATR_RATIO_SEGMENTS[2] - _ATR_RATIO_SEGMENTS[1])
        else:
            score = 10.0
        return float(np.clip(score, 0.0, 100.0))

    @staticmethod
    def _score_trend_strength(df: pd.DataFrame, ma_window: int = None) -> tuple:
        """
        【私有】MF1：MA趋势强度（支持多窗口）。

        计算方式：
          1. 计算 ma_window 日 MA（默认20）
          2. 用最近 _SLOPE_WINDOW 根均线做线性回归，得斜率
          3. 将斜率除以最新均线值，得"日均涨跌幅"（百分比斜率）
          4. sigmoid 映射到 0~100
        """
        if ma_window is None:
            ma_window = _MA_WINDOW

        close = df['close'].values
        ma = pd.Series(close).rolling(ma_window, min_periods=ma_window).mean().values

        # 取最近 _SLOPE_WINDOW 根有效均线
        valid_ma = ma[~np.isnan(ma)][-_SLOPE_WINDOW:]
        if len(valid_ma) < 2:
            return 50.0, float('nan')

        x = np.arange(len(valid_ma), dtype=float)
        slope = float(np.polyfit(x, valid_ma, 1)[0])
        if valid_ma[-1] <= 0 or np.isnan(valid_ma[-1]):
            return 50.0, float('nan')
        slope_pct = slope / valid_ma[-1]   # 相对斜率，消除绝对值影响

        # sigmoid 映射：s = 100 / (1 + exp(-k * slope_pct))
        score = MarketFactors._map_slope_to_score(slope_pct)
        return score, slope_pct

    @staticmethod
    def _score_boll_position(df: pd.DataFrame, ma_window: int = None) -> tuple:
        """
        【私有】MF2：布林带位置（支持多窗口）。

        计算当前收盘价在布林带内的相对位置（0~1），线性映射到 0~100。
        """
        if ma_window is None:
            ma_window = _MA_WINDOW

        close = df['close'].values
        ma    = pd.Series(close).rolling(ma_window, min_periods=ma_window).mean()
        std   = pd.Series(close).rolling(ma_window, min_periods=ma_window).std()
        upper = ma + _BOLL_STD * std
        lower = ma - _BOLL_STD * std

        last_close = float(close[-1])
        last_upper = float(upper.iloc[-1])
        last_lower = float(lower.iloc[-1])

        if np.isnan(last_upper) or np.isnan(last_lower):
            return 50.0, float('nan')

        band_width = last_upper - last_lower

        if band_width <= 0:
            return 50.0, float('nan')

        boll_pct = (last_close - last_lower) / band_width
        boll_pct = float(np.clip(boll_pct, 0.0, 1.0))
        score = boll_pct * 100.0
        return score, boll_pct

    @staticmethod
    def _score_volume_state(df: pd.DataFrame, vol_ma_window: int = None) -> tuple:
        """
        【私有】MF3：量能状态（支持多窗口）。

        计算当日成交量与 vol_ma_window 日均量的比值。
        """
        if vol_ma_window is None:
            vol_ma_window = _VOL_MA_WINDOW

        vol    = df['volume'].values
        vol_ma = pd.Series(vol).rolling(vol_ma_window, min_periods=vol_ma_window).mean().values
        last_vol    = float(vol[-1])
        last_vol_ma = float(vol_ma[-1]) if not np.isnan(vol_ma[-1]) else 0.0

        if last_vol_ma <= 0 or np.isnan(last_vol):
            return 50.0, float('nan')

        ratio = last_vol / last_vol_ma
        score = MarketFactors._map_vol_ratio_to_score(ratio)
        return score, ratio

    @staticmethod
    def _score_momentum(df: pd.DataFrame, window: int = None) -> tuple:
        """
        【私有】MF4：近期动量（N 日涨跌幅，默认 5）。

        sigmoid 映射，参数 k=30：
        - 涨幅 +5%  → 约82分
        - 涨幅  0%  → 50分
        - 跌幅 -5%  → 约18分
        """
        w = _MOMENTUM_WINDOW if window is None else window
        close = df["close"].values
        if len(close) < w + 1:
            return 50.0, float("nan")

        last_close = float(close[-1])
        if last_close <= 0 or np.isnan(last_close):
            return 50.0, float("nan")

        ref_close = float(close[-w - 1])
        if ref_close <= 0 or np.isnan(ref_close):
            return 50.0, float("nan")

        momentum = (last_close - ref_close) / ref_close
        score = MarketFactors._map_momentum_to_score(momentum)
        return score, momentum

    @staticmethod
    def _score_volatility(df: pd.DataFrame, atr_window: int = None, ma_window: int = None) -> tuple:
        """
        【私有】MF5：波动率压制（True Range ATR 比率的倒数，支持多窗口）。

        核心逻辑：
        使用 True Range 而非简单 H-L，能正确捕捉跳空缺口带来的波动：
          TR = max(H-L, |H-prev_close|, |L-prev_close|)
        A股指数频繁出现跳空低开/高开，仅用 H-L 会系统性低估波动率。

        ATR ratio = ATR(atr_window) / MA(ma_window)，衡量相对波动率：
        - 波动率低（市场稳定）→ ATR ratio 小 → 得分高
        - 波动率高（市场恐慌）→ ATR ratio 大 → 得分低

        映射逻辑（折线）：
        atr_ratio <= 0.01  → 90（极低波动，稳定市场）
        0.01~0.03          → 90 → 50（线性衰减，正常波动区间）
        0.03~0.06          → 50 → 10（快速衰减，高波动区间）
        > 0.06             → 10（极端波动）
        """
        if atr_window is None:
            atr_window = _ATR_WINDOW
        if ma_window is None:
            ma_window = _MA_WINDOW

        high  = df['high'].values
        low   = df['low'].values
        close = df['close'].values

        # True Range：考虑跳空缺口
        # TR[i] = max(H[i]-L[i], |H[i]-C[i-1]|, |L[i]-C[i-1]|)
        # 第一根K线无前日收盘，用 H-L 替代
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]  # 第一根用自身收盘替代，TR = H-L

        tr = np.maximum(
            high - low,
            np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
        )

        atr_series = pd.Series(tr).rolling(atr_window, min_periods=atr_window).mean()
        ma_series = pd.Series(close).rolling(ma_window, min_periods=ma_window).mean()

        atr = float(atr_series.iloc[-1])
        ma  = float(ma_series.iloc[-1])

        if np.isnan(atr) or np.isnan(ma) or ma <= 0 or atr < 0:
            return 50.0, float('nan')

        atr_ratio = atr / ma

        score = MarketFactors._map_atr_ratio_to_score(atr_ratio)
        return score, atr_ratio

    @staticmethod
    def _neutral_result(index_name: str, reason: str = '') -> Dict:
        """【私有】数据不足时返回黄色中性结果，允许开仓但缩半仓"""
        logger.warning(f"⚠️ [{index_name}] 市场数据缺失 reason={reason}，返回中性评估")
        return {
            'market_composite_score':   50.0,
            'market_risk_level':   'yellow',
            'position_scale_factor': get_market_factors_config()["scale_yellow"],
            'allow_new_open': True,
            'market_trend_strength':      50.0,
            'market_bollinger_position':       50.0,
            'market_volume_state':     50.0,
            'market_5day_momentum':   50.0,
            'market_volatility_suppression': 50.0,
            'detail': {'reason': reason},
        }

    # ===================== 市场风格检测 =====================

    @staticmethod
    def detect_style(
        index_ohlcv: Optional[pd.DataFrame] = None,
        trade_date: Optional[str] = None,
    ) -> Dict[str, object]:
        """检测当前市场风格（动量 / 反转 / 中性）。"""
        if index_ohlcv is None or index_ohlcv.empty:
            logger.warning("MarketFactors.detect_style: 无指数数据")
            return MarketFactors._classify_regime(float("nan"))

        df = index_ohlcv.copy()
        if "close" not in df.columns:
            logger.warning("MarketFactors.detect_style: 缺少 close 列")
            return MarketFactors._classify_regime(float("nan"))

        if trade_date and "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].astype(str)
            df = df.loc[df["trade_date"] <= trade_date]

        autocorr = MarketFactors._compute_rank_autocorr(df["close"].astype(float))
        return MarketFactors._classify_regime(autocorr)

    @staticmethod
    def _compute_rank_autocorr(close_prices: pd.Series) -> float:
        """滚动收益率排名的 1 阶 Spearman 自相关。"""
        cfg = get_style_detector_config()
        lookback = cfg["lookback"]
        min_obs = cfg["min_observations"]
        if close_prices is None or len(close_prices) < min_obs:
            return float("nan")

        tail = close_prices.iloc[-lookback:]
        if len(tail) < min_obs:
            return float("nan")

        returns = tail.pct_change(fill_method=None).dropna()
        if len(returns) < min_obs:
            return float("nan")

        ranks = returns.rank()
        rp = ranks.iloc[:-1].to_numpy()
        rc = ranks.iloc[1:].to_numpy()
        valid = np.isfinite(rp) & np.isfinite(rc)
        if valid.sum() < 10:
            return float("nan")

        corr, _ = stats.spearmanr(rp[valid], rc[valid])
        return float(corr) if np.isfinite(corr) else float("nan")

    @staticmethod
    def _classify_regime(autocorr: float) -> Dict[str, object]:
        """按自相关阈值分类 momentum / reversal / neutral。"""
        if not np.isfinite(autocorr):
            return get_neutral_style(autocorr=float("nan"), strength=0.0)

        cfg = get_style_detector_config()
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
