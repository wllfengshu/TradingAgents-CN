"""
信号生成模块（截面因子方案）

直接复用 zstock.factor_management.pipeline.CrossSectionStrategyPipeline，
输出 schema 与 score_signals() 完全一致，供 StrategyPipeline / 实时选股使用。

流程：
1. 优先 score_signals()（Mongo 预计算，极速）；
2. 否则 score_signals_live()（现场 M0~M5，与预计算同 schema）；
3. 字段：final_score, dragon_score, force_composite_score, rank, signal_type(buy/watch),
   market_risk_level, position_scale_factor 及 df.attrs 元数据。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

logger = logging.getLogger(__name__)


class SignalGenerator:
    """因子流水线信号生成器。"""

    def __init__(self, factor_pipeline: Optional[CrossSectionStrategyPipeline] = None):
        self.factor_pipeline = factor_pipeline or CrossSectionStrategyPipeline()
        self.signals_history: Dict[str, pd.DataFrame] = {}
        logger.info("✅ SignalGenerator(factor-pipeline) 初始化完成")

    async def generate_signals(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 60,
        sectors: Optional[list] = None,
        max_stocks: Optional[int] = None,
        prebuilt_data: Optional[Dict] = None,
        prefer_precomputed: bool = True,
    ) -> pd.DataFrame:
        """
        生成截面信号（与 score_signals 同 schema）。

        Args:
            trade_date: 交易日 'YYYY-MM-DD'，None 表示今天。
            lookback_days: OHLCV 回看天数（实时路径）。
            sectors: 关心的板块；None 走默认。
            max_stocks: 限制处理的股票数（调试用）。
            prebuilt_data: 已构造好的 run_pipeline 入参，跳过 load_real_data。
            prefer_precomputed: True 时优先尝试 Mongo 预计算 score_signals。
        """
        td = trade_date or datetime.now().strftime("%Y-%m-%d")
        logger.info(f"🎯 生成截面信号: trade_date={td}")

        df: pd.DataFrame

        if prebuilt_data is None and prefer_precomputed:
            try:
                df = await self.factor_pipeline.score_signals(td)
                logger.info(
                    f"✅ 预计算信号: universe={len(df)} "
                    f"grade={df.attrs.get('market_grade', '?')}"
                )
                self.signals_history[td] = df
                return df
            except ValueError as e:
                logger.info(f"预计算不可用，走实时计算: {e}")

        df = await self.factor_pipeline.score_signals_live(
            trade_date=td,
            lookback_days=lookback_days,
            sectors=sectors,
            max_stocks=max_stocks,
            prebuilt_data=prebuilt_data,
        )
        self.signals_history[td] = df
        logger.info(
            f"✅ 实时信号: universe={len(df)} "
            f"grade={df.attrs.get('market_grade', '?')}"
        )
        return df
