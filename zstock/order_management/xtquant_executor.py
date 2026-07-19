"""
XtQuant 执行接口模块

通过注入 QMTUtil（真实）或 MockQMTUtil（模拟）执行订单操作。
所有账户查询/下单操作完全委托给 qmt_util，不含任何内联 mock 逻辑。
订单数据持久化到 MongoDB，通过 DatabaseService 读写。
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
from zstock.common.entity.order_entity import Order

logger = logging.getLogger(__name__)

_ORDERS_COLLECTION = "zstock_orders"


class XtQuantExecutor:
    """
    XtQuant 执行器

    通过注入 QMTUtil（真实）或 MockQMTUtil（模拟）统一执行交易操作。
    mock 与真实模式的区别完全由注入的 qmt_util 类型决定，本类不含任何内联 mock 逻辑。
    订单数据全部持久化到 MongoDB（collection: zstock_orders）。

    属性：
        qmt_util:    QMTUtil 或 MockQMTUtil 实例
        db_service:  DatabaseService 实例
        mock_mode:   True 表示当前使用模拟客户端
    """

    def __init__(self, qmt_util=None):
        """
        初始化 XtQuant 执行器

        Args:
            qmt_util:   已连接的 QMTUtil 或 MockQMTUtil 实例。
                        未传入时自动创建并连接 MockQMTUtil。
        """
        from zstock.data_management.database_service import DatabaseService
        db_service = DatabaseService()
        self.db_service = db_service

        if qmt_util is None:
            # 延迟导入：xtquant SDK 依赖运行时 sys.path 动态注入，顶层导入会导致 IDE 解析失败
            from app.utils.xtquant_mock_util import get_xtquant_mock_client
            logger.warning("⚠️ qmt_util 未传入，自动使用 MockQMTUtil")
            qmt_util = get_xtquant_mock_client()
            qmt_util.connect()

        self.qmt_util = qmt_util
        # 延迟导入 MockQMTUtil 用于 isinstance 检查
        try:
            from app.utils.xtquant_mock_util import MockQMTUtil
            self.mock_mode = isinstance(qmt_util, MockQMTUtil)
        except ImportError:
            self.mock_mode = False
        logger.info(f"✅ XtQuantExecutor 初始化完成 (Mock={self.mock_mode})")

    # ------------------------------------------------------------------ #
    # 订单提交                                                              #
    # ------------------------------------------------------------------ #

    async def submit_order(self, order: Order) -> Optional[int]:
        """
        提交订单到券商，并将订单记录写入 MongoDB。

        Args:
            order: Order 对象，需包含 order_id / stock_code / direction / volume / price

        Returns:
            int: XtQuant 订单ID（失败返回 None）
        """
        try:
            logger.info(
                f"📤 提交订单: {order.order_id} {order.stock_code} "
                f"{order.direction} x{order.volume}"
            )

            if order.direction == 'buy':
                xt_order_id = self._buy(order)
            elif order.direction == 'sell':
                xt_order_id = self.qmt_util.sell(
                    code=order.stock_code,
                    volume=order.volume,
                    price=order.price,
                    remark=f"Order:{order.order_id}",
                )
            else:
                logger.error(f"❌ 未知的交易方向: {order.direction}")
                return None

            if xt_order_id:
                await self.db_service.insert_one(_ORDERS_COLLECTION, {
                    'order_id':     order.order_id,
                    'xt_order_id':  xt_order_id,
                    'stock_code':   order.stock_code,
                    'direction':    order.direction,
                    'volume':       order.volume,
                    'price':        order.price,
                    'status':       'pending',
                    'submitted_at': datetime.utcnow().isoformat(),
                    'updated_at':   datetime.utcnow().isoformat(),
                })
                logger.info(f"✅ 订单提交成功: xt_order_id={xt_order_id}")
                return xt_order_id

            logger.error(f"❌ 订单提交失败: {order.order_id}")
            return None

        except Exception as e:
            logger.error(f"❌ 提交订单异常: {e}")
            return None

    def _buy(self, order: Order) -> Optional[int]:
        """
        内部买入辅助：将 volume(股) 转换为 amount(元) 后调用 qmt_util.buy()。

        qmt_util.buy() 接受 amount(元)，内部再按当前价折算手数。
        price=None 时先通过 get_realtime_quote 获取当前价以计算 amount，
        最终仍将 price=None 传入 buy()，由 qmt_util 以市价下单。
        """
        price = order.price

        if price is None:
            try:
                quote = self.qmt_util.get_realtime_quote([order.stock_code]) or {}
                tick = quote.get(order.stock_code, {})
                price = float(tick.get('lastPrice', 0.0) or 0.0)
            except Exception as e:
                logger.error(f"❌ 获取 {order.stock_code} 实时行情失败: {e}")

        if not price or price <= 0:
            logger.error(f"❌ {order.stock_code} 无法取得有效价格，跳过买入")
            return None

        estimated_amount = order.volume * price
        return self.qmt_util.buy(
            code=order.stock_code,
            amount=estimated_amount,
            price=order.price,
            remark=f"Order:{order.order_id}",
        )

    # ------------------------------------------------------------------ #
    # 订单查询 / 撤单                                                       #
    # ------------------------------------------------------------------ #

    async def query_order(self, order_id: str) -> Optional[Dict]:
        """从 MongoDB 查询单条订单"""
        doc = await self.db_service.query_one(
            _ORDERS_COLLECTION, {'order_id': order_id}
        )
        if not doc:
            logger.error(f"❌ 订单不存在: {order_id}")
        return doc

    async def query_all_orders(self, status: Optional[str] = None) -> List[Dict]:
        """
        从 MongoDB 查询订单列表。

        Args:
            status: 按状态筛选（pending / filled / cancelled），None 表示不筛选
        """
        query = {'status': status} if status else {}
        return await self.db_service.query(_ORDERS_COLLECTION, query)

    async def cancel_order(self, order_id: str) -> bool:
        """
        撤单：向券商发送撤单请求，并将 MongoDB 中订单状态更新为 cancelled。
        """
        logger.info(f"🚫 撤单: {order_id}")

        doc = await self.db_service.query_one(
            _ORDERS_COLLECTION, {'order_id': order_id}
        )
        if not doc:
            logger.error(f"❌ 订单不存在: {order_id}")
            return False

        xt_order_id = doc.get('xt_order_id')
        if xt_order_id:
            self.qmt_util.cancel_order(xt_order_id)

        await self.db_service.update_one(
            _ORDERS_COLLECTION,
            {'order_id': order_id},
            {'status': 'cancelled', 'updated_at': datetime.utcnow().isoformat()},
        )
        logger.info(f"✅ 订单已撤单: {order_id}")
        return True

    # ------------------------------------------------------------------ #
    # 账户 / 持仓查询                                                       #
    # ------------------------------------------------------------------ #

    def get_account_info(self) -> Optional[Dict]:
        """
        获取账户信息。

        Returns:
            {'cash': float, 'total_value': float, 'frozen_cash': float}
        """
        try:
            info = self.qmt_util.get_account_info()
            return {
                'cash':        info.cash,
                'total_value': info.total_value,
                'frozen_cash': info.frozen_cash,
            }
        except Exception as e:
            logger.error(f"❌ 获取账户信息失败: {e}")
            return None

    def get_positions(self) -> List[Dict]:
        """
        获取当前持仓。

        Returns:
            [{'code', 'volume', 'cost_price', 'current_price'}, ...]
        """
        try:
            positions = self.qmt_util.get_positions()
            return [
                {
                    'code':          p.code,
                    'volume':        p.volume,
                    'cost_price':    p.cost_price,
                    'current_price': p.current_price,
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"❌ 获取持仓失败: {e}")
            return []

    # ------------------------------------------------------------------ #
    # 连接状态                                                              #
    # ------------------------------------------------------------------ #

    def is_connected(self) -> bool:
        """检查 qmt_util 是否已连接"""
        return bool(getattr(self.qmt_util, '_connected', False))
