"""
成交回报处理模块

负责处理订单成交回报、更新持仓、计算滑点等。

核心功能：
1. 成交回报监听
2. 持仓更新
3. 滑点计算
4. 成交记录保存
"""

import logging
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


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

    def handle_trade_report(self, order_id: str, filled_volume: int,
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
                'timestamp': datetime.utcnow().isoformat(),
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

    def reconcile_positions(self, xtquant_positions: List[Dict]) -> Dict:
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
            'timestamp': datetime.utcnow().isoformat(),
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
        target_positions: "pd.DataFrame",
        broker_positions: List[Dict],
        price_map: Optional[Dict[str, float]] = None,
        total_capital: Optional[float] = None,
    ) -> Dict:
        """
        对账：目标持仓 vs 券商实际持仓（执行层核心对账）。

        比较的是「应该持有多少股」与「实际持有多少股」，而非空本地账本。
        """
        from zstock.common.utils.common_utils import normalize_code
        from zstock.order_management.order_generator import OrderGenerator

        logger.info("🔍 目标持仓 vs 券商对账")

        target_df = OrderGenerator.normalize_positions_df(target_positions)
        broker_map = {
            normalize_code(p["code"]): int(p.get("volume", 0))
            for p in broker_positions
            if p.get("code")
        }

        matched = []
        discrepancies = []

        for _, row in target_df.iterrows():
            code = str(row["stock_code"])
            broker_vol = broker_map.get(code, 0)
            if "target_shares" in row.index and not pd.isna(row.get("target_shares")):
                target_vol = int(row["target_shares"])
            else:
                target_vol = OrderGenerator._row_to_volume(
                    row, price_map or {}, total_capital
                )
            if target_vol is None:
                discrepancies.append(
                    {
                        "stock_code": code,
                        "target_volume": None,
                        "broker_volume": broker_vol,
                        "difference": None,
                        "status": "skipped_unpriced",
                    }
                )
                continue
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
            if code not in set(target_df["stock_code"].astype(str)):
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
            "timestamp": datetime.utcnow().isoformat(),
        }
        logger.info(
            "✅ 目标对账: %d 一致, %d 偏差, status=%s",
            len(matched),
            len(discrepancies),
            status,
        )
        return result

    def calculate_slippage(self, order_price: float, filled_price: float) -> float:
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

    def get_trades(self, trade_id: Optional[str] = None) -> List[Dict]:
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

    def export_trades(self, format: str = 'dataframe') -> object:
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

    def get_current_positions(self) -> Dict[str, int]:
        """获取当前持仓"""
        return self.current_positions.copy()

    def export_positions(self, format: str = 'dataframe') -> object:
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
