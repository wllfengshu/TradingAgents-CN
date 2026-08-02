"""
策略层完整管道（截面因子方案）

新版策略层不再依赖大模型/ML 预测，直接基于 zstock.factor_management的 CrossSectionStrategyPipeline 产出的 top K 信号做：

    信号生成 (factor pipeline) → 组合优化 → 风控检查 → 换手控制 → 最终持仓
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .signal_generator import SignalGenerator
from .portfolio_optimizer import PortfolioOptimizer
from .risk_manager import RiskManager
from .turnover_controller import TurnoverController

logger = logging.getLogger(__name__)

# 策略参数配置文件路径
_STRATEGY_PARAMS_PATH = Path(__file__).parent.parent / "common" / "config" / "strategy_params.json"


class StrategyPipeline:
    """策略层全流程：信号 → 组合 → 风控 → 换手。"""

    _config_cache: Optional[Dict] = None

    def __init__(
        self,
        signal_generator: Optional[SignalGenerator] = None,
        portfolio_optimizer: Optional[PortfolioOptimizer] = None,
        risk_manager: Optional[RiskManager] = None,
        turnover_controller: Optional[TurnoverController] = None,
    ):
        self.signal_generator = signal_generator or SignalGenerator()
        self.portfolio_optimizer = portfolio_optimizer or PortfolioOptimizer()
        self.risk_manager = risk_manager or RiskManager()
        self.turnover_controller = turnover_controller or TurnoverController()
        self.pipeline_result: Dict[str, Any] = {}
        logger.info("✅ StrategyPipeline(factor) 初始化完成")

    async def execute_full_pipeline(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 60,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
        current_positions: Optional[pd.DataFrame] = None,
        total_capital: float = 1e7,
        config: Optional[Dict] = None,
        prebuilt_data: Optional[Dict] = None,
        precomputed_signals: Optional[pd.DataFrame] = None,  # ← 新增
    ) -> Dict:
        """
        Args:
            trade_date: 交易日，'YYYY-MM-DD'，默认今天。
            lookback_days: OHLCV 回看天数。
            sectors: 关心的板块名列表，None 走默认。
            max_stocks: 限制处理股票数（调试用）。
            current_positions: 当前持仓（code, weight, [score]）。
            total_capital: 总资金（用于换手成本估算）。
            config: 各阶段配置覆盖，结构见 _default_config()。
            prebuilt_data: 离线测试时直接注入 factor pipeline 的入参，
                跳过 query_service 真实拉数。
        """
        cfg = self._merge_config(config)
        td = trade_date or datetime.now().strftime('%Y-%m-%d')

        logger.info("=" * 60)
        logger.info(f"🚀 策略层管道启动 trade_date={td}")
        logger.info("=" * 60)

        results: Dict[str, Any] = {}

        # 1. 信号
        if precomputed_signals is not None:
            signals_df = precomputed_signals
            logger.info(f"✅ 使用预计算信号: {len(signals_df)} 只候选")
        else:
            signals_df = await self.signal_generator.generate_signals(
                trade_date=td,
                lookback_days=lookback_days,
                sectors=sectors,
                max_stocks=max_stocks,
                prebuilt_data=prebuilt_data,
            )
        results['signals'] = signals_df
        if signals_df.empty:
            logger.error("⚠️ 无信号，管道终止")
            self.pipeline_result = {'status': 'no_signals', 'trade_date': td, 'results': results}
            return self.pipeline_result

        # 2. 组合优化
        try:
            opt_result = self.portfolio_optimizer.optimize_portfolio(
                signals_df=signals_df,
                **cfg['portfolio_optimization'],
            )
        except Exception as e:
            logger.error(f"❌ 组合优化异常: {e}")
            opt_result = {'status': 'failed', 'reason': str(e), 'holdings_df': pd.DataFrame()}
        results['optimization'] = opt_result
        if opt_result.get('status') != 'success':
            self.pipeline_result = {'status': 'optimization_failed', 'trade_date': td, 'results': results}
            return self.pipeline_result
        optimized_holdings = opt_result['holdings_df'].copy()
        # 回填 sector，便于风控按行业检查
        if 'sector_code' in signals_df.columns and 'sector_code' not in optimized_holdings.columns:
            mapping = signals_df.set_index('code')['sector_code'].to_dict()
            optimized_holdings['sector_code'] = optimized_holdings['code'].map(mapping).fillna('UNKNOWN')

        # 3. 风控检查 + 纠正
        try:
            compliance, optimized_holdings = self.risk_manager.apply_corrections(
                optimized_holdings, signals_df,
            )
        except Exception as e:
            logger.error(f"❌ 风控检查异常: {e}")
            compliance = {'status': 'error', 'issues': [str(e)], 'metrics': {}}
        results['risk_check'] = compliance
        logger.info(f"🔍 风控状态: {compliance['status']} issues={len(compliance['issues'])}")

        # 4. 换手控制
        try:
            final_holdings = self.turnover_controller.apply_buffer_mechanism(
                new_holdings=optimized_holdings,
                current_holdings=current_positions,
                buffer_threshold=cfg['turnover_control']['buffer_threshold'],
            )
            trading_costs = self.turnover_controller.estimate_trading_costs(
                current_holdings=current_positions,
                new_holdings=final_holdings,
                total_capital=total_capital,
            )
        except Exception as e:
            logger.error(f"❌ 换手控制异常: {e}")
            final_holdings = optimized_holdings.copy()
            trading_costs = {'turnover': 0.0, 'cost_pct': 0.0, 'cost_amount': 0.0, 'fee_rate': 0.0}
        results['final_holdings'] = final_holdings
        results['trading_costs'] = trading_costs

        summary = {
            'status': 'success',
            'trade_date': td,
            'execution_time': datetime.now().astimezone().isoformat(),
            'statistics': {
                'signals_count': len(signals_df),
                'optimized_holdings': len(optimized_holdings),
                'final_holdings': len(final_holdings),
                'risk_status': compliance['status'],
                'turnover': trading_costs['turnover'],
                'cost_pct': trading_costs['cost_pct'],
                'total_capital': total_capital,
            },
            'results': results,
        }
        self.pipeline_result = summary
        logger.info(
            f"✅ 完成: signals={len(signals_df)} opt={len(optimized_holdings)} "
            f"final={len(final_holdings)} turnover={trading_costs['turnover']:.2%}"
        )
        return summary

    def get_target_positions(self) -> pd.DataFrame:
        if not self.pipeline_result:
            return pd.DataFrame()
        return self.pipeline_result.get('results', {}).get('final_holdings', pd.DataFrame())

    @staticmethod
    def _default_config() -> Dict[str, Dict]:
        """
        从 strategy_params.json 加载策略参数，转换为 pipeline 各阶段所需的配置结构。
        配置文件是策略参数的唯一数据源（Single Source of Truth）。
        结果缓存于类变量，进程生命周期内只读一次磁盘。
        """
        if StrategyPipeline._config_cache is not None:
            return StrategyPipeline._config_cache

        params = {}
        try:
            with open(_STRATEGY_PARAMS_PATH, 'r', encoding='utf-8') as f:
                params = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"❌ 加载策略参数失败: {_STRATEGY_PARAMS_PATH}, {e}")

        final_score = params.get('final_score', {})
        top_k = final_score.get('top_k', 5)

        cfg = {
            'portfolio_optimization': {
                'min_holdings': max(1, top_k - 2),        # 允许比 top_k 少2只（容忍信号不足）
                'max_holdings': top_k,
                'max_weight_per_stock': round(1.0 / max(top_k, 1), 2),  # 等权上限
                'weighting': 'score',
            },
            'risk_management': {
                'hard_stop_loss_pct': params.get('exit_rules', {}).get('hard_stop_loss_pct', -0.08),
            },
            'turnover_control': {
                'buffer_threshold': 0.15,
            },
        }
        StrategyPipeline._config_cache = cfg
        return cfg

    @classmethod
    def _merge_config(cls, override: Optional[Dict]) -> Dict[str, Dict]:
        base = cls._default_config()
        if not override:
            return base
        for k, v in override.items():
            if k in base and isinstance(v, dict):
                base[k] = {**base[k], **v}
            else:
                base[k] = v
        return base
