"""
市场风格检测器（Style Detector）

职责：
1. 计算滚动20日排名自相关（Rank Autocorrelation），判断市场是动量/反转/中性
2. 提供极性调整信号供 pipeline 使用
3. 分类：momentum（趋势延续）、reversal（均值回归）、neutral（无方向）

核心算法：
  对每个交易日 t：
  1. 计算过去 20 日收益率排名
  2. 计算排名序列的 1 阶自相关
  3. 根据自相关符号分类：
     - 正自相关 → 动量风格（强者恒强）
     - 负自相关 → 反转风格（涨多必跌）
     - 接近 0 → 中性

使用场景：
  - 当检测到"反转→动量"切换时，应对反转类因子（如 f_mean_reversion_signal）降权
  - 当检测到"动量→反转"切换时，应对动量类因子（如 fcoop1）谨慎

数据来源：
  - 使用沪深300指数（399300）日线 OHLCV 数据，通过 query_service.get_ohlcv() 获取
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from zstock.data_management.query_service import get_data_query_service
from zstock.common.config.strategy_config import load_strategy_params

logger = logging.getLogger(__name__)

# 风格分类阈值（模块内置保底值；运行时以 strategy_params.json → style_detector 为准）
_MOMENTUM_THRESHOLD = 0.05    # 自相关 > 0.05 → 动量
_REVERSAL_THRESHOLD = -0.05   # 自相关 < -0.05 → 反转
_MIN_OBSERVATIONS = 15         # 最少需要 15 个交易日数据
_LOOKBACK = 20                 # 默认回顾窗口


def _style_detector_config() -> Dict:
    """从 strategy_params.json → style_detector 读取风格阈值（缓存）。"""
    if _style_detector_config._cache is not None:
        return _style_detector_config._cache
    cfg = (load_strategy_params() or {}).get("style_detector") or {}
    _style_detector_config._cache = {
        "momentum_threshold": float(cfg.get("momentum_threshold", _MOMENTUM_THRESHOLD)),
        "reversal_threshold": float(cfg.get("reversal_threshold", _REVERSAL_THRESHOLD)),
        "min_observations": int(cfg.get("min_observations", _MIN_OBSERVATIONS)),
        "lookback": int(cfg.get("lookback", _LOOKBACK)),
    }
    return _style_detector_config._cache


_style_detector_config._cache = None


class StyleDetector:
    """市场风格检测器：动量 vs 反转"""

    def __init__(
        self,
        lookback: Optional[int] = None,
        momentum_threshold: Optional[float] = None,
        reversal_threshold: Optional[float] = None,
        min_observations: Optional[int] = None,
    ):
        cfg = _style_detector_config()
        self.lookback = lookback if lookback is not None else cfg["lookback"]
        self.momentum_threshold = (
            momentum_threshold
            if momentum_threshold is not None
            else cfg["momentum_threshold"]
        )
        self.reversal_threshold = (
            reversal_threshold
            if reversal_threshold is not None
            else cfg["reversal_threshold"]
        )
        self.min_observations = (
            min_observations
            if min_observations is not None
            else cfg["min_observations"]
        )

    def compute_rank_autocorr(
        self,
        close_prices: pd.Series,
    ) -> float:
        """
        计算滚动 ranking 自相关（关键指标）

        算法：
        1. 取最近 lookback 个交易日的收盘价
        2. 计算每日收益率，按收益率排名（1 = 最低，n = 最高）
        3. 计算排名序列的 1 阶自相关
        4. 自相关 > 0 → 动量风格（高排名日接着高排名日）
        5. 自相关 < 0 → 反转风格（高排名日接着低排名日）

        Args:
            close_prices: 收盘价序列（升序，索引为日期）

        Returns:
            排名自相关系数 [-1, 1]
        """
        if close_prices is None or len(close_prices) < self.min_observations:
            return float("nan")

        # 取最近 lookback 个交易日
        tail = close_prices.iloc[-self.lookback:]
        if len(tail) < self.min_observations:
            return float("nan")

        # 计算每日收益率
        returns = tail.pct_change(fill_method=None).dropna()
        if len(returns) < self.min_observations:
            return float("nan")

        # 对收益率排名
        ranks = returns.rank()

        # 计算 1 阶自相关（Spearman 秩相关）
        rank_prev = ranks.iloc[:-1]
        rank_curr = ranks.iloc[1:]

        # 转换为 numpy 数组避免 pandas 索引对齐问题
        rp = rank_prev.to_numpy()
        rc = rank_curr.to_numpy()

        # 过滤掉 nan
        valid = np.isfinite(rp) & np.isfinite(rc)
        if valid.sum() < 10:
            return float("nan")

        corr, _ = stats.spearmanr(rp[valid], rc[valid])
        return float(corr) if np.isfinite(corr) else float("nan")

    def classify_regime(self, autocorr: float) -> Dict[str, object]:
        """
        分类市场风格

        Returns:
            {
                "regime": "momentum" | "reversal" | "neutral",
                "autocorr": float,
                "strength": float (0-1, 信号强度),
                "momentum_weight": float (建议动量因子权重),
                "reversal_weight": float (建议反转因子权重),
            }
        """
        if not np.isfinite(autocorr):
            return {
                "regime": "neutral",
                "autocorr": float("nan"),
                "strength": 0.0,
                "momentum_weight": 0.5,
                "reversal_weight": 0.5,
            }

        # 计算信号强度（0-1）
        strength = min(abs(autocorr) / max(abs(self.momentum_threshold), 0.01), 1.0)

        if autocorr > self.momentum_threshold:
            regime = "momentum"
            # 动量风格：动量因子权重高，反转因子权重低
            momentum_weight = min(0.5 + strength * 0.5, 1.0)
            reversal_weight = 1.0 - momentum_weight
        elif autocorr < self.reversal_threshold:
            regime = "reversal"
            # 反转风格：反转因子权重高，动量因子权重低
            reversal_weight = min(0.5 + strength * 0.5, 1.0)
            momentum_weight = 1.0 - reversal_weight
        else:
            regime = "neutral"
            momentum_weight = 0.5
            reversal_weight = 0.5

        return {
            "regime": regime,
            "autocorr": float(autocorr),
            "strength": float(strength),
            "momentum_weight": float(momentum_weight),
            "reversal_weight": float(reversal_weight),
        }

    def detect(
        self,
        index_ohlcv: pd.DataFrame,
        trade_date: Optional[str] = None,
    ) -> Dict[str, object]:
        """
        主入口：检测当前市场风格

        Args:
            index_ohlcv: 指数 OHLCV DataFrame（需有 close 列和 trade_date 列）
            trade_date: 截面日期（可选，用于截断到当日）

        Returns:
            风格检测结果字典
        """
        if index_ohlcv is None or index_ohlcv.empty:
            logger.warning("StyleDetector: 无指数数据")
            return self.classify_regime(float("nan"))

        df = index_ohlcv.copy()
        if "close" not in df.columns:
            logger.warning("StyleDetector: 缺少 close 列")
            return self.classify_regime(float("nan"))

        # 如果指定了 trade_date，截断到当日及之前
        if trade_date and "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].astype(str)
            mask = df["trade_date"] <= trade_date
            df = df.loc[mask]

        close = df["close"].astype(float)
        autocorr = self.compute_rank_autocorr(close)

        return self.classify_regime(autocorr)

    async def detect_from_mongo(
        self,
        trade_date: str,
        index_code: str = "399300",
        lookback_days: int = 120,
    ) -> Dict[str, object]:
        """
        从 MongoDB 加载指数数据并检测风格（便捷方法）

        Args:
            trade_date: 截面日期 YYYY-MM-DD
            index_code: 指数代码，默认 399300（沪深300）
            lookback_days: 回顾天数，默认 120 天

        Returns:
            风格检测结果字典
        """
        try:
            qs = get_data_query_service()
            end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=lookback_days)
            start_date = start_dt.strftime("%Y-%m-%d")

            df, _ = await qs.get_ohlcv(index_code, start_date, trade_date, period="daily")
            return self.detect(df, trade_date)
        except Exception as e:
            logger.warning(f"StyleDetector: 从MongoDB加载指数数据失败: {e}")
            return self.classify_regime(float("nan"))