"""
执行策略模块

实现不同的订单执行策略：
1. 集合竞价：开盘前 09:25-09:30 执行卖单
2. TWAP（时间加权平均价）：分时段分批买入
3. 尾盘补单：收盘前 14:30-15:00 执行剩余订单
"""

import asyncio
import logging
import time
from typing import List, Optional, Callable
from datetime import datetime, time as time_obj

logger = logging.getLogger(__name__)


class ExecutionStrategy:
    """
    执行策略管理器

    职责：
    - 根据时间段选择执行策略
    - 管理不同策略的执行参数
    - 记录策略执行历史
    """

    # 时间常量
    MARKET_OPEN_AUCTION_START = time_obj(9, 15)  # 集合竞价开始
    MARKET_OPEN_AUCTION_END = time_obj(9, 30)    # 集合竞价结束
    MARKET_OPEN = time_obj(9, 30)                # 正常交易开始
    MARKET_CLOSE_AUCTION_START = time_obj(14, 55)  # 尾盘集合竞价开始
    MARKET_CLOSE_AUCTION_END = time_obj(15, 0)   # 收盘
    MARKET_CLOSE = time_obj(15, 0)               # 正常交易结束

    def __init__(self, twap_slices: int = 5, twap_interval_seconds: int = 30):
        """初始化执行策略管理器

        Args:
            twap_slices:           TWAP 分批数，默认 5 批
            twap_interval_seconds: 每批间隔秒数，默认 30 秒（测试时传 0）
        """
        self.twap_slices = twap_slices
        self.twap_interval_seconds = twap_interval_seconds
        self.strategy_history = []
        self.execution_stats = {
            'auction_sell_count': 0,
            'twap_buy_count': 0,
            'final_buy_count': 0,
        }

        logger.info("✅ ExecutionStrategy 初始化完成")

    def get_current_strategy(self) -> str:
        """
        获取当前应该使用的策略

        Returns:
            str: 策略名称
                'auction_sell': 集合竞价卖出
                'twap_buy': TWAP 分批买入
                'normal': 正常交易
                'final': 尾盘补单
        """
        now = datetime.now().time()

        if self.MARKET_OPEN_AUCTION_START <= now < self.MARKET_OPEN_AUCTION_END:
            return 'auction_sell'
        elif self.MARKET_OPEN <= now < self.MARKET_CLOSE_AUCTION_START:
            return 'twap_buy'
        elif self.MARKET_CLOSE_AUCTION_START <= now <= self.MARKET_CLOSE_AUCTION_END:
            return 'final'
        else:
            return 'closed'

    async def execute_with_strategy(self, orders: List, executor: object,
                            strategy: Optional[str] = None) -> int:
        """
        根据策略执行订单

        Args:
            orders: 订单列表
            executor: XtQuantExecutor 实例
            strategy: 指定策略（如为 None 则自动选择）

        Returns:
            int: 成功提交的订单数
        """
        if not strategy:
            strategy = self.get_current_strategy()

        logger.info(f"🎯 使用策略执行: {strategy}")

        submitted_count = 0

        if strategy == 'auction_sell':
            submitted_count = await self._execute_auction_sell(orders, executor)
        elif strategy == 'twap_buy':
            submitted_count = await self._execute_twap_buy(orders, executor)
        elif strategy == 'final':
            submitted_count = await self._execute_final_orders(orders, executor)
        elif strategy == 'closed':
            logger.error("⚠️ 市场已关闭，无法执行订单")
        else:
            logger.error(f"❌ 未知的执行策略: {strategy}")

        return submitted_count

    async def _execute_auction_sell(self, orders: List, executor: object) -> int:
        """
        集合竞价卖出

        在开盘竞价时段（09:25-09:30）优先执行卖单。

        Args:
            orders: 订单列表
            executor: 执行器

        Returns:
            int: 成功提交的订单数
        """
        logger.info("📋 集合竞价卖出策略")

        # 筛选卖单
        sell_orders = [o for o in orders if o.direction == 'sell']

        submitted = 0
        for order in sell_orders:
            if await executor.submit_order(order):
                submitted += 1
                self.execution_stats['auction_sell_count'] += 1

        logger.info(f"✅ 集合竞价卖出: {submitted} 个订单")

        return submitted

    async def _execute_twap_buy(self, orders: List, executor: object) -> int:
        """
        TWAP 分批买入

        在正常交易时段分时段分批买入，避免大单冲击。

        算法：
        1. 将买单数量分成 N 批
        2. 在交易时段均匀分布执行

        Args:
            orders: 订单列表
            executor: 执行器

        Returns:
            int: 成功提交的订单数
        """
        logger.info("📋 TWAP 分批买入策略")

        # 筛选买单
        buy_orders = [o for o in orders if o.direction == 'buy']

        if not buy_orders:
            logger.info("   无买入订单")
            return 0

        # 参数配置
        TWAP_SLICES = self.twap_slices
        TWAP_INTERVAL = self.twap_interval_seconds

        submitted = 0

        for order in buy_orders:
            # 计算每批数量
            batch_volume = order.volume // TWAP_SLICES
            remaining_volume = order.volume % TWAP_SLICES

            logger.debug(f"   分批买入: {order.stock_code} "
                        f"总={order.volume}, 每批={batch_volume}, 余数={remaining_volume}")

            for batch_idx in range(TWAP_SLICES):
                batch_qty = batch_volume
                if batch_idx == TWAP_SLICES - 1:
                    batch_qty += remaining_volume

                # 创建分批订单
                batch_order = self._create_batch_order(
                    order, batch_idx, batch_qty
                )

                if await executor.submit_order(batch_order):
                    submitted += 1
                    self.execution_stats['twap_buy_count'] += 1

                # 延迟后执行下一批
                if batch_idx < TWAP_SLICES - 1:
                    logger.debug(f"      等待 {TWAP_INTERVAL} 秒...")
                    await asyncio.sleep(TWAP_INTERVAL)

        logger.info(f"✅ TWAP 分批买入: {submitted} 个订单")

        return submitted

    async def _execute_final_orders(self, orders: List, executor: object) -> int:
        """
        尾盘补单

        在收盘前最后 5 分钟（14:55-15:00）执行剩余的买入订单。

        Args:
            orders: 订单列表
            executor: 执行器

        Returns:
            int: 成功提交的订单数
        """
        logger.info("📋 尾盘补单策略")

        submitted = 0

        for order in orders:
            if await executor.submit_order(order):
                submitted += 1
                self.execution_stats['final_buy_count'] += 1

        logger.info(f"✅ 尾盘补单: {submitted} 个订单")

        return submitted

    def _create_batch_order(self, original_order, batch_idx: int, batch_volume: int):
        """
        创建分批订单

        Args:
            original_order: 原始订单
            batch_idx: 批次索引
            batch_volume: 批次数量

        Returns:
            Order: 新的分批订单
        """
        from zstock.common.entity.order_entity import Order

        batch_id = f"{original_order.order_id}_B{batch_idx}"

        batch_order = Order(
            order_id=batch_id,
            stock_code=original_order.stock_code,
            direction=original_order.direction,
            volume=batch_volume,
            price_type=original_order.price_type,
            price=original_order.price,
        )

        return batch_order

    def get_strategy_stats(self) -> dict:
        """获取策略执行统计"""
        return self.execution_stats.copy()

    def reset_stats(self) -> None:
        """重置统计信息"""
        for key in self.execution_stats:
            self.execution_stats[key] = 0

        logger.info("✅ 执行统计已重置")
