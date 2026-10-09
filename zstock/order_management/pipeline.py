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
4. 对账：目标股数 vs 券商实际持仓
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
        allow_mock: bool = False,
        database_service=None,
    ):
        if order_generator is None:
            order_generator = OrderGenerator()

        if xtquant_executor is None:
            from .xtquant_executor import XtQuantExecutor

            xtquant_executor = XtQuantExecutor(allow_mock=allow_mock)

        if trade_settlement is None:
            from .trade_settlement import TradeSettlement

            trade_settlement = TradeSettlement()

        if execution_strategy is None:
            from .execution_strategy import ExecutionStrategy

            execution_strategy = ExecutionStrategy()

        self.allow_mock = allow_mock
        self._database_service = database_service
        self._order_generator = order_generator
        self._xtquant_executor = xtquant_executor
        self._trade_settlement = trade_settlement
        self._execution_strategy = execution_strategy
        self._pipeline_result: Dict = {}

        logger.info("OrderManagementPipeline 初始化完成")

    @staticmethod
    def _codes_from_frame(df: Optional[pd.DataFrame]) -> List[str]:
        if df is None or df.empty:
            return []
        for col in ("stock_code", "code"):
            if col not in df.columns:
                continue
            out: List[str] = []
            for v in df[col].tolist():
                if v is None or str(v) in ("", "nan", "None"):
                    continue
                out.append(str(v))
            return out
        return []

    async def execute_full_pipeline(
        self,
        target_positions: pd.DataFrame,
        current_positions: Optional[pd.DataFrame] = None,
        trade_date: Optional[str] = None,
        price_map: Optional[Dict[str, float]] = None,
        total_capital: Optional[float] = None,
        strategy: Optional[str] = None,
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
        if target_positions is None or target_positions.empty:
            return {"status": "no_target", "error": "目标持仓为空"}

        try:
            if getattr(self._xtquant_executor, "mock_mode", False) and not self.allow_mock:
                return {
                    "status": "failed",
                    "error": "当前为 Mock 客户端，拒绝下单。纸面测试请 OrderManagementPipeline(allow_mock=True)",
                }

            account_info = self._xtquant_executor.get_account_info() or {}
            if total_capital is None:
                raw_capital = account_info.get("total_value") or account_info.get("cash")
                try:
                    total_capital = float(raw_capital) if raw_capital is not None else 0.0
                except (TypeError, ValueError):
                    total_capital = 0.0
                if total_capital <= 0:
                    return {
                        "status": "failed",
                        "error": "无法获取有效账户资金，拒绝用默认资金下单",
                    }

            broker_positions = self._xtquant_executor.get_positions()
            if broker_positions is None:
                return {
                    "status": "failed",
                    "error": "无法获取券商持仓，拒绝当成空仓下单",
                }
            if current_positions is None or current_positions.empty:
                if current_positions is not None:
                    logger.warning("传入的 current_positions 为空，改用券商持仓，避免当成空仓全买")
                current_positions = pd.DataFrame(
                    [{"code": p["code"], "volume": p["volume"]} for p in broker_positions]
                )

            if price_map is None:
                codes = self._codes_from_frame(target_positions)
                codes.extend(self._codes_from_frame(current_positions))
                price_map = self._xtquant_executor.get_price_map(list(dict.fromkeys(codes)))

            self._trade_settlement.sync_positions_from_broker(broker_positions)

            logger.info("\n" + "=" * 60)
            logger.info("第一步：📋 订单生成")
            logger.info("=" * 60)

            orders = self._order_generator.generate_orders(
                target_positions=target_positions,
                current_positions=current_positions,
                trade_date=trade_date,
                price_map=price_map,
                total_capital=total_capital,
            )
            target_lots = self._order_generator.lots_from_positions(
                target_positions,
                price_map=price_map,
                total_capital=total_capital,
            )

            if not orders:
                logger.warning("⚠️ 未生成任何订单（可能已对齐目标）")
                reconcile = self._trade_settlement.reconcile_target_vs_broker(
                    target_lots,
                    broker_positions,
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
            await self._order_generator.persist_orders(orders, self._database_service)

            logger.info("\n" + "=" * 60)
            logger.info("第二步：🎯 策略执行")
            logger.info("=" * 60)

            current_strategy = strategy or self._execution_strategy.get_current_strategy()
            logger.info(f"   当前策略: {current_strategy}")
            if current_strategy == "closed":
                return {
                    "status": "failed",
                    "error": "市场已关闭或午休，拒绝提交订单",
                    "statistics": {
                        "target_positions": len(target_positions),
                        "orders_generated": len(orders),
                        "orders_submitted": 0,
                    },
                }

            exec_report = await self._execution_strategy.execute_with_strategy(
                orders=orders,
                executor=self._xtquant_executor,
                strategy=current_strategy,
            )
            submitted_count = int(exec_report.get("submitted", 0))
            deferred_count = int(exec_report.get("deferred", 0))
            failed_count = int(exec_report.get("failed", 0))
            results["submitted_count"] = submitted_count
            results["deferred_count"] = deferred_count
            results["failed_count"] = failed_count
            results["execution_report"] = exec_report
            results["submitted_orders"] = await self._xtquant_executor.query_all_orders()

            logger.info("\n" + "=" * 60)
            logger.info("第三步：📊 账户查询")
            logger.info("=" * 60)

            account_info = self._xtquant_executor.get_account_info()
            if account_info:
                logger.info(f"   现金: {account_info['cash']:.0f}")
                logger.info(f"   总资产: {account_info['total_value']:.0f}")
            else:
                logger.warning("   ⚠️ 账户信息不可用（QMT 未连接）")

            results["account_info"] = account_info

            logger.info("\n" + "=" * 60)
            logger.info("第四步：💼 持仓查询")
            logger.info("=" * 60)

            positions = self._xtquant_executor.get_positions()
            if positions is None:
                logger.error("提交后无法刷新券商持仓")
                positions = []
            logger.info(f"   当前持仓: {len(positions)} 只股票")
            results["current_positions"] = positions

            logger.info("\n" + "=" * 60)
            logger.info("第五步：🔍 目标 vs 券商对账")
            logger.info("=" * 60)

            reconcile_result = self._trade_settlement.reconcile_target_vs_broker(
                target_lots,
                positions,
            )
            results["reconciliation"] = reconcile_result

            cash = float((account_info or {}).get("cash", 0.0) or 0.0)
            total_value = float((account_info or {}).get("total_value", 0.0) or 0.0)
            if failed_count > 0:
                status = "failed"
                error = (
                    f"有 {failed_count} 笔订单提交失败（策略={current_strategy}，"
                    f"提交 {submitted_count}，推迟 {deferred_count}）"
                )
            elif deferred_count > 0:
                status = "partial"
                error = (
                    f"本时段推迟 {deferred_count} 笔订单未提交（策略={current_strategy}，"
                    f"已提交 {submitted_count}），需连续竞价或尾盘再跑"
                )
            elif submitted_count > 0 or len(orders) == 0:
                status = "success"
                error = None
            else:
                status = "failed"
                error = f"已生成 {len(orders)} 笔订单但提交 0 笔（策略={current_strategy}）"

            summary = {
                "status": status,
                "statistics": {
                    "target_positions": len(target_positions),
                    "orders_generated": len(orders),
                    "orders_submitted": submitted_count,
                    "orders_deferred": deferred_count,
                    "orders_failed": failed_count,
                    "positions_count": len(positions),
                    "cash_available": cash,
                    "total_value": total_value,
                    "reconciliation_status": reconcile_result["status"],
                },
                "results": results,
            }
            if error:
                summary["error"] = error

            logger.info("\n" + "=" * 60)
            logger.info("执行管道结束 status=%s", status)
            logger.info("=" * 60)
            logger.info(
                f"   目标 {summary['statistics']['target_positions']} | "
                f"订单 {summary['statistics']['orders_generated']} | "
                f"提交 {summary['statistics']['orders_submitted']} | "
                f"对账 {summary['statistics']['reconciliation_status']}"
            )

            self._pipeline_result = summary
            return summary

        except Exception as e:
            logger.error(f"❌ 管道执行失败: {e}", exc_info=True)
            return {"status": "failed", "error": str(e)}
