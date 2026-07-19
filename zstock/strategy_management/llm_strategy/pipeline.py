"""
策略层完整管道（旧版 ML 方案）

⚠️ DEPRECATED — 此文件调用的是旧版 API 签名（risk_manager.check_compliance(weights=...,
   industry_data=..., total_capital=...)、turnover_controller.apply_buffer_mechanism(
   ..., total_capital=...)），与当前截面因子方案的模块接口不兼容，调用会直接报 TypeError。
   新的截面因子方案请使用 zstock.strategy_management.pipeline.StrategyPipeline。

协调信号生成、组合优化、风险管理、换手控制等多个模块。

工作流：
1. 🎯 信号生成：模型预测 → 过滤 → 排序
2. 📊 组合优化：在约束条件下优化权重
3. 🔍 风控检查：检查是否符合风险标准
4. 🔄 换手控制：应用 Buffer 机制
5. 💾 持仓输出：生成最终目标持仓
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class StrategyPipeline:
    """
    策略层完整管道

    协调所有策略组件完成从信号生成到最终持仓的全流程。

    属性：
        signal_generator: 信号生成器
        portfolio_optimizer: 组合优化器
        risk_manager: 风险管理器
        turnover_controller: 换手控制器
    """

    def __init__(self, signal_generator=None, portfolio_optimizer=None,
                 risk_manager=None, turnover_controller=None):
        """
        初始化策略管道

        Args:
            signal_generator: 信号生成器
            portfolio_optimizer: 组合优化器
            risk_manager: 风险管理器
            turnover_controller: 换手控制器
        """
        # 如果未提供，则创建新的实例
        if signal_generator is None:
            from .signal_generator import SignalGenerator
            signal_generator = SignalGenerator()

        if portfolio_optimizer is None:
            from .portfolio_optimizer import PortfolioOptimizer
            portfolio_optimizer = PortfolioOptimizer()

        if risk_manager is None:
            from .risk_manager import RiskManager
            risk_manager = RiskManager()

        if turnover_controller is None:
            from .turnover_controller import TurnoverController
            turnover_controller = TurnoverController()

        self.signal_generator = signal_generator
        self.portfolio_optimizer = portfolio_optimizer
        self.risk_manager = risk_manager
        self.turnover_controller = turnover_controller

        self.pipeline_result = {}

        logger.info("✅ StrategyPipeline 初始化完成")

    def execute_full_pipeline(self,
                             factors_df: pd.DataFrame,
                             model_name: str,
                             trade_status_df: Optional[pd.DataFrame] = None,
                             industry_data: Optional[pd.DataFrame] = None,
                             market_cap_data: Optional[pd.DataFrame] = None,
                             market_data: Optional[pd.DataFrame] = None,
                             current_positions: Optional[pd.DataFrame] = None,
                             total_capital: float = 1e7,
                             config: Optional[Dict] = None) -> Dict:
        """
        执行完整的策略管道

        Args:
            factors_df: 因子数据
            model_name: 模型名称
            trade_status_df: 交易状态数据
            industry_data: 行业数据
            market_cap_data: 市值数据
            market_data: 市场数据
            current_positions: 当前持仓
            total_capital: 总资金量
            config: 配置字典

        Returns:
            Dict: 完整管道结果
        """
        logger.info("="*60)
        logger.info("🚀 启动策略层完整管道")
        logger.info("="*60)

        if config is None:
            config = {
                'signal_generation': {
                    'top_n': 20,
                    'score_threshold': -np.inf,
                },
                'portfolio_optimization': {
                    'min_holdings': 15,
                    'max_holdings': 25,
                    'max_weight_per_stock': 0.08,
                },
                'risk_management': {},
                'turnover_control': {
                    'buffer_threshold': 0.15,
                },
            }

        results = {}

        try:
            # ============================================================
            # 第一步：信号生成
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("第一步：📊 信号生成")
            logger.info("="*60)

            # 加载模型
            if not self.signal_generator.load_model(model_name):
                logger.error("❌ 模型加载失败，管道中断")
                return {'status': 'failed', 'error': 'Model loading failed'}

            # 生成信号
            signals_df = self.signal_generator.generate_signals(
                factors_df=factors_df,
                trade_status_df=trade_status_df,
                top_n=config['signal_generation'].get('top_n', 20)
            )

            if signals_df.empty:
                logger.error("❌ 信号生成失败")
                return {'status': 'failed', 'error': 'Signal generation failed'}

            results['signals'] = signals_df

            # ============================================================
            # 第二步：组合优化
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("第二步：📈 组合优化")
            logger.info("="*60)

            optimization_result = self.portfolio_optimizer.optimize_portfolio(
                signals_df=signals_df,
                **config['portfolio_optimization']
            )

            if optimization_result.get('status') == 'failed':
                logger.error("❌ 组合优化失败")
                return {'status': 'failed', 'error': 'Portfolio optimization failed'}

            results['optimization'] = optimization_result
            optimized_holdings = optimization_result.get('holdings_df')

            # ============================================================
            # 第三步：风控检查
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("第三步：🔍 风险管理")
            logger.info("="*60)

            weights = optimization_result.get('weights', np.array([]))

            compliance_result = self.risk_manager.check_compliance(
                weights=weights,
                holdings_df=optimized_holdings,
                industry_data=industry_data,
                market_cap_data=market_cap_data,
                market_data=market_data,
                total_capital=total_capital,
            )

            results['risk_check'] = compliance_result

            # 检查是否通过
            if compliance_result['status'] == 'passed':
                logger.info("✅ 通过风控检查")
            else:
                logger.warning(f"⚠️ 存在风险警告，问题数: {len(compliance_result['issues'])}")

            # ============================================================
            # 第四步：换手控制
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("第四步：🔄 换手控制")
            logger.info("="*60)

            final_holdings = self.turnover_controller.apply_buffer_mechanism(
                new_holdings=optimized_holdings,
                current_holdings=current_positions,
                buffer_threshold=config['turnover_control'].get('buffer_threshold', 0.15),
                total_capital=total_capital,
            )

            # 估算成本
            trading_costs = self.turnover_controller.estimate_trading_costs(
                current_holdings=current_positions if current_positions is not None else pd.DataFrame(),
                new_holdings=final_holdings,
                total_capital=total_capital,
            )

            results['trading_costs'] = trading_costs

            # ============================================================
            # 第五步：生成最终持仓
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("第五步：📋 最终持仓")
            logger.info("="*60)

            final_positions = self.turnover_controller.generate_final_positions(
                holdings_df=final_holdings,
                trade_date=pd.Timestamp.today().strftime('%Y-%m-%d'),
            )

            results['final_positions'] = final_positions

            # ============================================================
            # 汇总结果
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("✅ 策略管道执行完成")
            logger.info("="*60)

            summary = {
                'status': 'success',
                'execution_time': datetime.utcnow().isoformat(),
                'statistics': {
                    'signals_count': len(signals_df),
                    'optimized_holdings': len(optimized_holdings),
                    'final_holdings': len(final_holdings),
                    'total_capital': total_capital,
                    'trading_cost_pct': trading_costs['cost_pct'] * 100,
                    'risk_status': compliance_result['status'],
                },
                'results': results,
            }

            logger.info(f"\n📊 执行摘要:")
            logger.info(f"   - 信号数: {summary['statistics']['signals_count']}")
            logger.info(f"   - 优化持仓数: {summary['statistics']['optimized_holdings']}")
            logger.info(f"   - 最终持仓数: {summary['statistics']['final_holdings']}")
            logger.info(f"   - 交易成本率: {summary['statistics']['trading_cost_pct']:.2f}%")
            logger.info(f"   - 风险状态: {summary['statistics']['risk_status']}")

            self.pipeline_result = summary

            return summary

        except Exception as e:
            logger.error(f"❌ 管道执行失败: {e}")
            import traceback
            traceback.print_exc()
            return {'status': 'failed', 'error': str(e)}

    def get_target_positions(self) -> pd.DataFrame:
        """
        获取目标持仓

        Returns:
            pd.DataFrame: 目标持仓数据框
        """
        if not self.pipeline_result:
            logger.warning("⚠️ 管道未执行")
            return pd.DataFrame()

        final_positions = self.pipeline_result.get('results', {}).get('final_positions', {})
        holdings_data = final_positions.get('holdings', [])

        if holdings_data:
            return pd.DataFrame(holdings_data)

        return pd.DataFrame()

    def save_results(self, output_dir: str = 'strategy_results/') -> bool:
        """
        保存管道执行结果

        Args:
            output_dir: 输出目录

        Returns:
            bool: 保存是否成功
        """
        try:
            import os
            import json

            os.makedirs(output_dir, exist_ok=True)

            # 保存为 JSON
            timestamp = pd.Timestamp.today().strftime('%Y%m%d_%H%M%S')
            json_file = os.path.join(output_dir, f'strategy_result_{timestamp}.json')

            with open(json_file, 'w') as f:
                json.dump(self.pipeline_result, f, indent=2, default=str)

            logger.info(f"✅ 结果已保存: {json_file}")

            return True

        except Exception as e:
            logger.error(f"❌ 保存结果失败: {e}")
            return False
