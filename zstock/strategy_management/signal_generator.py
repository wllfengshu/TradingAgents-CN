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
        生成截面信号。

        通过因子层的唯一入口 compute_signals() 获取信号。
        """
        td = trade_date or datetime.now().strftime("%Y-%m-%d")
        logger.info(f"🎯 生成截面信号: trade_date={td}")

        # 使用因子层的唯一入口
        df = await self.factor_pipeline.compute_signals(
            trade_date=td,
            lookback_days=lookback_days,
            sectors=sectors,
            max_stocks=max_stocks,
            prebuilt_data=prebuilt_data,
            prefer_precomputed=prefer_precomputed,
        )

        self.signals_history[td] = df
        logger.info(
            f"✅ 信号生成完成: universe={len(df)} "
            f"grade={df.attrs.get('market_grade', '?')}"
        )
        return df
