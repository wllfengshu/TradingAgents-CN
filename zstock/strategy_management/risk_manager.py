"""
风险管理（截面因子方案）

针对因子管道输出的持仓权重做合规性检查：
- 权重和≈1.0
- 单股权重不超过上限
- Top-N 集中度
- 持仓数量在指定区间
- 行业暴露上限

风控参数从 strategy_params.json 读取，与组合优化器保持同源。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 策略参数配置文件路径（与 pipeline.py 同源）
_STRATEGY_PARAMS_PATH = Path(__file__).parent.parent / "common" / "config" / "strategy_params.json"


def _load_risk_limits_from_config() -> Dict:
    """
    从 strategy_params.json 读取风控相关参数，构建 risk_limits。
    结果缓存于函数属性，进程生命周期内只读一次磁盘。
    """
    if _load_risk_limits_from_config._cache is not None:
        return _load_risk_limits_from_config._cache

    try:
        with open(_STRATEGY_PARAMS_PATH, 'r', encoding='utf-8') as f:
            params = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"⚠️ 无法加载策略参数: {e}，使用内置保底值")
        params = {}

    top_k = params.get('final_score', {}).get('top_k', 5)
    top_sectors = params.get('sector_layer', {}).get('top_sectors', 3)
    portfolio = params.get('portfolio', {})

    limits = {
        'top_k': top_k,
        'min_holdings': max(1, top_k - 2),
        'max_holdings': top_k * 2,  # 允许 buffer / 最短持有保留旧持仓
        'max_weight_per_stock': float(
            portfolio.get('max_weight_per_stock', round(1.0 / max(top_k, 1), 2))
        ),
        'max_top5_concentration': 1.0,  # top_k=5 时不做 top5 集中度限制
        'max_sector_exposure': float(
            portfolio.get(
                'max_sector_exposure',
                round(1.0 / max(top_sectors, 1) + 0.15, 2),
            )
        ),
        'weight_sum_tolerance': 1e-3,
        # 允许黄灯缩仓后权重和 < 1（现金）
        'allow_cash': True,
    }
    _load_risk_limits_from_config._cache = limits
    return limits

_load_risk_limits_from_config._cache = None


class RiskManager:
    """合规性检查器。"""

    def __init__(self, risk_limits: Optional[Dict] = None):
        base_limits = _load_risk_limits_from_config()
        # 外部传入的 risk_limits 可以覆盖配置文件的值
        if risk_limits:
            base_limits.update(risk_limits)
        self.risk_limits = base_limits
        logger.info(f"✅ RiskManager 初始化完成: limits={self.risk_limits}")

    def check_compliance(
        self,
        holdings_df: pd.DataFrame,
        signals_df: Optional[pd.DataFrame] = None,
        limits_override: Optional[Dict] = None,
    ) -> Dict:
        """
        Args:
            holdings_df: 列 code, weight，可选 sector_code / score。
            signals_df: 可选；如果 holdings_df 没有 sector_code，
                可以从 signals_df 按 code 查回填。

        Returns:
            {
                'status': 'passed' | 'warning',
                'issues': [str, ...],
                'metrics': {...},
            }
        """
        limits = {**self.risk_limits, **(limits_override or {})}
        issues: List[str] = []
        metrics: Dict = {}

        if holdings_df is None or holdings_df.empty:
            return {'status': 'warning', 'issues': ['empty holdings'], 'metrics': {}}

        df = holdings_df.copy()
        if 'sector_code' not in df.columns and signals_df is not None and not signals_df.empty:
            mapping = signals_df.drop_duplicates(subset='code', keep='last').set_index('code')['sector_code'].to_dict() if 'sector_code' in signals_df.columns else {}
            df['sector_code'] = df['code'].map(mapping).fillna('UNKNOWN')

        weights = df['weight'].astype(float).values
        n = len(df)
        top_k = limits.get('top_k', 5)
        metrics['n_holdings'] = n
        metrics['weight_sum'] = float(weights.sum())
        metrics['max_weight'] = float(weights.max())
        top_n = float(np.sort(weights)[::-1][:top_k].sum())
        metrics[f'top{top_k}_concentration'] = top_n

        if n < limits['min_holdings']:
            issues.append(f"持仓数 {n} < min_holdings={limits['min_holdings']}")
        if n > limits['max_holdings']:
            issues.append(f"持仓数 {n} > max_holdings={limits['max_holdings']}")
        wsum = metrics['weight_sum']
        tol = limits['weight_sum_tolerance']
        if limits.get('allow_cash', True):
            if wsum > 1.0 + tol or wsum < -tol:
                issues.append(f"权重和={wsum:.4f} 非法（允许现金时须 ∈ [0,1]）")
        elif abs(wsum - 1.0) > tol:
            issues.append(f"权重和={wsum:.4f} 偏离 1.0 超出容忍")
        if metrics['max_weight'] > limits['max_weight_per_stock'] + 1e-6:
            issues.append(f"单股权重 {metrics['max_weight']:.4f} > 上限 {limits['max_weight_per_stock']}")
        if top_n > limits['max_top5_concentration'] + 1e-6:
            issues.append(f"Top{top_k} 集中度 {top_n:.4f} > {limits['max_top5_concentration']}")

        if 'sector_code' in df.columns:
            sector_exposure = df.groupby('sector_code')['weight'].sum().sort_values(ascending=False)
            metrics['sector_exposure'] = sector_exposure.to_dict()
            top_sector_w = float(sector_exposure.iloc[0]) if len(sector_exposure) else 0.0
            metrics['max_sector_exposure'] = top_sector_w
            if top_sector_w > limits['max_sector_exposure'] + 1e-6:
                issues.append(
                    f"板块 {sector_exposure.index[0]} 暴露 {top_sector_w:.4f} > "
                    f"上限 {limits['max_sector_exposure']}"
                )

        return {
            'status': 'passed' if not issues else 'warning',
            'issues': issues,
            'metrics': metrics,
        }

    def apply_corrections(
        self,
        holdings_df: pd.DataFrame,
        signals_df: Optional[pd.DataFrame] = None,
        limits_override: Optional[Dict] = None,
    ) -> tuple:
        """
        检查风控，若存在可纠正的违规则自动修正持仓。

        可纠正的违规：
        - 单股权重超过上限 → 截断至上限，剩余权重按比例重分配
        - 板块暴露超限 → 截断该板块所有股票，权重转给未超限板块

        Returns:
            (compliance_dict, corrected_holdings_df)
        """
        limits = {**self.risk_limits, **(limits_override or {})}
        compliance = self.check_compliance(
            holdings_df, signals_df, limits_override=limits_override
        )

        if compliance['status'] == 'passed':
            return compliance, holdings_df

        df = holdings_df.copy()

        # 回填 sector_code（供板块纠正使用）
        if 'sector_code' not in df.columns and signals_df is not None and not signals_df.empty:
            if 'sector_code' in signals_df.columns:
                mapping = (
                    signals_df.drop_duplicates(subset='code', keep='last')
                    .set_index('code')['sector_code']
                    .to_dict()
                )
                df['sector_code'] = df['code'].map(mapping).fillna('UNKNOWN')

        correctable_issues = []
        remaining_issues = []
        for issue in compliance['issues']:
            if '单股权重' in issue or '板块' in issue:
                correctable_issues.append(issue)
            else:
                remaining_issues.append(issue)

        if not correctable_issues:
            # 无可用规则纠正，返回原持仓
            return compliance, holdings_df

        # 1. 单股上限纠正：截断超出部分，按比例重分配给未触顶股票
        weights = df['weight'].astype(float).values.copy()
        cap = limits['max_weight_per_stock']
        over_mask = weights > cap + 1e-9
        if over_mask.any():
            excess = float((weights[over_mask] - cap).sum())
            weights[over_mask] = cap
            under_mask = ~over_mask
            if under_mask.any() and weights[under_mask].sum() > 0:
                weights[under_mask] += excess * (weights[under_mask] / weights[under_mask].sum())
            elif under_mask.any():
                weights[under_mask] += excess / under_mask.sum()
            df['weight'] = weights  # ← 写回 DataFrame，供后续板块纠正使用
            logger.info(f"🔧 风控纠正: 单股权重截断至 {cap:.4f}")

        # 2. 板块暴露纠正：超限板块的股票整体降权，多余权重分配给未超限板块
        if 'sector_code' in df.columns:
            max_sector = limits['max_sector_exposure']
            sector_weights = df.groupby('sector_code')['weight'].sum()
            for sector, sw in sector_weights.items():
                if sw > max_sector + 1e-9:
                    sector_mask = df['sector_code'] == sector
                    scale = max_sector / sw
                    sector_excess = float(df.loc[sector_mask, 'weight'].sum() * (1 - scale))
                    df.loc[sector_mask, 'weight'] = df.loc[sector_mask, 'weight'].astype(float) * scale
                    other_mask = ~sector_mask
                    other_w = df.loc[other_mask, 'weight'].astype(float)
                    if other_w.sum() > 0:
                        df.loc[other_mask, 'weight'] = other_w + sector_excess * (other_w / other_w.sum())
                    logger.info(f"🔧 风控纠正: 板块 {sector} 降权至 {max_sector:.2%}")

        # 最终：若超配则缩放到 ≤1；不主动加仓填满（允许现金）
        wsum = float(df['weight'].astype(float).sum())
        if wsum > 1.0 + 1e-9:
            df['weight'] = df['weight'].astype(float) / wsum
        # 再次截断单股上限（缩放后可能仍略超）
        cap = limits['max_weight_per_stock']
        df.loc[df['weight'] > cap, 'weight'] = cap

        # 纠正后重新检查，确保指标和状态反映真实结果
        final_compliance = self.check_compliance(df, signals_df)
        corrected_compliance = {
            'status': final_compliance['status'],
            'issues': final_compliance['issues'],
            'metrics': final_compliance['metrics'],
            'corrected_issues': correctable_issues,
        }
        return corrected_compliance, df
