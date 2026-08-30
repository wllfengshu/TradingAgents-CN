"""
订单生成模块

根据目标持仓和当前持仓计算持仓差异，生成订单列表。

设计约定：
- ``generate_orders`` 是同步纯计算函数。把 weight → shares 的换算所需的
  「最新价」和「总资金」由外部 *预先* 注入，不在内部偷偷起异步调用 / 起
  新的数据库连接（避免阻塞事件循环或产生未 await 的 coroutine）。
- 目标/当前持仓列名兼容策略层 ``code`` + ``weight`` 与执行层 ``stock_code`` + ``volume``。
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from zstock.common.utils.common_utils import normalize_code
from zstock.data_management.database_service import get_database_service
from zstock.common.entity.order_entity import Order

logger = logging.getLogger(__name__)

ORDER_COLLECTION_NAME = "zstock_orders"


class OrderGenerator:
    """订单生成器。"""

    def __init__(self):
        self.orders: Dict[str, Order] = {}
        self.trade_date: Optional[str] = None
        self.order_sequence = 0
        self._database_service = None
        logger.info("✅ OrderGenerator 初始化完成")

    @property
    def database_service(self):
        if self._database_service is None:
            self._database_service = get_database_service()
        return self._database_service

    @staticmethod
    def _position_code_column(df: pd.DataFrame) -> str:
        if "stock_code" in df.columns:
            return "stock_code"
        if "code" in df.columns:
            return "code"
        raise KeyError("持仓 DataFrame 须包含 code 或 stock_code 列")

    @staticmethod
    def normalize_positions_df(df: Optional[pd.DataFrame]) -> pd.DataFrame:
        """统一为 stock_code(6位) + volume/weight/target_shares 列。"""
        if df is None or df.empty:
            return pd.DataFrame(columns=["stock_code", "volume"])

        out = df.copy()
        code_col = OrderGenerator._position_code_column(out)
        if code_col != "stock_code":
            out = out.rename(columns={code_col: "stock_code"})
        out["stock_code"] = out["stock_code"].astype(str).map(normalize_code)
        return out

    @staticmethod
    def _extract_stock_code(row: pd.Series) -> Optional[str]:
        for col in ("stock_code", "code"):
            if col in row.index:
                val = row.get(col)
                if val is not None and str(val) not in ("", "nan", "None"):
                    return normalize_code(str(val))
        return None

    def generate_orders(
        self,
        target_positions: pd.DataFrame,
        current_positions: Optional[pd.DataFrame] = None,
        trade_date: Optional[str] = None,
        price_map: Optional[Dict[str, float]] = None,
        total_capital: Optional[float] = None,
    ) -> List[Order]:
        """根据目标/当前持仓生成订单列表。

        缺行情时跳过该标的、保留当前持仓，绝不把目标股数当成 0 去清仓。
        用 weight 换算时必须传入有效 total_capital，不再默认 1000 万。
        """
        from zstock.common.utils.common_utils import normalize_date

        logger.info("📋 开始生成订单")

        if trade_date is None:
            trade_date = pd.Timestamp.today().strftime("%Y-%m-%d")
        self.trade_date = normalize_date(trade_date)
        self.order_sequence = 0
        orders: List[Order] = []

        target_positions = self.normalize_positions_df(target_positions)
        if target_positions.empty:
            logger.warning("⚠️ 目标持仓为空")
            return orders

        current_positions = self.normalize_positions_df(current_positions)
        raw_price_map = price_map or {}
        price_map = {}
        for k, v in raw_price_map.items():
            if v is None:
                continue
            try:
                px = float(v)
            except (TypeError, ValueError):
                continue
            if px > 0:
                price_map[normalize_code(k)] = px

        try:
            current_map: Dict[str, int] = {}
            for _, row in current_positions.iterrows():
                stock_code = row.get("stock_code")
                volume = int(row.get("volume", 0))
                if stock_code:
                    current_map[str(stock_code)] = volume

            target_codes = set(target_positions["stock_code"].astype(str))
            current_codes = set(current_map.keys())

            sell_codes = current_codes - target_codes
            buy_codes = target_codes - current_codes
            adjust_codes = target_codes & current_codes

            logger.info(
                f"   卖出 {len(sell_codes)} / 买入 {len(buy_codes)} / 调仓 {len(adjust_codes)}"
            )

            for stock_code in sell_codes:
                vol = current_map.get(stock_code, 0)
                if vol > 0:
                    orders.append(self._create_order(stock_code, "sell", vol, "market"))

            skipped: List[str] = []

            for stock_code in adjust_codes:
                target_row = target_positions[target_positions["stock_code"] == stock_code]
                if target_row.empty:
                    continue
                target_vol = self._row_to_volume(
                    target_row.iloc[0], price_map, total_capital
                )
                if target_vol is None:
                    skipped.append(stock_code)
                    continue
                cur_vol = current_map.get(stock_code, 0)
                if target_vol < cur_vol:
                    orders.append(
                        self._create_order(stock_code, "sell", cur_vol - target_vol, "market")
                    )

            for stock_code in buy_codes:
                target_row = target_positions[target_positions["stock_code"] == stock_code]
                if target_row.empty:
                    continue
                target_vol = self._row_to_volume(
                    target_row.iloc[0], price_map, total_capital
                )
                if target_vol is None:
                    skipped.append(stock_code)
                    continue
                if target_vol > 0:
                    orders.append(self._create_order(stock_code, "buy", target_vol, "market"))

            for stock_code in adjust_codes:
                if stock_code in skipped:
                    continue
                target_row = target_positions[target_positions["stock_code"] == stock_code]
                if target_row.empty:
                    continue
                target_vol = self._row_to_volume(
                    target_row.iloc[0], price_map, total_capital
                )
                if target_vol is None:
                    skipped.append(stock_code)
                    continue
                cur_vol = current_map.get(stock_code, 0)
                if target_vol > cur_vol:
                    orders.append(
                        self._create_order(stock_code, "buy", target_vol - cur_vol, "market")
                    )

            if skipped:
                logger.error(
                    "❌ 缺价或无法换算，已跳过（保留当前持仓，不清仓）: %s",
                    skipped,
                )

            for order in orders:
                self.orders[order.order_id] = order

            logger.info(
                f"✅ 订单生成完成 共 {len(orders)} 个 ("
                f"卖出 {sum(1 for o in orders if o.direction == 'sell')}, "
                f"买入 {sum(1 for o in orders if o.direction == 'buy')})"
            )
            return orders

        except Exception as e:
            logger.error(f"❌ 订单生成失败: {e}", exc_info=True)
            return []

    def _create_order(
        self,
        stock_code: str,
        direction: str,
        volume: int,
        price_type: str = "market",
        price: Optional[float] = None,
    ) -> Order:
        self.order_sequence += 1
        # order_id 需要跨进程/跨实例保证唯一：
        # 单纯 "{trade_date}{sequence:04d}" 在"同一天重跑 pipeline / 崩溃重启 /
        # 多账户多进程"场景下会与另一 OrderGenerator 实例产出同 id，
        # 与 DB 唯一索引冲突或数据覆盖。加短 uuid 后缀彻底解决。
        import uuid
        suffix = uuid.uuid4().hex[:6]
        order_id = f"{self.trade_date.replace('-', '')}{self.order_sequence:04d}{suffix}"
        return Order(
            order_id=order_id,
            stock_code=normalize_code(stock_code),
            direction=direction,
            volume=volume,
            price_type=price_type,
            price=price,
        )

    @staticmethod
    def _row_to_volume(
        row: pd.Series,
        price_map: Dict[str, float],
        total_capital: Optional[float],
    ) -> Optional[int]:
        """从行数据中提取目标股数。优先 target_shares，否则 weight × 价格。

        返回 None 表示无法确定（缺价、缺资金等）。调用方必须跳过该标的，
        不得把 None 当成 0 股去生成卖单。
        """
        if "target_shares" in row.index and not pd.isna(row["target_shares"]):
            try:
                return max(int(row["target_shares"]), 0)
            except (TypeError, ValueError):
                logger.error("target_shares 无法解析，跳过该标的")
                return None

        if "weight" in row.index and not pd.isna(row["weight"]):
            try:
                weight = float(row["weight"])
            except (TypeError, ValueError):
                return None
            stock_code = OrderGenerator._extract_stock_code(row)
            if not stock_code:
                return None
            if weight <= 0:
                return 0
            if total_capital is None or total_capital <= 0:
                logger.error(
                    "缺少有效总资金，无法把 weight 换算成股数: %s", stock_code
                )
                return None
            price = float(price_map.get(stock_code, 0.0) or 0.0)
            if price <= 0:
                logger.error(
                    "缺少最新价，无法把 weight 换算成股数（跳过，不清仓）: %s",
                    stock_code,
                )
                return None
            shares = int((total_capital * weight) / price)
            lot_shares = (shares // 100) * 100
            if lot_shares <= 0:
                logger.error(
                    "正权重整手后不足 1 手，跳过（不清仓）: %s weight=%s price=%s",
                    stock_code,
                    weight,
                    price,
                )
                return None
            return lot_shares
        logger.error("目标行既无 target_shares 也无 weight，跳过")
        return None

    def get_orders(self, order_id: Optional[str] = None) -> List[Order]:
        if order_id:
            order = self.orders.get(order_id)
            return [order] if order else []
        return list(self.orders.values())

    def update_order_status(
        self,
        order_id: str,
        status: str,
        filled_volume: int = 0,
        filled_price: Optional[float] = None,
    ) -> bool:
        order = self.orders.get(order_id)
        if not order:
            logger.warning(f"❌ 订单不存在: {order_id}")
            return False
        order.status = status
        order.filled_volume = filled_volume
        order.filled_price = filled_price
        order.updated_at = datetime.utcnow().isoformat()
        logger.info(f"📝 订单状态更新: {order_id} → {status}")
        return True

    def export_orders(self, format: str = "dataframe"):
        orders = list(self.orders.values())
        if format == "dataframe":
            data = [o.to_dict() for o in orders]
            return pd.DataFrame(data) if data else pd.DataFrame()
        if format == "list":
            return [o.to_dict() for o in orders]
        if format == "dict":
            return {o.order_id: o.to_dict() for o in orders}
        logger.warning(f"❌ 未知的导出格式: {format}")
        return None

    async def save_orders_to_db(self, orders: List[Order]) -> bool:
        """把订单落库为"created"状态（幂等：仅在首次插入时写入初始字段）。

        订单生命周期由两个模块协作维护，共用 zstock_orders 集合按 order_id 幂等：
          created  (order_generator.save_orders_to_db)
              → pending  (xtquant_executor.submit_order 成功后写 xt_order_id/status)
              → filled / partial / cancelled / rejected  (由 broker 回报驱动)

        用 update_one(upsert=True) + $setOnInsert，而不是 insert_many：
          - insert_many 与 executor 端的 upsert 会因同 order_id 造成 DuplicateKey
            或数据重复（历史 bug：账本损坏）；
          - $setOnInsert 保证"文档已存在则不覆盖"，即使 executor 已先写入
            pending + xt_order_id，此处也不会把 status 退回 created。
        """
        if not orders:
            return True
        try:
            for o in orders:
                doc = o.to_dict()
                if not doc.get("status"):
                    doc["status"] = "created"
                await self.database_service.update_one(
                    ORDER_COLLECTION_NAME,
                    {"order_id": doc["order_id"]},
                    {"$setOnInsert": doc},
                    upsert=True,
                )
            return True
        except Exception as e:
            logger.error(f"❌ 订单落库失败: {e}")
            return False
