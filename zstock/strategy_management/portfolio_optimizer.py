"""
组合优化（截面因子方案）

因子管道产出的 ranked candidates 已经是按 final_score 排序的 top K，
组合优化器只负责：
1. 在 [min_holdings, max_holdings] 区间内挑名额；
2. 用 final_score（线性或 softmax）分配权重；
3. 应用 per-stock cap，超出的权重再分配；
4. 归一化。
"""

from __future__ import annotations

import logging
from typing import Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PortfolioOptimizer:
    """截面权重分配器。"""

    def __init__(self):
        logger.info("✅ PortfolioOptimizer(factor) 初始化完成")

    def optimize_portfolio(
        self,
        signals_df: pd.DataFrame,
        min_holdings: int = 5,
        max_holdings: int = 20,
        max_weight_per_stock: float = 0.20,
        weighting: str = 'score',  # 'equal' | 'score' | 'softmax'
        softmax_temperature: float = 1.0,
    ) -> Dict:
        """
        Args:
            signals_df: 至少包含 code / final_score 两列。
            min_holdings: 最少持仓数量。
            max_holdings: 最多持仓数量。
            max_weight_per_stock: 单股最大权重。
            weighting: 权重分配方式。
            softmax_temperature: softmax 温度（越大越接近等权）。

        Returns:
            {
                'status': 'success' | 'failed',
                'holdings_df': DataFrame(code, score, weight),
                'weights': np.ndarray,
            }
        """
        if signals_df is None or signals_df.empty:
            return {'status': 'failed', 'reason': 'empty signals', 'holdings_df': pd.DataFrame(), 'weights': np.array([])}

        df = signals_df.copy()
        if 'final_score' not in df.columns:
            df['final_score'] = 0.0
        df = df.sort_values('final_score', ascending=False).reset_index(drop=True)

        # 确定持仓数量：取实际候选数与 max_holdings 的较小值
        # 注意：min_holdings 不是硬约束（候选不足时无法凭空创造股票），仅作为告警阈值
        n = min(len(df), max_holdings)
        if n == 0:
            return {'status': 'failed', 'reason': 'no candidates', 'holdings_df': pd.DataFrame(), 'weights': np.array([])}

        if n < min_holdings:
            logger.warning(f"⚠️ 候选数 {n} < min_holdings={min_holdings}，按实际候选数处理")

        df = df.head(n).copy()
        scores = df['final_score'].astype(float).values

        if weighting == 'equal' or np.nanstd(scores) < 1e-9 or np.isnan(scores).all():
            w = np.ones(n) / n
        elif weighting == 'softmax':
            x = scores / max(softmax_temperature, 1e-6)
            x = x - x.max()
            w = np.exp(x)
            w = w / w.sum()
        else:  # 'score'
            shifted = scores - scores.min() + 1e-6
            w = shifted / shifted.sum()

        # 应用 per-stock 上限：超出的权重收回后再按比例分配给未上限的票。
        cap = max_weight_per_stock
        for _ in range(10):
            over = w > cap
            if not over.any():
                break
            excess = (w[over] - cap).sum()
            w[over] = cap
            if not (~over).any():
                break
            normal = ~over
            w[normal] = w[normal] + excess * (w[normal] / w[normal].sum())

        w = w / w.sum()  # 最终归一化
        df['weight'] = w

        # 过滤掉权重为零或极小的股票（score-weighting 下末尾票可能接近零）
        df = df[df['weight'] > 1e-6].copy()
        if df.empty:
            return {'status': 'failed', 'reason': 'all weights zero after cap', 'holdings_df': pd.DataFrame(), 'weights': np.array([])}
        # 重新归一化（移除零权重票后权重和可能不等于1）
        df['weight'] = df['weight'] / df['weight'].sum()
        w = df['weight'].values

        holdings_df = df[['code', 'final_score', 'weight']].rename(columns={'final_score': 'score'})
        return {
            'status': 'success',
            'holdings_df': holdings_df,
            'weights': w,
            'n_holdings': len(df),
            'max_weight_actual': float(w.max()),
        }
