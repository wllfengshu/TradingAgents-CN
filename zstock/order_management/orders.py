"""
订单生成与对账

- OrderGenerator: 目标持仓 vs 当前持仓 → 订单列表；落库 created
- TradeSettlement: 券商持仓同步、目标股数 vs 券商对账
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from zstock.common.utils.common_utils import buy_shares, normalize_code, sell_shares
from zstock.common.entity.order_entity import Order

logger = logging.getLogger(__name__)

ORDER_COLLECTION_NAME = "zstock_orders"


class OrderGenerator:
    """订单黑盒：generate_orders / lots_from_positions / persist_orders。"""

    def __init__(self):
        self.orders: Dict[str, Order] = {}
        self.trade_date: Optional[str] = None
        self.order_sequence = 0
        logger.info("✅ OrderGenerator 初始化完成")

    @staticmethod
    def _position_code_column(df: pd.DataFrame) -> str:
        if "stock_code" in df.columns:
            return "stock_code"
        if "code" in df.columns:
            return "code"
        raise KeyError("持仓 DataFrame 须包含 code 或 stock_code 列")

    @staticmethod
    def _normalize_positions_df(df: Optional[pd.DataFrame]) -> pd.DataFrame:
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
        available_cash: Optional[float] = None,
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

        target_positions = self._normalize_positions_df(target_positions)
        if target_positions.empty:
            logger.warning("⚠️ 目标持仓为空")
            return orders

        current_positions = self._normalize_positions_df(current_positions)
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
            can_use_map: Dict[str, int] = {}
            for _, row in current_positions.iterrows():
                stock_code = row.get("stock_code")
                volume = int(row.get("volume", 0) or 0)
                if not stock_code:
                    continue
                current_map[str(stock_code)] = volume
                if "can_use_volume" in row.index and not pd.isna(row.get("can_use_volume")):
                    try:
                        can_use = int(row.get("can_use_volume"))
                    except (TypeError, ValueError):
                        can_use = volume
                else:
                    can_use = volume
                can_use_map[str(stock_code)] = max(0, min(can_use, volume))

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
                qty = sell_shares(
                    stock_code, vol, vol, can_use_map.get(stock_code, vol)
                )
                if qty > 0:
                    orders.append(self._create_order(stock_code, "sell", qty, "limit"))

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
                    qty = sell_shares(
                        stock_code,
                        cur_vol - target_vol,
                        cur_vol,
                        can_use_map.get(stock_code, cur_vol),
                    )
                    if qty > 0:
                        orders.append(self._create_order(stock_code, "sell", qty, "limit"))

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
                    orders.append(self._create_order(stock_code, "buy", target_vol, "limit"))

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
                    qty = buy_shares(stock_code, target_vol - cur_vol)
                    if qty > 0:
                        orders.append(self._create_order(stock_code, "buy", qty, "limit"))

            if skipped:
                logger.error(
                    "❌ 缺价或无法换算，已跳过（保留当前持仓，不清仓）: %s",
                    skipped,
                )

            orders = self._cap_buys_to_cash(
                orders, price_map, total_capital, available_cash
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

    @staticmethod
    def _cap_buys_to_cash(
        orders: List[Order],
        price_map: Dict[str, float],
        total_capital: Optional[float],
        available_cash: Optional[float],
    ) -> List[Order]:
        """买单金额不超过可用现金 + 本批卖出回款。权重超过总资产的买单丢掉。"""
        if not orders:
            return orders
        sells = [o for o in orders if o.direction == "sell"]
        buys = [o for o in orders if o.direction == "buy"]
        if total_capital is not None and float(total_capital) > 0:
            cap = float(total_capital)
            kept = []
            for order in buys:
                px = float(price_map.get(order.stock_code, 0.0) or 0.0)
                if px > 0 and order.volume * px > cap * 1.001:
                    logger.error(
                        "买单 %s 金额 %.0f 超过总资产 %.0f，丢弃",
                        order.stock_code, order.volume * px, cap,
                    )
                    continue
                kept.append(order)
            buys = kept
        if available_cash is None:
            return sells + buys
        budget = max(0.0, float(available_cash))
        for order in sells:
            px = float(price_map.get(order.stock_code, 0.0) or 0.0)
            if px > 0:
                budget += order.volume * px
        need = 0.0
        priced: List[tuple] = []
        for order in buys:
            px = float(price_map.get(order.stock_code, 0.0) or 0.0)
            if px <= 0:
                logger.error("买单 %s 缺价，丢弃", order.stock_code)
                continue
            priced.append((order, px))
            need += order.volume * px
        if need <= budget + 1e-6 or need <= 0:
            return sells + [order for order, _px in priced]
        scale = budget / need
        logger.warning(
            "买单金额 %.0f 超过可用 %.0f（含卖出回款），按 %.2f 缩小",
            need, budget, scale,
        )
        scaled: List[Order] = []
        for order, px in priced:
            qty = buy_shares(order.stock_code, int(order.volume * scale))
            if qty <= 0:
                logger.error("缩小后 %s 不足一手，跳过买入", order.stock_code)
                continue
            order.volume = qty
            scaled.append(order)
        return sells + scaled

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
            if weight > 1.0 + 1e-6:
                logger.error("权重 %.2f 超过 100%%，拒绝换算股数: %s", weight, stock_code)
                return None
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
            notional = shares * price
            if notional > float(total_capital) * 1.001:
                logger.error(
                    "目标金额 %.0f 超过账户总资产 %.0f，拒绝: %s",
                    notional, float(total_capital), stock_code,
                )
                return None
            lot_shares = buy_shares(stock_code, shares)
            if lot_shares <= 0:
                logger.error(
                    "正权重整手后不足 1 手，跳过（不清仓）: %s weight=%s price=%s",
                    stock_code,
                    weight,
                    price,
                )
                return None
            return lot_shares
        if "volume" in row.index and not pd.isna(row["volume"]):
            try:
                return max(int(row["volume"]), 0)
            except (TypeError, ValueError):
                return None
        logger.error("目标行既无 target_shares 也无 weight，跳过")
        return None

    def lots_from_positions(
        self,
        positions: Optional[pd.DataFrame],
        price_map: Optional[Dict[str, float]] = None,
        total_capital: Optional[float] = None,
    ) -> pd.DataFrame:
        """目标持仓 → stock_code + volume（股）。换算失败的标的不出现在结果里。"""
        df = self._normalize_positions_df(positions)
        if df.empty:
            return pd.DataFrame(columns=["stock_code", "volume"])
        raw = price_map or {}
        px: Dict[str, float] = {}
        for k, v in raw.items():
            if v is None:
                continue
            try:
                price = float(v)
            except (TypeError, ValueError):
                continue
            if price > 0:
                px[normalize_code(k)] = price
        rows = []
        for _, row in df.iterrows():
            vol = self._row_to_volume(row, px, total_capital)
            if vol is None:
                continue
            rows.append({"stock_code": str(row["stock_code"]), "volume": int(vol)})
        if not rows:
            return pd.DataFrame(columns=["stock_code", "volume"])
        return pd.DataFrame(rows)

    def _get_orders(self, order_id: Optional[str] = None) -> List[Order]:
        if order_id:
            order = self.orders.get(order_id)
            return [order] if order else []
        return list(self.orders.values())

    def _update_order_status(
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
        order.updated_at = datetime.now(timezone.utc).isoformat()
        logger.info(f"📝 订单状态更新: {order_id} → {status}")
        return True

    def _export_orders(self, format: str = "dataframe"):
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

    @staticmethod
    async def persist_orders(orders: List[Order], database_service=None) -> bool:
        """把订单落库为 created（幂等：$setOnInsert，不覆盖已存在文档）。

        与 XtQuantExecutor 共用 zstock_orders，按 order_id 幂等：
          created  (OrderGenerator.persist_orders)
              → pending  (submit_order 成功后写 xt_order_id/status)
              → filled / partial / cancelled / rejected
        """
        if not orders:
            return True
        db = database_service
        if db is None:
            from zstock.data_management.query_service import get_data_query_service

            db = get_data_query_service()
        try:
            for o in orders:
                doc = o.to_dict()
                # 落库必须是 created。对象默认若被写成 pending，提交侧会把它当成已发送而跳过。
                doc["status"] = "created"
                await db.update_one(
                    ORDER_COLLECTION_NAME,
                    {"order_id": doc["order_id"]},
                    {"$setOnInsert": doc},
                    upsert=True,
                )
            return True
        except Exception as e:
            logger.error(f"❌ 订单落库失败: {e}")
            return False


class TradeSettlement:
    """
    成交回报处理器

    职责：
    - 监听和处理成交回报
    - 更新实际持仓
    - 计算执行成本和滑点
    - 记录成交明细

    属性：
        trades: 成交记录 {trade_id: trade_info}
        current_positions: 当前持仓
    """

    def __init__(self):
        """初始化成交回报处理器"""
        self.trades = {}  # {trade_id: trade_info}
        self.current_positions = {}  # {stock_code: volume}
        self.trade_sequence = 0

        logger.info("✅ TradeSettlement 初始化完成")

    def _handle_trade_report(self, order_id: str, filled_volume: int,
                           filled_price: float, order_direction: str,
                           stock_code: str) -> bool:
        """
        处理成交回报

        Args:
            order_id: 订单ID
            filled_volume: 成交数量
            filled_price: 成交价格
            order_direction: 交易方向（buy / sell）
            stock_code: 股票代码

        Returns:
            bool: 处理是否成功
        """
        try:
            logger.info(f"📥 处理成交回报: {order_id} {stock_code} "
                       f"{order_direction} x{filled_volume} @ {filled_price:.2f}")

            # ============================================================
            # 第一步：创建成交记录
            # ============================================================
            self.trade_sequence += 1
            trade_id = f"TRADE{self.trade_sequence:06d}"

            trade_info = {
                'trade_id': trade_id,
                'order_id': order_id,
                'stock_code': stock_code,
                'direction': order_direction,
                'volume': filled_volume,
                'price': filled_price,
                'amount': filled_volume * filled_price,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }

            self.trades[trade_id] = trade_info

            # ============================================================
            # 第二步：更新持仓
            # ============================================================
            self._update_position(stock_code, order_direction, filled_volume)

            # ============================================================
            # 第三步：计算滑点和成本
            # ============================================================
            # 滑点 = (成交价 - 预期价) / 预期价
            # 这里使用成交价格作为基准（实际应传入预期价格）
            trade_info['slippage'] = 0.0  # 待计算

            logger.info(f"✅ 成交回报处理完成: {trade_id}")

            return True

        except Exception as e:
            logger.error(f"❌ 成交回报处理失败: {e}")
            return False

    def _update_position(self, stock_code: str, direction: str, volume: int) -> None:
        """更新持仓"""
        current_volume = self.current_positions.get(stock_code, 0)

        if direction == 'buy':
            new_volume = current_volume + volume
        elif direction == 'sell':
            new_volume = max(0, current_volume - volume)
        else:
            logger.error(f"❌ 未知的交易方向: {direction}")
            return

        self.current_positions[stock_code] = new_volume

        logger.info(f"   持仓更新: {stock_code} {current_volume} → {new_volume}")

    def _reconcile_positions(self, xtquant_positions: List[Dict]) -> Dict:
        """
        对账：比较本地持仓和 XtQuant 持仓

        Args:
            xtquant_positions: XtQuant 返回的持仓列表

        Returns:
            Dict: 对账结果
                {
                    'matched': [...],       # 匹配的持仓
                    'discrepancies': [...], # 不一致的持仓
                    'status': 'ok' / 'warning' / 'error',
                }
        """
        logger.info("🔍 开始对账")

        # 创建 XtQuant 持仓字典（统一 6 位 code）
        from zstock.common.utils.common_utils import normalize_code

        xtquant_map = {
            normalize_code(p["code"]): int(p.get("volume", 0))
            for p in xtquant_positions
            if p.get("code")
        }

        matched = []
        discrepancies = []

        # 检查所有本地持仓
        for stock_code, local_volume in self.current_positions.items():
            xtquant_volume = xtquant_map.get(stock_code, 0)

            if local_volume == xtquant_volume:
                matched.append({
                    'stock_code': stock_code,
                    'local_volume': local_volume,
                    'xtquant_volume': xtquant_volume,
                    'status': 'matched',
                })
            else:
                discrepancies.append({
                    'stock_code': stock_code,
                    'local_volume': local_volume,
                    'xtquant_volume': xtquant_volume,
                    'difference': xtquant_volume - local_volume,
                    'status': 'discrepancy',
                })

                logger.error(f"   ⚠️ 持仓不一致: {stock_code} "
                             f"本地={local_volume}, XtQuant={xtquant_volume}")

        # 检查 XtQuant 中但本地没有的持仓
        for stock_code, xtquant_volume in xtquant_map.items():
            if stock_code not in self.current_positions:
                discrepancies.append({
                    'stock_code': stock_code,
                    'local_volume': 0,
                    'xtquant_volume': xtquant_volume,
                    'difference': xtquant_volume,
                    'status': 'unknown',
                })

                logger.error(f"   ⚠️ XtQuant 存在但本地不存在: {stock_code} "
                             f"x{xtquant_volume}")

        # 判断对账状态
        if not discrepancies:
            status = 'ok'
        elif len(discrepancies) <= len(matched):
            status = 'warning'
        else:
            status = 'error'

        result = {
            'matched_count': len(matched),
            'discrepancy_count': len(discrepancies),
            'matched': matched,
            'discrepancies': discrepancies,
            'status': status,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

        logger.info(f"✅ 对账完成: {result['matched_count']} 正常, "
                   f"{result['discrepancy_count']} 异常, 状态={status}")

        return result

    def sync_positions_from_broker(self, broker_positions: List[Dict]) -> None:
        """用券商持仓初始化本地账本（对账前调用）。"""
        from zstock.common.utils.common_utils import normalize_code

        self.current_positions = {
            normalize_code(p["code"]): int(p.get("volume", 0))
            for p in broker_positions
            if p.get("code")
        }
        logger.info("📒 本地账本已从券商同步: %d 只", len(self.current_positions))

    def reconcile_target_vs_broker(
        self,
        target_lots: "pd.DataFrame",
        broker_positions: List[Dict],
    ) -> Dict:
        """对账：目标股数 vs 券商实际持仓。只吃 stock_code/code + volume。"""
        from zstock.common.utils.common_utils import normalize_code

        logger.info("🔍 目标持仓 vs 券商对账")

        lots: Dict[str, int] = {}
        if target_lots is not None and not target_lots.empty:
            code_col = "stock_code" if "stock_code" in target_lots.columns else "code"
            vol_col = "volume" if "volume" in target_lots.columns else "target_shares"
            for _, row in target_lots.iterrows():
                code = normalize_code(str(row[code_col]))
                try:
                    lots[code] = int(row[vol_col])
                except (TypeError, ValueError):
                    continue

        broker_map = {
            normalize_code(p["code"]): int(p.get("volume", 0))
            for p in broker_positions
            if p.get("code")
        }

        matched = []
        discrepancies = []

        for code, target_vol in lots.items():
            broker_vol = broker_map.get(code, 0)
            if target_vol == broker_vol:
                matched.append(
                    {"stock_code": code, "target_volume": target_vol, "broker_volume": broker_vol}
                )
            else:
                discrepancies.append(
                    {
                        "stock_code": code,
                        "target_volume": target_vol,
                        "broker_volume": broker_vol,
                        "difference": broker_vol - target_vol,
                    }
                )

        for code, broker_vol in broker_map.items():
            if code not in lots:
                discrepancies.append(
                    {
                        "stock_code": code,
                        "target_volume": 0,
                        "broker_volume": broker_vol,
                        "difference": broker_vol,
                        "status": "extra_holding",
                    }
                )

        if not discrepancies:
            status = "ok"
        elif len(discrepancies) <= len(matched):
            status = "warning"
        else:
            status = "error"

        result = {
            "matched_count": len(matched),
            "discrepancy_count": len(discrepancies),
            "matched": matched,
            "discrepancies": discrepancies,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            "✅ 目标对账: %d 一致, %d 偏差, status=%s",
            len(matched),
            len(discrepancies),
            status,
        )
        return result

    def _calculate_slippage(self, order_price: float, filled_price: float) -> float:
        """
        计算滑点

        滑点 = (成交价 - 订单价) / 订单价 × 100%

        Args:
            order_price: 订单价格
            filled_price: 成交价格

        Returns:
            float: 滑点（百分比）
        """
        if order_price <= 0:
            return 0.0

        slippage = (filled_price - order_price) / order_price * 100

        return round(slippage, 2)

    def _get_trades(self, trade_id: Optional[str] = None) -> List[Dict]:
        """
        获取成交记录

        Args:
            trade_id: 指定成交ID，如为 None 则返回所有

        Returns:
            List[Dict]: 成交记录列表
        """
        if trade_id:
            trade = self.trades.get(trade_id)
            return [trade] if trade else []

        return list(self.trades.values())

    def _export_trades(self, format: str = 'dataframe') -> object:
        """
        导出成交记录

        Args:
            format: 导出格式（dataframe / list）

        Returns:
            DataFrame 或 List
        """
        if format == 'dataframe':
            if not self.trades:
                return pd.DataFrame()
            return pd.DataFrame(list(self.trades.values()))

        elif format == 'list':
            return list(self.trades.values())

        else:
            logger.warning(f"❌ 未知的导出格式: {format}")
            return None

    def _get_current_positions(self) -> Dict[str, int]:
        """获取当前持仓"""
        return self.current_positions.copy()

    def _export_positions(self, format: str = 'dataframe') -> object:
        """
        导出当前持仓

        Args:
            format: 导出格式（dataframe / dict）

        Returns:
            DataFrame 或 Dict
        """
        if format == 'dataframe':
            if not self.current_positions:
                return pd.DataFrame()

            data = [
                {'stock_code': code, 'volume': volume}
                for code, volume in self.current_positions.items()
            ]
            return pd.DataFrame(data)

        elif format == 'dict':
            return self.current_positions.copy()

        else:
            logger.warning(f"❌ 未知的导出格式: {format}")
            return None
