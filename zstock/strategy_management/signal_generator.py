"""
信号生成模块（截面因子方案）

直接复用 zstock.factor_management.pipeline.CrossSectionStrategyPipeline，
不再走「机器学习模型预测」路径。

流程：
1. 通过 CrossSectionStrategyPipeline 拉真实数据并运行 M0~M5 流程；
2. 把最终 top K 候选转成统一的 signals DataFrame；
3. 字段对外保持稳定：trade_date, code, sector_code, final_score,
   dragon_score, rank, signal_type。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

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
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
        prebuilt_data: Optional[Dict] = None,
    ) -> pd.DataFrame:
        """
        生成截面信号。

        Args:
            trade_date: 交易日 'YYYY-MM-DD'，None 表示今天。
            lookback_days: OHLCV 回看天数。
            sectors: 关心的板块；None 走默认。
            max_stocks: 限制处理的股票数（调试用）。
            prebuilt_data: 已经预先构造好的 run_pipeline 入参，传入则直接使用，
                不再调用 query_service（用于回测/单测离线数据注入）。
        """
        td = trade_date or datetime.now().strftime('%Y-%m-%d')
        logger.info(f"🎯 生成截面信号: trade_date={td}")

        if prebuilt_data is not None:
            data = prebuilt_data
        else:
            data = await self.factor_pipeline.load_real_data(
                trade_date=td,
                lookback_days=lookback_days,
                sectors=sectors,
                max_stocks=max_stocks,
            )

        ranked = await self.factor_pipeline.run_pipeline(**data)
        if not ranked:
            logger.error("⚠️ 因子管道无信号输出")
            df = pd.DataFrame(columns=[
                'trade_date', 'code', 'sector_code', 'final_score',
                'dragon_score', 'rank', 'signal_type', 'created_at',
            ])
            self.signals_history[td] = df
            return df

        rows = []
        now_iso = datetime.now().astimezone().isoformat()
        for i, sig in enumerate(ranked, start=1):
            rows.append({
                'trade_date': td,
                'code': sig.get('code'),
                'sector_code': sig.get('sector_code'),
                'final_score': float(sig.get('final_score', 0.0)),
                'dragon_score': float(sig.get('dragon_score', 0.0)),
                'rank': i,
                'signal_type': 'buy',
                'created_at': now_iso,
            })
        df = pd.DataFrame(rows)
        self.signals_history[td] = df
        logger.info(f"✅ 信号生成完成: {len(df)} 条")
        return df
