"""
执行层完整管道

协调订单生成、执行、成交回报处理等多个模块。

典型串联（无需额外桥接服务）::

    strategy_summary = await StrategyPipeline().execute_full_pipeline(...)
    target = strategy_summary['results']['final_holdings']  # code + weight
    exec_summary = await OrderManagementPipeline().execute_full_pipeline(
        target_positions=target,
    )

工作流：
1. 订单生成：目标持仓 → 订单列表（自动补行情/持仓/资金）
2. 策略执行：根据时间选择执行策略
3. 订单提交：通过 XtQuant 提交订单
4. 对账：目标持仓 vs 券商实际持仓
"""

import logging
from typing import Dict, List, Optional

import pandas as pd

from .order_generator import OrderGenerator

logger = logging.getLogger(__name__)


class OrderManagementPipeline:
    """执行层完整管道：直接接收策略层 final_holdings。"""

    def __init__(
        self,
        order_generator=None,
        xtquant_executor=None,
        trade_settlement=None,
        execution_strategy=None,
    ):
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
        self.pipeline_result: Dict = {}

        logger.info("✅ OrderManagementPipeline 初始化完成")

    async def execute_full_pipeline(
        self,
        target_positions: pd.DataFrame,
        current_positions: Optional[pd.DataFrame] = None,
        trade_date: Optional[str] = None,
        price_map: Optional[Dict[str, float]] = None,
        total_capital: Optional[float] = None,
    ) -> Dict:
        """
        执行完整管道。

        target_positions 可直接传入 StrategyPipeline 的 final_holdings（code + weight）。
        price_map / current_positions / total_capital 缺省时从 QMT 自动补齐。
        """
        logger.info("=" * 60)
        logger.info("🚀 启动执行层完整管道")
        logger.info("=" * 60)

        results: Dict = {}
        target_positions = OrderGenerator.normalize_positions_df(target_positions)

        if target_positions.empty:
            return {"status": "no_target", "error": "目标持仓为空"}

        try:
            account_info = self.xtquant_executor.get_account_info() or {}
            if total_capital is None:
                total_capital = float(
                    account_info.get("total_value")
                    or account_info.get("cash")
                    or 1e7
                )

            broker_positions = self.xtquant_executor.get_positions()
            if current_positions is None:
                current_positions = pd.DataFrame(
                    [{"stock_code": p["code"], "volume": p["volume"]} for p in broker_positions]
                )
            else:
                current_positions = OrderGenerator.normalize_positions_df(current_positions)

            if price_map is None:
                codes = target_positions["stock_code"].astype(str).tolist()
                price_map = self.xtquant_executor.get_price_map(codes)

            self.trade_settlement.sync_positions_from_broker(broker_positions)

            logger.info("\n" + "=" * 60)
            logger.info("第一步：📋 订单生成")
            logger.info("=" * 60)

            orders = self.order_generator.generate_orders(
                target_positions=target_positions,
                current_positions=current_positions,
                trade_date=trade_date,
                price_map=price_map,
                total_capital=total_capital,
            )

            if not orders:
                logger.warning("⚠️ 未生成任何订单（可能已对齐目标）")
                reconcile = self.trade_settlement.reconcile_target_vs_broker(
                    target_positions,
                    broker_positions,
                    price_map=price_map,
                    total_capital=total_capital,
                )
                return {
                    "status": "no_orders",
                    "statistics": {
                        "target_positions": len(target_positions),
                        "orders_generated": 0,
                        "orders_submitted": 0,
                        "reconciliation_status": reconcile["status"],
                    },
                    "results": {"reconciliation": reconcile},
                }

            results["orders"] = orders
            await self.order_generator.save_orders_to_db(orders)

            logger.info("\n" + "=" * 60)
            logger.info("第二步：🎯 策略执行")
            logger.info("=" * 60)

            current_strategy = self.execution_strategy.get_current_strategy()
            logger.info(f"   当前策略: {current_strategy}")

            submitted_count = await self.execution_strategy.execute_with_strategy(
                orders=orders,
                executor=self.xtquant_executor,
                strategy=None,
            )

            results["submitted_count"] = submitted_count
            results["submitted_orders"] = await self.xtquant_executor.query_all_orders()

            logger.info("\n" + "=" * 60)
            logger.info("第三步：📊 账户查询")
            logger.info("=" * 60)

            account_info = self.xtquant_executor.get_account_info()
            if account_info:
                logger.info(f"   现金: {account_info['cash']:.0f}")
                logger.info(f"   总资产: {account_info['total_value']:.0f}")
            else:
                logger.warning("   ⚠️ 账户信息不可用（QMT 未连接）")

            results["account_info"] = account_info

            logger.info("\n" + "=" * 60)
            logger.info("第四步：💼 持仓查询")
            logger.info("=" * 60)

            positions = self.xtquant_executor.get_positions()
            logger.info(f"   当前持仓: {len(positions)} 只股票")
            results["current_positions"] = positions

            logger.info("\n" + "=" * 60)
            logger.info("第五步：🔍 目标 vs 券商对账")
            logger.info("=" * 60)

            reconcile_result = self.trade_settlement.reconcile_target_vs_broker(
                target_positions,
                positions,
                price_map=price_map,
                total_capital=total_capital,
            )
            results["reconciliation"] = reconcile_result

            cash = float((account_info or {}).get("cash", 0.0))
            total_value = float((account_info or {}).get("total_value", 0.0))

            summary = {
                "status": "success",
                "statistics": {
                    "target_positions": len(target_positions),
                    "orders_generated": len(orders),
                    "orders_submitted": submitted_count,
                    "positions_count": len(positions),
                    "cash_available": cash,
                    "total_value": total_value,
                    "reconciliation_status": reconcile_result["status"],
                },
                "results": results,
            }

            logger.info("\n" + "=" * 60)
            logger.info("✅ 执行管道完成")
            logger.info("=" * 60)
            logger.info(
                f"   目标 {summary['statistics']['target_positions']} | "
                f"订单 {summary['statistics']['orders_generated']} | "
                f"提交 {summary['statistics']['orders_submitted']} | "
                f"对账 {summary['statistics']['reconciliation_status']}"
            )

            self.pipeline_result = summary
            return summary

        except Exception as e:
            logger.error(f"❌ 管道执行失败: {e}", exc_info=True)
            return {"status": "failed", "error": str(e)}

    def get_execution_summary(self) -> Dict:
        if not self.pipeline_result:
            logger.error("⚠️ 管道未执行")
            return {}
        return self.pipeline_result
