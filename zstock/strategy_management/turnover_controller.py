"""
换手控制（截面因子方案）

Buffer 机制：新候选必须显著优于当前持仓最弱票才换入。

输入：
- new_holdings: 待换入的目标持仓（含 code/weight/score）
- current_holdings: 当前实际持仓（含 code/weight，可无 score）

输出：
- final_holdings：经 buffer 过滤后的目标持仓
- trading_cost：估算换手成本
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class TurnoverController:
    """Buffer 机制 + 成本估算。"""

    def __init__(self, buffer_threshold: float = 0.15, fee_rate: float = 0.0015):
        self.buffer_threshold = buffer_threshold
        self.fee_rate = fee_rate  # 双边费率上限（手续费+滑点+印花税近似）
        logger.info(f"✅ TurnoverController 初始化完成: buffer={buffer_threshold} fee={fee_rate}")

    def apply_buffer_mechanism(
        self,
        new_holdings: pd.DataFrame,
        current_holdings: Optional[pd.DataFrame] = None,
        buffer_threshold: Optional[float] = None,
    ) -> pd.DataFrame:
        """
        Args:
            new_holdings: 候选目标持仓，需含 'code', 'weight', 可选 'score'。
            current_holdings: 当前持仓，需含 'code', 'weight', 可选 'score'。
            buffer_threshold: 覆盖默认阈值。

        Returns:
            合并后的目标持仓：保留所有 current_holdings 中仍在 new_holdings
            的票；对于 new_holdings 里"新增"的票，要求其 score 显著高于
            current_holdings 最低分（× (1 + buffer)）才允许换入。
        """
        if buffer_threshold is None:
            buffer_threshold = self.buffer_threshold

        if new_holdings is None or new_holdings.empty:
            return pd.DataFrame(columns=['code', 'weight', 'score'])

        # 没有当前持仓时直接采纳新持仓
        if current_holdings is None or current_holdings.empty:
            return self._normalize(new_holdings.copy())

        # 整理列名
        new_df = new_holdings.copy()
        cur_df = current_holdings.copy()
        for df in (new_df, cur_df):
            if 'score' not in df.columns:
                df['score'] = np.nan

        new_codes = set(new_df['code'])

        kept = cur_df[cur_df['code'].isin(new_codes)].copy()
        kept_codes = set(kept['code'])

        # 新增候选必须显著优于 cur 最低分
        min_cur_score = float(cur_df['score'].dropna().min()) if cur_df['score'].notna().any() else None
        if min_cur_score is None:
            threshold_score = -np.inf
        else:
            threshold_score = min_cur_score * (1.0 + buffer_threshold) if min_cur_score > 0 else min_cur_score + abs(min_cur_score) * buffer_threshold

        additions = new_df[~new_df['code'].isin(kept_codes)].copy()
        if not additions.empty:
            if additions['score'].notna().any():
                # 有评分数据时：新增候选必须显著优于当前持仓最弱票才允许换入
                additions = additions[additions['score'] >= threshold_score]
            else:
                # 所有新增候选均无评分：拒绝换入，防止无评分股票绕过 buffer 保护
                logger.warning("⚠️ 新增候选全部无评分(score=NaN)，拒绝换入，仅保留已有持仓")
                additions = additions.iloc[0:0]  # 清空

        # 合并：kept 的权重用 new_holdings 里的最新权重
        if not kept.empty:
            new_w_map = new_df.set_index('code')['weight'].to_dict()
            kept['weight'] = kept['code'].map(new_w_map).fillna(kept['weight'])

        final = pd.concat([kept[['code', 'weight', 'score']], additions[['code', 'weight', 'score']]], ignore_index=True)
        return self._normalize(final)

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        total = float(df['weight'].sum())
        if total > 0:
            df['weight'] = df['weight'] / total
        return df.reset_index(drop=True)

    def estimate_trading_costs(
        self,
        current_holdings: Optional[pd.DataFrame],
        new_holdings: pd.DataFrame,
        total_capital: float = 1e7,
    ) -> Dict:
        """估算换手成本：turnover = 0.5 * Σ|new - old|，成本 = turnover * fee_rate。"""
        cur = current_holdings if current_holdings is not None and not current_holdings.empty else pd.DataFrame(columns=['code', 'weight'])
        new = new_holdings if new_holdings is not None and not new_holdings.empty else pd.DataFrame(columns=['code', 'weight'])

        merged = pd.merge(
            cur[['code', 'weight']].rename(columns={'weight': 'w_old'}),
            new[['code', 'weight']].rename(columns={'weight': 'w_new'}),
            on='code', how='outer',
        )
        merged = merged.infer_objects(copy=False).fillna(0.0)
        turnover = 0.5 * float((merged['w_new'] - merged['w_old']).abs().sum())
        cost_pct = turnover * self.fee_rate
        return {
            'turnover': turnover,
            'cost_pct': cost_pct,
            'cost_amount': cost_pct * total_capital,
            'fee_rate': self.fee_rate,
        }

    @staticmethod
    def generate_final_positions(
        holdings_df: pd.DataFrame,
        trade_date: Optional[str] = None,
    ) -> Dict:
        """把 holdings_df 打包成目标持仓 dict，用于持久化/对账。"""
        td = trade_date or datetime.now().strftime('%Y-%m-%d')
        if holdings_df is None or holdings_df.empty:
            return {'trade_date': td, 'holdings': [], 'count': 0}
        return {
            'trade_date': td,
            'holdings': holdings_df.to_dict(orient='records'),
            'count': len(holdings_df),
        }
