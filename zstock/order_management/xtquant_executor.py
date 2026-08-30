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

    # 允许推进为终态/流转态的状态图（防止 broker 回报把 filled 打回 pending）
    _TERMINAL_STATUSES = frozenset({"filled", "cancelled", "rejected"})

    async def submit_order(self, order: Order) -> Optional[int]:
        """提交订单到券商，并将订单记录写入 MongoDB。

        幂等性守卫：若 zstock_orders 中已有同 order_id 且状态是
        pending / partial_filled / filled / cancelled，视为已提交（或已终态），
        不重复调用 broker。此举防止 TWAP 分批因网络重试造成同一订单在
        broker 端出现多笔真金真银的重复委托。
        """
        try:
            # 幂等：查 DB
            existing = await self.db_service.query_one(
                _ORDERS_COLLECTION, {"order_id": order.order_id}
            )
            if existing:
                existing_status = existing.get("status")
                if existing_status in ("pending", "partial_filled", "filled",
                                       "cancelled", "rejected", "pending_cancel"):
                    logger.warning(
                        "⚠️ 订单 %s 已存在于 DB (status=%s)，跳过重发以保证幂等",
                        order.order_id, existing_status,
                    )
                    xt_id = existing.get("xt_order_id")
                    return int(xt_id) if xt_id else None

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

            # xt_order_id 语义：QMT 侧成功返回正整数；0 或 None / 负值视为失败。
            if xt_order_id is not None and int(xt_order_id) > 0:
                # 与 order_generator.save_orders_to_db 共享同一 zstock_orders 集合，
                # 按 order_id 幂等——这里必须用 upsert 而不是 insert_one：
                # 1) generator 侧可能已经把该 order_id 写为 status="created"，
                #    再 insert_one 会 DuplicateKey；
                # 2) TWAP 分批下 submit_order 会对同一 order_id 反复调用（网络重试
                #    等场景），insert_one 会造成同一订单在库里出现多条。
                now_iso = datetime.utcnow().isoformat()
                await self.db_service.update_one(
                    _ORDERS_COLLECTION,
                    {"order_id": order.order_id},
                    {
                        "$set": {
                            "xt_order_id": xt_order_id,
                            "stock_code": normalize_code(order.stock_code),
                            "broker_code": broker_code,
                            "direction": order.direction,
                            "volume": order.volume,
                            "price": order.price,
                            "status": "pending",
                            "submitted_at": now_iso,
                            "updated_at": now_iso,
                        },
                        "$setOnInsert": {
                            "order_id": order.order_id,
                        },
                    },
                    upsert=True,
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
        """请求撤单（不立即改 status=cancelled）。

        真实 QMT 的撤单是**异步且可能失败**的：
          - 涨跌停锁死时不可撤
          - 集合竞价 09:20-09:25 段不可撤
          - 已成交/部分成交后剩余部分不可撤（需拆分）

        因此这里只做两件事：
          1) 把 broker 侧的 cancel 请求发出去
          2) 把 DB 状态置为 "pending_cancel"（"已请求撤单，等 broker 回执"）

        真正把状态推进到 "cancelled" 必须由 broker 回报驱动
        （见 on_order_status 回调）。原实现直接标 cancelled 会造成
        DB=cancelled 但券商侧仍在成交的账本发散事故。
        """
        logger.info(f"🚫 撤单请求: {order_id}")

        doc = await self.db_service.query_one(
            _ORDERS_COLLECTION, {"order_id": order_id}
        )
        if not doc:
            logger.error(f"❌ 订单不存在: {order_id}")
            return False

        # 已终态订单不再重复撤单
        if doc.get("status") in self._TERMINAL_STATUSES:
            logger.warning(
                "订单 %s 已终态 (%s)，忽略撤单请求", order_id, doc.get("status")
            )
            return False

        xt_order_id = doc.get("xt_order_id")
        cancel_ok = False
        if xt_order_id:
            try:
                # cancel_order 底层可能返回 None / bool / int，各家 QMT 略有不同
                ret = self.qmt_util.cancel_order(xt_order_id)
                # 只要没有异常，就认为撤单请求已发到 broker，具体成败等回报
                cancel_ok = ret is not False
            except Exception as e:
                logger.error(f"❌ broker 撤单调用异常 {order_id}: {e}")
                cancel_ok = False

        # 只标 pending_cancel，等 on_order_status 回调把状态推到 cancelled/filled/partial
        await self.db_service.update_one(
            _ORDERS_COLLECTION,
            {"order_id": order_id},
            {
                "status": "pending_cancel" if cancel_ok else "cancel_failed",
                "updated_at": datetime.utcnow().isoformat(),
            },
        )
        logger.info(
            "撤单请求已发送: %s (broker_ret_ok=%s)，最终状态等 broker 回执",
            order_id, cancel_ok,
        )
        return cancel_ok

    # ============ 订单状态机推进 ============
    # 以下两个方法是 broker 回报的接入点。真实接入 QMT 需要在 QMTUtil
    # 侧注册 xtquant.xttrader.XtQuantTrader.register_callback(...)
    # 的 on_order / on_trade 回调，回调里调用这两个方法即可把状态机跑通。
    # 目前 QMTUtil 未提供该注册钩子；下面两个方法保证了状态机在**上层
    # 一旦能拿到回报**时就能正确推进，不再"永远停在 pending"。

    async def on_order_status(
        self,
        xt_order_id: int,
        broker_status: str,
        filled_volume: int = 0,
        rejection_reason: Optional[str] = None,
    ) -> bool:
        """处理券商侧订单状态变更回报。

        broker_status: 直接来自 QMT 的字符串状态。常见值映射到我们内部状态：
            'accepted' / 'partial'  → pending / partial_filled
            'filled'                → filled
            'cancelled' / 'canceled'→ cancelled
            'rejected'              → rejected
        """
        s = (broker_status or "").strip().lower()
        mapping = {
            "accepted": "pending",
            "reported": "pending",
            "partial": "partial_filled",
            "partial_filled": "partial_filled",
            "filled": "filled",
            "traded": "filled",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "rejected": "rejected",
        }
        new_status = mapping.get(s)
        if not new_status:
            logger.warning("未识别的 broker_status: %s (xt=%s)", broker_status, xt_order_id)
            return False

        doc = await self.db_service.query_one(
            _ORDERS_COLLECTION, {"xt_order_id": xt_order_id}
        )
        if not doc:
            logger.error("on_order_status: 未找到 xt_order_id=%s 对应订单", xt_order_id)
            return False
        # 已终态不允许被回退（例如 filled 之后再来一条 accepted）
        if doc.get("status") in self._TERMINAL_STATUSES:
            logger.debug(
                "订单 %s 已在终态 %s，忽略状态更新 %s",
                doc.get("order_id"), doc.get("status"), new_status,
            )
            return False

        update: Dict = {
            "status": new_status,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if filled_volume:
            update["filled_volume"] = int(filled_volume)
        if rejection_reason:
            update["rejection_reason"] = rejection_reason
        await self.db_service.update_one(
            _ORDERS_COLLECTION,
            {"xt_order_id": xt_order_id},
            update,
        )
        return True

    async def on_trade(
        self,
        xt_order_id: int,
        filled_price: float,
        filled_volume: int,
        broker_trade_id: Optional[str] = None,
        trade_time: Optional[str] = None,
    ) -> bool:
        """处理券商成交回报——记录成交明细，并推进订单状态。

        broker_trade_id：券商侧唯一的 fill id（如 QMT 的 traded_id/business_id）。
        必须传入以支持幂等——QMT 在断线重连/轮询模式下会对同一成交回调多次，
        本地账本必须去重，否则 current_positions 会重复累加。
        """
        doc = await self.db_service.query_one(
            _ORDERS_COLLECTION, {"xt_order_id": xt_order_id}
        )
        if not doc:
            logger.error("on_trade: 未找到 xt_order_id=%s 对应订单", xt_order_id)
            return False

        # 幂等：同一 broker_trade_id 只记一次
        if broker_trade_id:
            existing_fills = doc.get("fills") or []
            if any(f.get("broker_trade_id") == broker_trade_id for f in existing_fills):
                logger.debug(
                    "on_trade: 重复回调忽略 (order=%s trade=%s)",
                    doc.get("order_id"), broker_trade_id,
                )
                return False

        fill = {
            "broker_trade_id": broker_trade_id,
            "filled_price": float(filled_price),
            "filled_volume": int(filled_volume),
            "trade_time": trade_time or datetime.utcnow().isoformat(),
        }
        total_filled = int(doc.get("filled_volume") or 0) + int(filled_volume)
        target_volume = int(doc.get("volume") or 0)
        # 已终态订单不推进 status，但仍记录 fill 供审计
        if doc.get("status") in self._TERMINAL_STATUSES:
            new_status = doc.get("status")
        elif target_volume > 0 and total_filled >= target_volume:
            new_status = "filled"
        else:
            new_status = "partial_filled"

        await self.db_service.update_one(
            _ORDERS_COLLECTION,
            {"xt_order_id": xt_order_id},
            {
                "$set": {
                    "status": new_status,
                    "filled_volume": total_filled,
                    "updated_at": datetime.utcnow().isoformat(),
                },
                "$push": {"fills": fill},
            },
        )
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
        """判断 QMT 连接是否活跃。

        优先调用 qmt_util 的公共方法（不同实现可能是 is_connected() 或
        connected 属性），退回到私有 _connected 属性做兜底——避免因对方
        重命名内部字段而静默失联。
        """
        util = self.qmt_util
        # 1) 公共方法
        fn = getattr(util, "is_connected", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                pass
        # 2) 常见的公共属性名
        for attr in ("connected", "_connected"):
            if hasattr(util, attr):
                try:
                    return bool(getattr(util, attr))
                except Exception:
                    continue
        return False


def _accepts_volume(fn) -> bool:
    import inspect

    try:
        return "volume" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
