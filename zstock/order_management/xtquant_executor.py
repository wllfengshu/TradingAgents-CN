"""
XtQuant 执行接口模块

通过注入 QMTUtil（真实）或 MockQMTUtil（模拟）执行订单操作。
订单数据持久化到 MongoDB，通过 DatabaseService 读写。
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime

from zstock.common.entity.order_entity import Order
from zstock.common.utils.common_utils import normalize_code
from zstock.common.utils.xtquant_data_utils import to_xt_code
from zstock.data_management.database_service import get_database_service

logger = logging.getLogger(__name__)

_ORDERS_COLLECTION = "zstock_orders"


class XtQuantExecutor:
    """XtQuant 执行器：提交/查询/撤单，代码统一转为 QMT 格式。"""

    def __init__(self, qmt_util=None, allow_mock: bool = False):
        from .qmt_client_factory import create_qmt_util, is_mock_client

        self._db_service = None
        self.allow_mock = allow_mock

        if qmt_util is None:
            qmt_util = create_qmt_util(prefer_real=True, allow_mock=allow_mock)

        self.qmt_util = qmt_util
        self.mock_mode = is_mock_client(qmt_util)
        if self.mock_mode:
            logger.warning("XtQuantExecutor 处于 Mock 模式（allow_mock=%s）", allow_mock)
        logger.info("XtQuantExecutor 初始化完成 (Mock=%s)", self.mock_mode)

    @property
    def db_service(self):
        if self._db_service is None:
            self._db_service = get_database_service()
        return self._db_service

    @staticmethod
    def to_broker_code(stock_code: str) -> str:
        """6 位或任意格式 → QMT 代码（如 600000.SH）。"""
        return to_xt_code(normalize_code(stock_code))

    async def submit_order(self, order: Order) -> Optional[int]:
        """提交订单到券商，并将订单记录写入 MongoDB。"""
        try:
            broker_code = self.to_broker_code(order.stock_code)
            logger.info(
                f"📤 提交订单: {order.order_id} {broker_code} "
                f"{order.direction} x{order.volume}"
            )

            if order.direction == "buy":
                xt_order_id = self._buy(order, broker_code)
            elif order.direction == "sell":
                xt_order_id = self.qmt_util.sell(
                    code=broker_code,
                    volume=order.volume,
                    price=order.price,
                    remark=f"Order:{order.order_id}",
                )
            else:
                logger.error(f"❌ 未知的交易方向: {order.direction}")
                return None

            if xt_order_id:
                await self.db_service.insert_one(
                    _ORDERS_COLLECTION,
                    {
                        "order_id": order.order_id,
                        "xt_order_id": xt_order_id,
                        "stock_code": normalize_code(order.stock_code),
                        "broker_code": broker_code,
                        "direction": order.direction,
                        "volume": order.volume,
                        "price": order.price,
                        "status": "pending",
                        "submitted_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                )
                logger.info(f"✅ 订单提交成功: xt_order_id={xt_order_id}")
                return xt_order_id

            logger.error(f"❌ 订单提交失败: {order.order_id}")
            return None

        except Exception as e:
            logger.error(f"❌ 提交订单异常: {e}")
            return None

    def _buy(self, order: Order, broker_code: str) -> Optional[int]:
        """按股数下单；qmt_util.buy 支持 volume 参数时优先使用。"""
        price = order.price
        if price is None:
            try:
                quote = self.qmt_util.get_realtime_quote([broker_code]) or {}
                tick = quote.get(broker_code, {})
                price = float(tick.get("lastPrice", 0.0) or 0.0)
            except Exception as e:
                logger.error(f"❌ 获取 {broker_code} 实时行情失败: {e}")

        if not price or price <= 0:
            logger.error(f"❌ {broker_code} 无法取得有效价格，跳过买入")
            return None

        buy_fn = self.qmt_util.buy
        if _accepts_volume(buy_fn):
            return buy_fn(
                code=broker_code,
                amount=order.volume * price,
                price=price,
                remark=f"Order:{order.order_id}",
                volume=order.volume,
            )

        return buy_fn(
            code=broker_code,
            amount=order.volume * price,
            price=price,
            remark=f"Order:{order.order_id}",
        )

    async def query_order(self, order_id: str) -> Optional[Dict]:
        doc = await self.db_service.query_one(
            _ORDERS_COLLECTION, {"order_id": order_id}
        )
        if not doc:
            logger.error(f"❌ 订单不存在: {order_id}")
        return doc

    async def query_all_orders(self, status: Optional[str] = None) -> List[Dict]:
        query = {"status": status} if status else {}
        return await self.db_service.query(_ORDERS_COLLECTION, query)

    async def cancel_order(self, order_id: str) -> bool:
        logger.info(f"🚫 撤单: {order_id}")

        doc = await self.db_service.query_one(
            _ORDERS_COLLECTION, {"order_id": order_id}
        )
        if not doc:
            logger.error(f"❌ 订单不存在: {order_id}")
            return False

        xt_order_id = doc.get("xt_order_id")
        if xt_order_id:
            self.qmt_util.cancel_order(xt_order_id)

        await self.db_service.update_one(
            _ORDERS_COLLECTION,
            {"order_id": order_id},
            {"status": "cancelled", "updated_at": datetime.utcnow().isoformat()},
        )
        logger.info(f"✅ 订单已撤单: {order_id}")
        return True

    def get_account_info(self) -> Optional[Dict]:
        try:
            info = self.qmt_util.get_account_info()
            return {
                "cash": info.cash,
                "total_value": info.total_value,
                "frozen_cash": info.frozen_cash,
            }
        except Exception as e:
            logger.error(f"❌ 获取账户信息失败: {e}")
            return None

    def get_positions(self) -> Optional[List[Dict]]:
        """返回 6 位 code 格式的持仓列表。查询失败返回 None（不是空列表）。"""
        try:
            positions = self.qmt_util.get_positions()
            if positions is None:
                logger.error("券商持仓查询返回 None")
                return None
            return [
                {
                    "code": normalize_code(p.code),
                    "broker_code": self.to_broker_code(p.code),
                    "volume": p.volume,
                    "cost_price": p.cost_price,
                    "current_price": p.current_price,
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"❌ 获取持仓失败: {e}")
            return None

    def get_price_map(self, codes: List[str]) -> Dict[str, float]:
        """批量获取最新价 {6位code: price}。"""
        if not codes:
            return {}
        broker_codes = [self.to_broker_code(c) for c in codes]
        try:
            quotes = self.qmt_util.get_realtime_quote(broker_codes) or {}
        except Exception as e:
            logger.error(f"❌ 批量行情失败: {e}")
            return {}

        out: Dict[str, float] = {}
        for pure, broker in zip(codes, broker_codes):
            tick = quotes.get(broker, {})
            px = float(tick.get("lastPrice", 0.0) or 0.0)
            if px > 0:
                out[normalize_code(pure)] = px
        return out

    def is_connected(self) -> bool:
        return bool(getattr(self.qmt_util, "_connected", False))


def _accepts_volume(fn) -> bool:
    import inspect

    try:
        return "volume" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
