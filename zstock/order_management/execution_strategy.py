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
from typing import Dict, List, Optional, Callable
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
    MARKET_OPEN_AUCTION_START = time_obj(9, 15)
    MARKET_OPEN_AUCTION_END = time_obj(9, 30)
    MARKET_OPEN = time_obj(9, 30)
    LUNCH_START = time_obj(11, 30)
    LUNCH_END = time_obj(13, 0)
    MARKET_CLOSE_AUCTION_START = time_obj(14, 55)
    MARKET_CLOSE_AUCTION_END = time_obj(15, 0)
    MARKET_CLOSE = time_obj(15, 0)
    A_SHARE_LOT = 100

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
        elif self.LUNCH_START <= now < self.LUNCH_END:
            return 'closed'
        elif self.MARKET_OPEN <= now < self.MARKET_CLOSE_AUCTION_START:
            return 'twap_buy'
        elif self.MARKET_CLOSE_AUCTION_START <= now <= self.MARKET_CLOSE_AUCTION_END:
            return 'final'
        else:
            return 'closed'

    async def execute_with_strategy(self, orders: List, executor: object,
                            strategy: Optional[str] = None) -> Dict:
        """
        根据策略执行订单。

        Returns:
            submitted: 成功提交次数（TWAP 按批次计）
            deferred: 本时段故意未提交（如集合竞价的买单）
            failed: 应提交但失败或被跳过的原始订单数
        """
        if not strategy:
            strategy = self.get_current_strategy()

        logger.info(f"使用策略执行: {strategy}")

        empty = {"submitted": 0, "deferred": 0, "failed": 0}

        if strategy == 'auction_sell':
            return await self._execute_auction_sell(orders, executor)
        if strategy == 'twap_buy':
            return await self._execute_twap_buy(orders, executor)
        if strategy == 'final':
            return await self._execute_final_orders(orders, executor)
        if strategy == 'closed':
            logger.error("市场已关闭，无法执行订单")
            empty["failed"] = len(orders)
            return empty

        logger.error(f"未知的执行策略: {strategy}")
        empty["failed"] = len(orders)
        return empty

    async def _execute_auction_sell(self, orders: List, executor: object) -> Dict:
        """集合竞价只提交卖单；买单记为 deferred，不记成功。"""
        logger.info("集合竞价卖出策略")

        sell_orders = [o for o in orders if o.direction == 'sell']
        buy_orders = [o for o in orders if o.direction == 'buy']
        report = {"submitted": 0, "deferred": len(buy_orders), "failed": 0}
        if buy_orders:
            logger.error(
                "集合竞价时段只提交卖单，%d 笔买单推迟到连续竞价/尾盘",
                len(buy_orders),
            )

        for order in sell_orders:
            if await executor.submit_order(order):
                report["submitted"] += 1
                self.execution_stats['auction_sell_count'] += 1
            else:
                report["failed"] += 1

        logger.info(
            "集合竞价卖出: 提交 %s / 失败 %s / 推迟买单 %s",
            report["submitted"],
            report["failed"],
            report["deferred"],
        )
        return report

    async def _execute_twap_buy(self, orders: List, executor: object) -> Dict:
        """连续竞价：先提交全部卖单，再按 100 股整手 TWAP 买单。"""
        logger.info("TWAP 连续竞价（先卖后买）")

        sell_orders = [o for o in orders if o.direction == "sell"]
        buy_orders = [o for o in orders if o.direction == "buy"]
        report = {"submitted": 0, "deferred": 0, "failed": 0}

        for order in sell_orders:
            if await executor.submit_order(order):
                report["submitted"] += 1
                self.execution_stats["auction_sell_count"] += 1
            else:
                report["failed"] += 1

        if sell_orders:
            logger.info("已提交卖单 %s，失败 %s", report["submitted"], report["failed"])

        if not buy_orders:
            logger.info("无买入订单")
            return report

        TWAP_SLICES = self.twap_slices
        TWAP_INTERVAL = self.twap_interval_seconds

        for order in buy_orders:
            batches = _lot_batches(order.volume, TWAP_SLICES, self.A_SHARE_LOT)
            if not batches:
                logger.error(
                    "%s 买单量 %s 无法按 100 股整手下单，跳过",
                    order.stock_code,
                    order.volume,
                )
                report["failed"] += 1
                continue

            all_ok = True
            for batch_idx, batch_qty in enumerate(batches):
                batch_order = self._create_batch_order(order, batch_idx, batch_qty)
                if await executor.submit_order(batch_order):
                    report["submitted"] += 1
                    self.execution_stats["twap_buy_count"] += 1
                else:
                    all_ok = False

                if batch_idx < len(batches) - 1 and TWAP_INTERVAL > 0:
                    await asyncio.sleep(TWAP_INTERVAL)

            if not all_ok:
                report["failed"] += 1

        logger.info(
            "TWAP 结束: 提交 %s / 失败 %s",
            report["submitted"],
            report["failed"],
        )
        return report

    async def _execute_final_orders(self, orders: List, executor: object) -> Dict:
        """尾盘提交剩余买卖单。买单必须是 100 股整手。"""
        logger.info("尾盘补单策略")
        report = {"submitted": 0, "deferred": 0, "failed": 0}

        for order in orders:
            if order.direction == "buy":
                if order.volume < self.A_SHARE_LOT or order.volume % self.A_SHARE_LOT != 0:
                    logger.error(
                        "%s 尾盘买单量 %s 非整手，跳过",
                        order.stock_code,
                        order.volume,
                    )
                    report["failed"] += 1
                    continue
            if await executor.submit_order(order):
                report["submitted"] += 1
                self.execution_stats['final_buy_count'] += 1
            else:
                report["failed"] += 1

        logger.info(
            "尾盘补单: 提交 %s / 失败 %s",
            report["submitted"],
            report["failed"],
        )
        return report

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


def _lot_batches(volume: int, slices: int, lot: int = 100) -> List[int]:
    """把股数切成不超过 slices 批，每批都是 lot 的整数倍。

    买单不足一手则返回空列表（由调用方跳过），避免拆出 20/60 这种废单。
    """
    if volume <= 0 or slices <= 0 or lot <= 0:
        return []
    if volume < lot or volume % lot != 0:
        return []
    n = min(slices, volume // lot)
    chunk = (volume // n) // lot * lot
    if chunk < lot:
        return [volume]
    batches = [chunk] * n
    batches[-1] += volume - chunk * n
    return [b for b in batches if b > 0]
