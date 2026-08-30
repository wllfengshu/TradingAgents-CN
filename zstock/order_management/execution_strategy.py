"""
执行策略模块

实现不同的订单执行策略：
1. 开盘集合竞价：09:15-09:20 可申报可撤单，09:20-09:25 可申报不可撤单；
                 09:25-09:30 为静默期（不受理任何委托）。集合竞价必须限价。
2. 连续竞价 TWAP：09:30-11:30 / 13:00-14:57 分时段分批买入
3. 收盘集合竞价：14:57-15:00（沪深主板/科创板/创业板/北交所均从 14:57 开始），
                 必须限价，禁止市价单。
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Callable
from datetime import datetime, time as time_obj

try:
    from zoneinfo import ZoneInfo
    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # 老 Python / 缺 tzdata：允许退化为 naive local time
    _CN_TZ = None

logger = logging.getLogger(__name__)


class ExecutionStrategy:
    """
    执行策略管理器

    职责：
    - 根据时间段选择执行策略
    - 管理不同策略的执行参数
    - 记录策略执行历史

    时段划分（严格按上交所/深交所规则）：
      09:15-09:20  开盘集合竞价，可申报可撤单（auction_sell / limit only）
      09:20-09:25  开盘集合竞价，可申报不可撤单（auction_sell / limit only）
      09:25-09:30  静默期，交易所不受理任何委托 → strategy='closed'
      09:30-11:30  上午连续竞价 TWAP
      11:30-13:00  午间休市
      13:00-14:57  下午连续竞价 TWAP
      14:57-15:00  收盘集合竞价，limit only（沪深主板/创业板/科创板/北交所自
                    2018-08-20 起统一为 14:57 起 3 分钟集合竞价）
    """

    # 时间常量（严格按交易所规则拆分，禁止把 14:55 当作收盘集合竞价起点）
    MARKET_OPEN_AUCTION_START = time_obj(9, 15)   # 开盘集合竞价开始
    MARKET_OPEN_AUCTION_CANCELABLE_END = time_obj(9, 20)  # 09:20 起集合竞价单不可撤
    MARKET_OPEN_AUCTION_END = time_obj(9, 25)     # 集合竞价申报截止（09:25-09:30 静默）
    MARKET_OPEN = time_obj(9, 30)                 # 连续竞价开始
    LUNCH_START = time_obj(11, 30)
    LUNCH_END = time_obj(13, 0)
    MARKET_CLOSE_AUCTION_START = time_obj(14, 57)  # 收盘集合竞价：严格 14:57 开始
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

    def _now_local(self) -> time_obj:
        """返回 Asia/Shanghai 本地时间（time 部分），避免部署到 UTC 主机时段错乱。"""
        if _CN_TZ is not None:
            return datetime.now(_CN_TZ).time()
        return datetime.now().time()

    def get_current_strategy(self) -> str:
        """
        获取当前应该使用的策略

        Returns:
            str: 策略名称
                'auction_sell': 开盘集合竞价卖出（09:15-09:25，仅限价）
                'twap_buy':     连续竞价 TWAP（09:30-11:30 / 13:00-14:57）
                'final':        收盘集合竞价（14:57-15:00，仅限价）
                'closed':       非交易时段（含 09:25-09:30 静默期与午休）
        """
        now = self._now_local()

        # 09:15-09:25 开盘集合竞价（09:20 后不可撤单，此策略无撤单动作，
        # 因此可将 09:15-09:25 视为一个整体处理）
        if self.MARKET_OPEN_AUCTION_START <= now < self.MARKET_OPEN_AUCTION_END:
            return 'auction_sell'
        # 09:25-09:30 静默期：交易所不受理委托，任何下单请求都会被券商拒绝
        if self.MARKET_OPEN_AUCTION_END <= now < self.MARKET_OPEN:
            return 'closed'
        if self.LUNCH_START <= now < self.LUNCH_END:
            return 'closed'
        # 连续竞价：09:30-11:30 与 13:00-14:57
        if self.MARKET_OPEN <= now < self.MARKET_CLOSE_AUCTION_START:
            return 'twap_buy'
        # 收盘集合竞价：14:57-15:00
        if self.MARKET_CLOSE_AUCTION_START <= now <= self.MARKET_CLOSE_AUCTION_END:
            return 'final'
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
        """开盘集合竞价：只提交卖单；买单记为 deferred。

        集合竞价必须使用限价委托——市价单在集合竞价段会被交易所拒收。
        因此这里对卖单强制 price_type='limit'，price 缺省时保留原报价，
        由 executor 侧兜底为对手价/上一收盘价。
        """
        logger.info("集合竞价卖出策略（限价单，禁市价）")

        sell_orders = [o for o in orders if o.direction == 'sell']
        buy_orders = [o for o in orders if o.direction == 'buy']
        report = {"submitted": 0, "deferred": len(buy_orders), "failed": 0}
        if buy_orders:
            # 买单在集合竞价段推迟到连续竞价/尾盘，属正常业务分支
            logger.info(
                "集合竞价时段只提交卖单，%d 笔买单推迟到连续竞价/尾盘",
                len(buy_orders),
            )

        for order in sell_orders:
            # 集合竞价强制限价
            if getattr(order, 'price_type', 'limit') != 'limit':
                order.price_type = 'limit'
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
        """收盘集合竞价（14:57-15:00）：提交剩余买卖单，强制限价。

        - 收盘集合竞价必须使用限价单，市价单会被交易所拒收
        - 买单必须 100 股整手
        """
        logger.info("收盘集合竞价补单策略（限价单，禁市价）")
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
            # 收盘集合竞价强制限价（无论买卖）
            if getattr(order, 'price_type', 'limit') != 'limit':
                order.price_type = 'limit'
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
