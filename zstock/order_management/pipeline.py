"""
执行层完整管道

协调订单生成、执行、成交回报处理等多个模块。

工作流：
1. 📋 订单生成：目标持仓 → 订单列表
2. 🎯 策略执行：根据时间选择执行策略
3. 📤 订单提交：通过 XtQuant 提交订单
4. 📥 回报处理：处理成交回报、更新持仓
5. 🔍 对账验证：本地持仓 vs XtQuant 持仓
"""

import logging
import pandas as pd
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class OrderManagementPipeline:
    """
    执行层完整管道

    协调所有执行组件完成从目标持仓到实际持仓的全流程。
    """

    def __init__(self, order_generator=None, xtquant_executor=None,
                 trade_settlement=None, execution_strategy=None):
        """
        初始化执行管道

        Args:
            order_generator: 订单生成器
            xtquant_executor: XtQuant 执行器
            trade_settlement: 成交回报处理器
            execution_strategy: 执行策略管理器
        """
        # 如果未提供，则创建新的实例
        if order_generator is None:
            from .order_generator import OrderGenerator
            order_generator = OrderGenerator()

        if xtquant_executor is None:
            from .xtquant_executor import XtQuantExecutor
            xtquant_executor = XtQuantExecutor()

        if trade_settlement is None:
            from .trade_settlement import TradeSettlement
            trade_settlement = TradeSettlement()

        if execution_strategy is None:
            from .execution_strategy import ExecutionStrategy
            execution_strategy = ExecutionStrategy()

        self.order_generator = order_generator
        self.xtquant_executor = xtquant_executor
        self.trade_settlement = trade_settlement
        self.execution_strategy = execution_strategy

        self.pipeline_result = {}

        logger.info("✅ OrderManagementPipeline 初始化完成")

    async def execute_full_pipeline(self,
                             target_positions: pd.DataFrame,
                             current_positions: Optional[pd.DataFrame] = None,
                             trade_date: Optional[str] = None,
                             price_map: Optional[Dict[str, float]] = None,
                             total_capital: float = 1e7) -> Dict:
        """执行完整的执行管道。

        Args:
            target_positions: 目标持仓 DataFrame。
            current_positions: 当前持仓 DataFrame。
            trade_date: 交易日期。
            price_map: 用于把 weight 换算成股数的 {code: 最新价}，由调用方
                提前从行情服务取好（避免在订单生成器里偷偷起异步调用）。
            total_capital: 总资金。
        """
        logger.info("="*60)
        logger.info("🚀 启动执行层完整管道")
        logger.info("="*60)

        results = {}

        try:
            logger.info("\n" + "="*60)
            logger.info("第一步：📋 订单生成")
            logger.info("="*60)

            orders = self.order_generator.generate_orders(
                target_positions=target_positions,
                current_positions=current_positions,
                trade_date=trade_date,
                price_map=price_map,
                total_capital=total_capital,
            )

            if not orders:
                logger.error("❌ 未生成任何订单")
                return {'status': 'failed', 'error': 'No orders generated'}

            results['orders'] = orders

            # ============================================================
            # 第二步：策略选择和执行
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("第二步：🎯 策略执行")
            logger.info("="*60)

            current_strategy = self.execution_strategy.get_current_strategy()
            logger.info(f"   当前策略: {current_strategy}")

            submitted_count = await self.execution_strategy.execute_with_strategy(
                orders=orders,
                executor=self.xtquant_executor,
                strategy=None,  # 自动选择
            )

            results['submitted_count'] = submitted_count
            results['submitted_orders'] = await self.xtquant_executor.query_all_orders()

            # ============================================================
            # 第三步：账户信息查询
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("第三步：📊 账户查询")
            logger.info("="*60)

            account_info = self.xtquant_executor.get_account_info()
            logger.info(f"   现金: {account_info['cash']:.0f}")
            logger.info(f"   总资产: {account_info['total_value']:.0f}")

            results['account_info'] = account_info

            # ============================================================
            # 第四步：持仓查询
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("第四步：💼 持仓查询")
            logger.info("="*60)

            positions = self.xtquant_executor.get_positions()
            logger.info(f"   当前持仓: {len(positions)} 只股票")

            results['current_positions'] = positions

            # ============================================================
            # 第五步：对账验证
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("第五步：🔍 对账验证")
            logger.info("="*60)

            reconcile_result = self.trade_settlement.reconcile_positions(positions)

            results['reconciliation'] = reconcile_result

            # ============================================================
            # 汇总结果
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("✅ 执行管道完成")
            logger.info("="*60)

            summary = {
                'status': 'success',
                'statistics': {
                    'target_positions': len(target_positions),
                    'orders_generated': len(orders),
                    'orders_submitted': submitted_count,
                    'positions_count': len(positions),
                    'cash_available': account_info['cash'],
                    'total_value': account_info['total_value'],
                    'reconciliation_status': reconcile_result['status'],
                },
                'results': results,
            }

            logger.info(f"\n📊 执行摘要:")
            logger.info(f"   - 目标持仓数: {summary['statistics']['target_positions']}")
            logger.info(f"   - 生成订单数: {summary['statistics']['orders_generated']}")
            logger.info(f"   - 已提交: {summary['statistics']['orders_submitted']}")
            logger.info(f"   - 当前持仓数: {summary['statistics']['positions_count']}")
            logger.info(f"   - 对账状态: {summary['statistics']['reconciliation_status']}")

            self.pipeline_result = summary

            return summary

        except Exception as e:
            logger.error(f"❌ 管道执行失败: {e}")
            import traceback
            traceback.print_exc()
            return {'status': 'failed', 'error': str(e)}

    def get_execution_summary(self) -> Dict:
        """获取执行摘要"""
        if not self.pipeline_result:
            logger.error("⚠️ 管道未执行")
            return {}

        return self.pipeline_result
