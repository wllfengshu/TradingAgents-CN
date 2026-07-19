"""
持仓收益服务
管理模拟/实盘持仓快照、交易流水、收益计算
"""

import math
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from zoneinfo import ZoneInfo

from app.core.database import get_mongo_db
from app.utils.xtquant_mock_util import MockQMTUtil

logger = logging.getLogger("app.services.portfolio_service")

_CN_TZ = ZoneInfo("Asia/Shanghai")


def _now_cn() -> datetime:
    return datetime.now(_CN_TZ).replace(tzinfo=None)


class PortfolioService:
    """持仓收益服务"""

    # ── 模拟下单持久化 ──────────────────────────────────────

    async def save_simulated_orders(
        self,
        user_id: str,
        task_id: str,
        orders: List[Dict],
        account_info: Dict,
        positions: List[Dict],
    ) -> List[Dict]:
        """将模拟下单结果持久化到 MongoDB，同时更新模拟持仓快照

        Args:
            user_id: 用户ID
            task_id: AI交易任务ID
            orders: 模拟下单结果列表
            account_info: 账户信息 {cash, total_value, frozen_cash}
            positions: 当前持仓列表 [{code, name, volume, cost_price, current_price}]

        Returns:
            持久化后的订单列表（带 _id）
        """
        db = get_mongo_db()
        saved_orders = []

        for order in orders:
            doc = {
                "user_id": user_id,
                "task_id": task_id,
                "mode": "paper",
                "code": order.get("code", ""),
                "name": order.get("name", ""),
                "action": order.get("action", ""),
                "price": order.get("price", 0),
                "volume": order.get("volume", 0),
                "amount": order.get("amount", 0),
                "simulated_cost": order.get("simulated_cost", 0),
                "order_id": order.get("order_id", ""),
                "success": order.get("success", False),
                "error": order.get("error"),
                "created_at": _now_cn(),
            }
            result = await db.ai_trading_orders.insert_one(doc)
            doc["_id"] = str(result.inserted_id)
            saved_orders.append(doc)

        # 更新模拟持仓快照
        if orders:
            await self._update_paper_portfolio_snapshot(
                user_id, task_id, orders, account_info, positions
            )

        return saved_orders

    async def _update_paper_portfolio_snapshot(
        self,
        user_id: str,
        task_id: str,
        orders: List[Dict],
        account_info: Dict,
        positions: List[Dict],
    ):
        """根据订单更新模拟持仓快照"""
        db = get_mongo_db()

        # 获取当前快照
        snapshot = await db.ai_trading_portfolio_snapshots.find_one(
            {"user_id": user_id, "mode": "paper"}
        )

        if snapshot:
            current_holdings = snapshot.get("holdings", {})
            cash = snapshot.get("cash", account_info.get("cash", 0))
            total_value = snapshot.get("total_value", account_info.get("total_value", 0))
        else:
            # 首次：从初始账户信息构建
            current_holdings = {}
            for p in positions:
                current_holdings[p["code"]] = {
                    "name": p.get("name", ""),
                    "volume": p.get("volume", 0),
                    "cost_price": p.get("cost_price", 0),
                    "current_price": p.get("current_price", 0),
                }
            cash = account_info.get("cash", 0)
            total_value = account_info.get("total_value", 0)

        # 应用订单
        for order in orders:
            if not order.get("success"):
                continue

            code = order.get("code", "")
            action = order.get("action", "")
            price = order.get("price", 0)
            volume = order.get("volume", 0)
            amount = order.get("amount", 0)

            if action == "买入":
                cost = price * volume if price and volume else amount
                commission = max(cost * 0.00025, 5.0) if cost else 0
                transfer_fee = cost * 0.00001 if cost else 0
                total_cost = cost + commission + transfer_fee

                cash -= total_cost

                if code in current_holdings:
                    h = current_holdings[code]
                    old_total = h["cost_price"] * h["volume"]
                    new_total = price * volume if price and volume else amount
                    h["volume"] += volume
                    h["cost_price"] = (old_total + new_total) / h["volume"] if h["volume"] > 0 else 0
                    h["current_price"] = price
                else:
                    current_holdings[code] = {
                        "name": order.get("name", ""),
                        "volume": volume,
                        "cost_price": price,
                        "current_price": price,
                    }

            elif action == "卖出":
                trade_amount = price * volume if price and volume else 0
                commission = max(trade_amount * 0.00025, 5.0) if trade_amount else 0
                stamp_tax = trade_amount * 0.001 if trade_amount else 0
                transfer_fee = trade_amount * 0.00001 if trade_amount else 0
                net_income = trade_amount - commission - stamp_tax - transfer_fee

                cash += net_income

                if code in current_holdings:
                    current_holdings[code]["volume"] -= volume
                    current_holdings[code]["current_price"] = price
                    if current_holdings[code]["volume"] <= 0:
                        del current_holdings[code]

        # 计算新的总资产
        holdings_value = sum(
            h.get("current_price", 0) * h.get("volume", 0)
            for h in current_holdings.values()
        )
        total_value = cash + holdings_value

        # 保存快照
        now = _now_cn()
        await db.ai_trading_portfolio_snapshots.update_one(
            {"user_id": user_id, "mode": "paper"},
            {
                "$set": {
                    "user_id": user_id,
                    "mode": "paper",
                    "task_id": task_id,
                    "cash": cash,
                    "total_value": total_value,
                    "holdings": current_holdings,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "initial_capital": total_value,
                    "created_at": now,
                },
            },
            upsert=True,
        )

    # ── 持仓收益查询 ──────────────────────────────────────

    async def get_portfolio(self, user_id: str, mode: str = "paper") -> Dict[str, Any]:
        """获取持仓收益概览

        Returns:
            {
                mode, cash, total_value, initial_capital,
                total_return, total_return_pct,
                holdings: [{code, name, volume, cost_price, current_price, market_value, pnl, pnl_pct}],
                daily_returns: [{date, return_pct}],
                sharpe_ratio, max_drawdown, win_rate,
                recent_orders: [...]
            }
        """
        db = get_mongo_db()

        if mode == "paper":
            snapshot = await db.ai_trading_portfolio_snapshots.find_one(
                {"user_id": user_id, "mode": "paper"}
            )
        else:
            # 实盘：从QMT获取真实持仓
            qmt = MockQMTUtil()
            with qmt:
                account_info = qmt.get_account_info()
                positions = qmt.get_positions()

            holdings = {}
            for p in positions:
                holdings[p.code] = {
                    "name": p.name,
                    "volume": p.volume,
                    "cost_price": p.cost_price,
                    "current_price": p.current_price,
                }

            snapshot = {
                "cash": account_info.cash,
                "total_value": account_info.total_value,
                "initial_capital": account_info.total_value,
                "holdings": holdings,
                "created_at": _now_cn(),
                "updated_at": _now_cn(),
            }

        if not snapshot:
            return {
                "mode": mode,
                "cash": 0,
                "total_value": 0,
                "initial_capital": 0,
                "total_return": 0,
                "total_return_pct": 0,
                "holdings": [],
                "daily_returns": [],
                "sharpe_ratio": 0,
                "max_drawdown": 0,
                "max_drawdown_pct": 0,
                "win_rate": 0,
                "recent_orders": [],
                "has_data": False,
            }

        cash = snapshot.get("cash", 0)
        total_value = snapshot.get("total_value", 0)
        initial_capital = snapshot.get("initial_capital", total_value)
        raw_holdings = snapshot.get("holdings", {})

        # 构建持仓列表
        holdings_list = []
        for code, h in raw_holdings.items():
            vol = h.get("volume", 0)
            cost = h.get("cost_price", 0)
            cur = h.get("current_price", 0)
            market_value = cur * vol
            pnl = (cur - cost) * vol
            pnl_pct = ((cur - cost) / cost * 100) if cost > 0 else 0
            holdings_list.append({
                "code": code,
                "name": h.get("name", ""),
                "volume": vol,
                "cost_price": round(cost, 2),
                "current_price": round(cur, 2),
                "market_value": round(market_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
            })

        total_return = total_value - initial_capital
        total_return_pct = (total_return / initial_capital * 100) if initial_capital > 0 else 0

        # 获取每日收益（从快照历史中计算）
        daily_returns = await self._get_daily_returns(user_id, mode)

        # 计算夏普比率（基于每日收益）
        sharpe_ratio = self._calc_sharpe_ratio(daily_returns)

        # 计算最大回撤
        max_drawdown, max_drawdown_pct = await self._calc_max_drawdown(user_id, mode, initial_capital)

        # 计算胜率
        win_rate = await self._calc_win_rate(user_id, mode)

        # 获取最近订单
        recent_orders = await self._get_recent_orders(user_id, mode, limit=20)

        return {
            "mode": mode,
            "cash": round(cash, 2),
            "total_value": round(total_value, 2),
            "initial_capital": round(initial_capital, 2),
            "total_return": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),
            "holdings": holdings_list,
            "daily_returns": daily_returns,
            "sharpe_ratio": round(sharpe_ratio, 2),
            "max_drawdown": round(max_drawdown, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "win_rate": round(win_rate, 2),
            "recent_orders": recent_orders,
            "has_data": True,
        }

    async def _get_daily_returns(self, user_id: str, mode: str) -> List[Dict]:
        """从订单历史推算每日收益率"""
        db = get_mongo_db()

        pipeline = [
            {"$match": {"user_id": user_id, "mode": mode, "success": True}},
            {"$sort": {"created_at": 1}},
            {"$group": {
                "_id": {
                    "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}
                },
                "net_amount": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$action", "买入"]},
                            {"$subtract": [
                                {"$ifNull": ["$amount", 0]},
                                {"$ifNull": ["$simulated_cost", 0]}
                            ]},
                            {"$add": [
                                {"$multiply": [
                                    {"$ifNull": ["$price", 0]},
                                    {"$ifNull": ["$volume", 0]}
                                ]},
                                {"$multiply": [-1, {"$ifNull": ["$simulated_cost", 0]}]}
                            ]}
                        ]
                    }
                },
                "trade_count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]

        cursor = db.ai_trading_orders.aggregate(pipeline)
        results = await cursor.to_list(length=90)

        daily_returns = []
        for r in results:
            daily_returns.append({
                "date": r["_id"],
                "net_amount": round(r.get("net_amount", 0), 2),
                "trade_count": r.get("trade_count", 0),
            })

        return daily_returns

    async def _get_recent_orders(
        self, user_id: str, mode: str, limit: int = 20
    ) -> List[Dict]:
        """获取最近交易订单"""
        db = get_mongo_db()

        cursor = db.ai_trading_orders.find(
            {"user_id": user_id, "mode": mode, "success": True}
        ).sort("created_at", -1).limit(limit)

        orders = []
        async for doc in cursor:
            orders.append({
                "code": doc.get("code", ""),
                "name": doc.get("name", ""),
                "action": doc.get("action", ""),
                "price": doc.get("price", 0),
                "volume": doc.get("volume", 0),
                "amount": doc.get("amount", 0),
                "simulated_cost": doc.get("simulated_cost", 0),
                "created_at": doc.get("created_at", "").isoformat()
                if isinstance(doc.get("created_at"), datetime)
                else str(doc.get("created_at", "")),
            })

        return orders

    async def _calc_win_rate(self, user_id: str, mode: str) -> float:
        """计算胜率：已平仓交易中盈利笔数 / 总平仓笔数"""
        db = get_mongo_db()

        # 找所有卖出订单
        sell_orders = []
        cursor = db.ai_trading_orders.find(
            {"user_id": user_id, "mode": mode, "action": "卖出", "success": True}
        )
        async for doc in cursor:
            sell_orders.append(doc)

        if not sell_orders:
            return 0.0

        # 获取对应持仓快照来计算成本
        snapshot = await db.ai_trading_portfolio_snapshots.find_one(
            {"user_id": user_id, "mode": mode}
        )
        current_holdings = snapshot.get("holdings", {}) if snapshot else {}

        win_count = 0
        for order in sell_orders:
            code = order.get("code", "")
            sell_price = order.get("price", 0)
            h = current_holdings.get(code, {})
            cost_price = h.get("cost_price", 0)
            if cost_price > 0 and sell_price > cost_price:
                win_count += 1

        return (win_count / len(sell_orders)) * 100 if sell_orders else 0.0

    def _calc_sharpe_ratio(self, daily_returns: List[Dict]) -> float:
        """计算年化夏普比率（无风险利率取3%）"""
        if len(daily_returns) < 2:
            return 0.0

        returns = [r.get("net_amount", 0) for r in daily_returns]
        if not returns:
            return 0.0

        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance) if variance > 0 else 0

        if std_dev == 0:
            return 0.0

        # 年化：假设252个交易日，无风险利率3%
        risk_free_daily = 0.03 / 252
        sharpe = (avg_return - risk_free_daily) / std_dev * math.sqrt(252)
        return sharpe

    async def _calc_max_drawdown(
        self, user_id: str, mode: str, initial_capital: float
    ) -> tuple:
        """计算最大回撤"""
        db = get_mongo_db()

        # 从快照历史获取总资产序列
        snapshot = await db.ai_trading_portfolio_snapshots.find_one(
            {"user_id": user_id, "mode": mode}
        )
        if not snapshot:
            return 0.0, 0.0

        # 用当前总资产和初始资金简单估算
        # 更精确的方式需要每日净值快照，这里基于可用数据估算
        total_value = snapshot.get("total_value", initial_capital)
        peak = max(initial_capital, total_value)
        if peak == 0:
            return 0.0, 0.0

        drawdown = peak - total_value
        drawdown_pct = (drawdown / peak) * 100

        return drawdown, drawdown_pct

    async def get_portfolio_history(
        self,
        user_id: str,
        mode: str = "paper",
        days: int = 30,
    ) -> Dict[str, Any]:
        """获取持仓历史净值曲线数据

        Returns:
            {
                mode,
                nav_curve: [{date, nav, return_pct}],
                trade_calendar: [{date, action, code, name, amount}]
            }
        """
        db = get_mongo_db()

        snapshot = await db.ai_trading_portfolio_snapshots.find_one(
            {"user_id": user_id, "mode": mode}
        )

        if not snapshot:
            return {
                "mode": mode,
                "nav_curve": [],
                "trade_calendar": [],
                "has_data": False,
            }

        initial_capital = snapshot.get("initial_capital", snapshot.get("total_value", 0))
        current_total = snapshot.get("total_value", 0)

        # 构建净值曲线（基于订单时间点）
        start_date = _now_cn() - timedelta(days=days)
        pipeline = [
            {"$match": {
                "user_id": user_id,
                "mode": mode,
                "success": True,
                "created_at": {"$gte": start_date},
            }},
            {"$sort": {"created_at": 1}},
            {"$group": {
                "_id": {
                    "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}
                },
                "buy_amount": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$action", "买入"]},
                            {"$ifNull": ["$amount", 0]},
                            0
                        ]
                    }
                },
                "sell_amount": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$action", "卖出"]},
                            {"$multiply": [
                                {"$ifNull": ["$price", 0]},
                                {"$ifNull": ["$volume", 0]}
                            ]},
                            0
                        ]
                    }
                },
                "trades": {
                    "$push": {
                        "action": "$action",
                        "code": "$code",
                        "name": "$name",
                        "amount": {"$ifNull": ["$amount", 0]},
                    }
                },
            }},
            {"$sort": {"_id": 1}},
        ]

        cursor = db.ai_trading_orders.aggregate(pipeline)
        nav_curve = []
        trade_calendar = []
        running_nav = initial_capital

        # 初始净值点
        nav_curve.append({
            "date": start_date.strftime("%Y-%m-%d"),
            "nav": round(initial_capital, 2),
            "return_pct": 0,
        })

        async for doc in cursor:
            net_flow = doc.get("sell_amount", 0) - doc.get("buy_amount", 0)
            running_nav += net_flow
            return_pct = ((running_nav - initial_capital) / initial_capital * 100) if initial_capital > 0 else 0

            nav_curve.append({
                "date": doc["_id"],
                "nav": round(running_nav, 2),
                "return_pct": round(return_pct, 2),
            })

            for t in doc.get("trades", []):
                trade_calendar.append({
                    "date": doc["_id"],
                    "action": t.get("action", ""),
                    "code": t.get("code", ""),
                    "name": t.get("name", ""),
                    "amount": t.get("amount", 0),
                })

        # 当前净值点
        if nav_curve and nav_curve[-1]["date"] != _now_cn().strftime("%Y-%m-%d"):
            cur_return_pct = ((current_total - initial_capital) / initial_capital * 100) if initial_capital > 0 else 0
            nav_curve.append({
                "date": _now_cn().strftime("%Y-%m-%d"),
                "nav": round(current_total, 2),
                "return_pct": round(cur_return_pct, 2),
            })

        return {
            "mode": mode,
            "nav_curve": nav_curve,
            "trade_calendar": trade_calendar,
            "has_data": True,
        }

    async def init_paper_portfolio(
        self, user_id: str, initial_capital: float = 1000000.0
    ) -> Dict[str, Any]:
        """初始化模拟账户（重置或创建）

        Args:
            user_id: 用户ID
            initial_capital: 初始资金（默认100万）

        Returns:
            初始化后的快照信息
        """
        db = get_mongo_db()
        now = _now_cn()

        # 清空该用户所有模拟订单记录
        await db.ai_trading_orders.delete_many(
            {"user_id": user_id, "mode": "paper"}
        )

        # 重置模拟持仓快照
        await db.ai_trading_portfolio_snapshots.update_one(
            {"user_id": user_id, "mode": "paper"},
            {
                "$set": {
                    "user_id": user_id,
                    "mode": "paper",
                    "cash": initial_capital,
                    "total_value": initial_capital,
                    "initial_capital": initial_capital,
                    "holdings": {},
                    "created_at": now,
                    "updated_at": now,
                },
            },
            upsert=True,
        )

        return {
            "user_id": user_id,
            "mode": "paper",
            "initial_capital": initial_capital,
            "message": "模拟账户已重置",
        }


# 单例
_portfolio_service: Optional[PortfolioService] = None
_portfolio_service_lock = threading.Lock()


def get_portfolio_service() -> PortfolioService:
    global _portfolio_service
    if _portfolio_service is None:
        with _portfolio_service_lock:
            if _portfolio_service is None:
                _portfolio_service = PortfolioService()
    return _portfolio_service
