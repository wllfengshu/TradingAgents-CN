"""
订单生成模块

根据目标持仓和当前持仓计算持仓差异，生成订单列表。

设计约定：
- ``generate_orders`` 是同步纯计算函数。把 weight → shares 的换算所需的
  「最新价」和「总资金」由外部 *预先* 注入，不在内部偷偷起异步调用 / 起
  新的数据库连接（避免阻塞事件循环或产生未 await 的 coroutine）。
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from zstock.data_management.database_service import get_database_service
from zstock.common.entity.order_entity import Order

logger = logging.getLogger(__name__)

# 表名
ORDER_COLLECTION_NAME = "zstock_orders"

class OrderGenerator:
    """订单生成器。"""

    def __init__(self):
        self.orders: Dict[str, Order] = {}
        self.trade_date: Optional[str] = None
        self.order_sequence = 0
        self.database_service = get_database_service()
        logger.info("✅ OrderGenerator 初始化完成")

    @staticmethod
    def _position_code_column(df: pd.DataFrame) -> str:
        if 'stock_code' in df.columns:
            return 'stock_code'
        if 'code' in df.columns:
            return 'code'
        raise KeyError("持仓 DataFrame 须包含 code 或 stock_code 列")

    def generate_orders(
        self,
        target_positions: pd.DataFrame,
        current_positions: Optional[pd.DataFrame] = None,
        trade_date: Optional[str] = None,
        price_map: Optional[Dict[str, float]] = None,
        total_capital: float = 1e7,
    ) -> List[Order]:
        """根据目标/当前持仓生成订单列表。

        Args:
            target_positions: 目标持仓 DataFrame，须含 ``stock_code``，
                以及 ``target_shares`` 或 ``weight``。
            current_positions: 当前持仓 DataFrame，须含 ``stock_code``、``volume``。
            trade_date: 'YYYY-MM-DD'（也接受 YYYYMMDD，内部规范化）。
            price_map: 用于把 weight 换算成股数的最新价 {code: price}。
                没有就只能依赖 target_positions 里已带的 ``target_shares``。
            total_capital: 总资金（元）。仅在依赖 weight 换算股数时使用。
        """
        from zstock.common.utils.common_utils import normalize_date

        logger.info("📋 开始生成订单")

        if trade_date is None:
            trade_date = pd.Timestamp.today().strftime("%Y-%m-%d")
        self.trade_date = normalize_date(trade_date)
        self.order_sequence = 0
        orders: List[Order] = []

        if target_positions is None or target_positions.empty:
            logger.warning("⚠️ 目标持仓为空")
            return orders

        price_map = price_map or {}

        try:
            if current_positions is None or current_positions.empty:
                current_positions = pd.DataFrame(columns=['stock_code', 'volume'])

            current_code_col = self._position_code_column(current_positions)
            target_code_col = self._position_code_column(target_positions)

            current_map: Dict[str, int] = {}
            for _, row in current_positions.iterrows():
                stock_code = row.get(current_code_col)
                volume = int(row.get('volume', 0))
                if stock_code is not None:
                    current_map[stock_code] = volume

            target_codes = set(target_positions[target_code_col].values)
            current_codes = set(current_map.keys())

            sell_codes = current_codes - target_codes
            buy_codes = target_codes - current_codes
            adjust_codes = target_codes & current_codes

            logger.info(f"   卖出 {len(sell_codes)} / 买入 {len(buy_codes)} / 调仓 {len(adjust_codes)}")

            for stock_code in sell_codes:
                vol = current_map.get(stock_code, 0)
                if vol > 0:
                    orders.append(self._create_order(stock_code, 'sell', vol, 'market'))

            for stock_code in adjust_codes:
                target_row = target_positions[target_positions[target_code_col] == stock_code]
                if target_row.empty:
                    continue
                target_vol = self._row_to_volume(target_row.iloc[0], price_map, total_capital)
                cur_vol = current_map.get(stock_code, 0)
                if target_vol < cur_vol:
                    orders.append(self._create_order(stock_code, 'sell', cur_vol - target_vol, 'market'))

            for stock_code in buy_codes:
                target_row = target_positions[target_positions[target_code_col] == stock_code]
                if target_row.empty:
                    continue
                target_vol = self._row_to_volume(target_row.iloc[0], price_map, total_capital)
                if target_vol > 0:
                    orders.append(self._create_order(stock_code, 'buy', target_vol, 'market'))

            for stock_code in adjust_codes:
                target_row = target_positions[target_positions[target_code_col] == stock_code]
                if target_row.empty:
                    continue
                target_vol = self._row_to_volume(target_row.iloc[0], price_map, total_capital)
                cur_vol = current_map.get(stock_code, 0)
                if target_vol > cur_vol:
                    orders.append(self._create_order(stock_code, 'buy', target_vol - cur_vol, 'market'))

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

    def _create_order(self, stock_code: str, direction: str, volume: int,
                      price_type: str = 'market', price: Optional[float] = None) -> Order:
        self.order_sequence += 1
        order_id = f"{self.trade_date}{self.order_sequence:04d}"
        return Order(
            order_id=order_id, stock_code=stock_code, direction=direction,
            volume=volume, price_type=price_type, price=price,
        )

    @staticmethod
    def _row_to_volume(
        row: pd.Series,
        price_map: Dict[str, float],
        total_capital: float,
    ) -> int:
        """从行数据中提取目标股数。优先用 target_shares，否则用 weight × 价格换算。"""
        if 'target_shares' in row and not pd.isna(row['target_shares']):
            try:
                return max(int(row['target_shares']), 0)
            except (TypeError, ValueError):
                return 0

        if 'weight' in row and not pd.isna(row['weight']):
            try:
                weight = float(row['weight'])
            except (TypeError, ValueError):
                return 0
            stock_code = row.get('stock_code')
            price = float(price_map.get(stock_code, 0.0) or 0.0)
            if price <= 0:
                logger.debug(f"   缺少最新价，无法把 weight 换算成股数: {stock_code}")
                return 0
            shares = int((total_capital * weight) / price)
            return (shares // 100) * 100
        return 0

    def get_orders(self, order_id: Optional[str] = None) -> List[Order]:
        if order_id:
            order = self.orders.get(order_id)
            return [order] if order else []
        return list(self.orders.values())

    def update_order_status(self, order_id: str, status: str,
                            filled_volume: int = 0,
                            filled_price: Optional[float] = None) -> bool:
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

    def export_orders(self, format: str = 'dataframe'):
        orders = list(self.orders.values())
        if format == 'dataframe':
            data = [o.to_dict() for o in orders]
            return pd.DataFrame(data) if data else pd.DataFrame()
        if format == 'list':
            return [o.to_dict() for o in orders]
        if format == 'dict':
            return {o.order_id: o.to_dict() for o in orders}
        logger.warning(f"❌ 未知的导出格式: {format}")
        return None

    async def save_orders_to_db(self, orders: List[Order]) -> bool:
        """异步持久化订单到 MongoDB。"""
        if not orders:
            return True
        try:
            await self.database_service.insert_many(
                ORDER_COLLECTION_NAME, [o.to_dict() for o in orders]
            )
            return True
        except Exception as e:
            logger.error(f"❌ 订单落库失败: {e}")
            return False
