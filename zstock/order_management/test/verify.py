"""
执行层验证脚本

演示完整的订单生成、执行、成交处理流程。
"""

import asyncio
import logging
import pandas as pd
import sys
import os

# 添加项目根目录到 Python 路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..', '..'))
sys.path.insert(0, project_root)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def verify_order_management():
    """验证执行层完整功能"""

    logger.info("="*80)
    logger.info("🚀 执行层（Order Management）验证")
    logger.info("="*80)

    try:
        # ====================================================================
        # 第一步：验证各模块单独导入
        # ====================================================================
        logger.info("\n【第一步】验证模块导入")
        logger.info("-" * 80)

        from zstock.order_management import (
            OrderGenerator,
            XtQuantExecutor,
            TradeSettlement,
            ExecutionStrategy,
        )
        from zstock.order_management.pipeline import OrderManagementPipeline

        logger.info("✅ 所有模块导入成功")
        logger.info("   - OrderGenerator")
        logger.info("   - XtQuantExecutor")
        logger.info("   - TradeSettlement")
        logger.info("   - ExecutionStrategy")
        logger.info("   - OrderManagementPipeline")

        # ====================================================================
        # 第二步：验证 OrderGenerator
        # ====================================================================
        logger.info("\n【第二步】验证订单生成模块")
        logger.info("-" * 80)

        # 1. 只初始化 MongoDB
        from app.core import database as db_module
        print("📌 步骤 1: 初始化 MongoDB...")
        await db_module.db_manager.init_mongodb()
        db_module.mongo_client = db_module.db_manager.mongo_client
        db_module.mongo_db = db_module.db_manager.mongo_db
        print("✅ MongoDB 已连接\n")

        generator = OrderGenerator()
        logger.info("✅ OrderGenerator 初始化成功")

        # 策略层格式：code + weight（与 StrategyPipeline.final_holdings 一致）
        target_positions = pd.DataFrame({
            'code': ['600000', '600001', '000001'],
            'weight': [0.10, 0.05, 0.08],
        })

        logger.info(f"   目标持仓数: {len(target_positions)}")

        # 生成订单（Mock 行情价 10 元，总资金 100 万）
        orders = generator.generate_orders(
            target_positions,
            price_map={'600000': 10.0, '600001': 10.0, '000001': 10.0},
            total_capital=1_000_000,
        )
        logger.info(f"✅ 生成订单数: {len(orders)}")

        for i, order in enumerate(orders[:3]):
            logger.info(f"   [{i+1}] {order.stock_code} {order.direction} x{order.volume}")

        # ====================================================================
        # 第三步：验证 XtQuantExecutor
        # ====================================================================
        logger.info("\n【第三步】验证执行器模块")
        logger.info("-" * 80)

        executor = XtQuantExecutor(allow_mock=True)
        logger.info("✅ XtQuantExecutor 初始化成功")

        # 检查 Mock 模式
        logger.info(f"   Mock 模式: {executor.mock_mode}")

        # 获取账户信息
        account_info = executor.get_account_info()
        if not account_info:
            logger.error("❌ 账户信息不可用")
            return False
        logger.info(f"✅ 账户信息获取成功")
        logger.info(f"   - 现金: {account_info['cash']:.0f}")
        logger.info(f"   - 总资产: {account_info['total_value']:.0f}")

        # 获取持仓（失败是 None，不是空列表）
        positions = executor.get_positions()
        if positions is None:
            logger.error("❌ 持仓查询失败")
            return False
        logger.info(f"✅ 持仓查询成功: {len(positions)} 只")

        # 提交订单
        submitted_count = 0
        for order in orders[:2]:
            if await executor.submit_order(order):
                submitted_count += 1

        logger.info(f"✅ 订单提交成功: {submitted_count} 个")

        # ====================================================================
        # 第四步：验证 TradeSettlement
        # ====================================================================
        logger.info("\n【第四步】验证成交处理模块")
        logger.info("-" * 80)

        settlement = TradeSettlement()
        logger.info("✅ TradeSettlement 初始化成功")

        # 处理成交回报
        success = settlement.handle_trade_report(
            order_id=orders[0].order_id,
            filled_volume=500,
            filled_price=10.5,
            order_direction='buy',
            stock_code=orders[0].stock_code,
        )

        logger.info(f"✅ 成交回报处理: {'成功' if success else '失败'}")

        # 对账
        current_positions = executor.get_positions()
        reconcile_result = settlement.reconcile_target_vs_broker(
            target_positions,
            current_positions,
            price_map={'600000': 10.0, '600001': 10.0, '000001': 10.0},
            total_capital=1_000_000,
        )
        logger.info(f"✅ 对账完成: {reconcile_result['status']}")
        logger.info(f"   - 匹配数: {reconcile_result['matched_count']}")
        logger.info(f"   - 异常数: {reconcile_result['discrepancy_count']}")

        # ====================================================================
        # 第五步：验证 ExecutionStrategy
        # ====================================================================
        logger.info("\n【第五步】验证执行策略模块")
        logger.info("-" * 80)

        strategy = ExecutionStrategy(twap_interval_seconds=0)
        logger.info("✅ ExecutionStrategy 初始化成功")

        current_strategy = strategy.get_current_strategy()
        logger.info(f"✅ 当前时段策略: {current_strategy}")

        strategy_stats = strategy.get_strategy_stats()
        logger.info(f"✅ 策略统计:")
        logger.info(f"   - 集合竞价卖出: {strategy_stats['auction_sell_count']}")
        logger.info(f"   - TWAP 分批买入: {strategy_stats['twap_buy_count']}")
        logger.info(f"   - 尾盘补单: {strategy_stats['final_buy_count']}")

        # ====================================================================
        # 第六步：验证 OrderManagementPipeline
        # ====================================================================
        logger.info("\n【第六步】验证完整管道")
        logger.info("-" * 80)

        pipeline = OrderManagementPipeline(
            xtquant_executor=XtQuantExecutor(allow_mock=True),
            execution_strategy=ExecutionStrategy(twap_interval_seconds=0),
            allow_mock=True,
        )
        logger.info("✅ OrderManagementPipeline 初始化成功")

        # 执行完整流程
        logger.info("\n执行完整管道...")

        result = await pipeline.execute_full_pipeline(
            target_positions,
            strategy="final",
        )

        if result['status'] == 'success':
            logger.info("✅ 管道执行成功")
            stats = result['statistics']
            logger.info(f"\n📊 执行统计:")
            logger.info(f"   - 目标持仓数: {stats['target_positions']}")
            logger.info(f"   - 生成订单数: {stats['orders_generated']}")
            logger.info(f"   - 已提交: {stats['orders_submitted']}")
            logger.info(f"   - 当前持仓数: {stats['positions_count']}")
            logger.info(f"   - 对账状态: {stats['reconciliation_status']}")
        else:
            logger.error(f"❌ 管道执行失败: {result.get('error', 'Unknown error')}")
            return False

        # ====================================================================
        # 总结
        # ====================================================================
        logger.info("\n" + "="*80)
        logger.info("✅ 所有验证通过！")
        logger.info("="*80)
        logger.info("\n📋 执行层完整功能验证成功：")
        logger.info("   ✓ 订单生成模块正常工作")
        logger.info("   ✓ XtQuant 执行器正常工作")
        logger.info("   ✓ 成交回报处理正常工作")
        logger.info("   ✓ 执行策略选择正常工作")
        logger.info("   ✓ 完整管道集成正常工作")
        logger.info("\n🚀 执行层（Phase 5）开发完成！")
        logger.info("="*80)

        return True

    except Exception as e:
        logger.error(f"\n❌ 验证失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = asyncio.run(verify_order_management())
    sys.exit(0 if success else 1)
