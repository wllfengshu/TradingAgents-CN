"""
AI选股服务
6个分析师Agent协同工作：大盘分析师、主线板块分析师、市场合力分析师、
股票龙头分析师、风险分析师、决策分析师
每个Agent先使用代码计算指标，然后将计算结果发送给LLM分析
"""

import asyncio
import uuid
import json
import logging
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List
from zoneinfo import ZoneInfo

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.utils.json_compressor import compress_json_for_llm

from langchain_core.messages import HumanMessage

from tradingagents.graph.trading_graph import TradingAgentsGraph
from app.services.simple_analysis_service import (
    create_analysis_config,
    get_provider_and_url_by_model_sync,
)
from app.core.database import get_mongo_db
from ..utils.stock_utils import is_main_board_stock

logger = logging.getLogger("app.services.ai_selector_service")


class ApiCache:
    """API调用缓存，在单次AI选股运行期间缓存akshare接口调用结果，运行结束后清空

    类似Java的ThreadLocal效果：每次AI选股运行时创建，运行期间同一接口只调用一次，
    运行结束后清空缓存，不同任务的缓存互不干扰。
    线程安全：使用 Lock 防止多线程并发时的重复调用和竞态问题。
    """

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()  # Fix: 保证线程安全

    def call(self, cache_key: str, func, *args, **kwargs):
        """调用API并缓存结果，如果已有缓存则直接返回（线程安全）"""
        with self._lock:
            if cache_key in self._cache:
                self._hits += 1
                logger.info(f"API缓存命中: {cache_key}")
                return self._cache[cache_key]
            # 在锁内调用 API，防止并发时同一 key 被重复请求（TOCTOU）
            result = func(*args, **kwargs)
            self._cache[cache_key] = result
            self._misses += 1

        # 日志记录在锁外，避免长时间持锁
        try:
            import pandas as pd
            if isinstance(result, pd.DataFrame):
                logger.info(
                    f"[底层接口数据] {cache_key} -> "
                    f"shape={result.shape}, columns={list(result.columns)}\n"
                    f"{result.head(5).to_string(index=False)}"
                )
            else:
                logger.info(f"[底层接口数据] {cache_key} -> {result}")
        except Exception as _log_err:
            logger.error(f"[底层接口数据] {cache_key} -> 日志记录失败: {_log_err}")
        return result

    def clear(self):
        if self._hits > 0 or self._misses > 0:
            logger.info(f"API缓存清空，命中{self._hits}次，未命中{self._misses}次")
        self._cache.clear()
        self._hits = 0
        self._misses = 0


# ============================================================
# 指标计算函数
# ============================================================

def compute_market_indicators(api_cache: ApiCache) -> Dict[str, Any]:
    """大盘分析师指标计算：指数/北向资金/涨跌比等"""
    try:
        import akshare as ak

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 添加非交易时段提示，防止 LLM 误判数据时效
        try:
            from datetime import time as _dtime
            _now_sh = datetime.now(ZoneInfo("Asia/Shanghai"))
            _weekday = _now_sh.weekday()
            _t = _now_sh.time()
            _is_trading = (
                _weekday < 5 and (
                    _dtime(9, 15) <= _t <= _dtime(11, 30) or
                    _dtime(13, 0) <= _t <= _dtime(15, 30)
                )
            )
            if not _is_trading:
                _reason = "周末" if _weekday >= 5 else f"非交易时段（{_now_sh.strftime('%H:%M')}）"
                result["数据时效提示"] = (
                    f"⚠️ 当前为{_reason}，以下行情数据为上一交易日快照，并非实时数据，"
                    f"请在 A 股交易时间（工作日 9:15-11:30 / 13:00-15:30）内运行以获取实时数据。"
                )
        except Exception:
            pass

        # 1. 上证指数（含5日趋势）
        try:
            sh_index = api_cache.call("stock_zh_index_daily:sh000001", ak.stock_zh_index_daily, symbol="sh000001")
            if sh_index is not None and not sh_index.empty:
                latest = sh_index.iloc[-1]
                prev = sh_index.iloc[-2] if len(sh_index) > 1 else latest
                # Fix: base5 取 5 日前的收盘价（iloc[-6]），用于计算 5 日涨跌幅（5 个区间）
                # 展示用的 close5 则取最近 5 日（iloc[-5:]），共 5 个数据点，与名称一致
                base5_row = sh_index.iloc[-6] if len(sh_index) >= 6 else sh_index.iloc[0]
                base5 = float(base5_row["close"])
                change5d = round((float(latest["close"]) - base5) / base5 * 100, 2)
                recent5 = sh_index.iloc[-5:] if len(sh_index) >= 5 else sh_index
                close5 = [round(float(v), 2) for v in recent5["close"].tolist()]
                result["上证指数"] = {
                    "收盘价": float(latest["close"]),
                    "今日涨跌幅(%)": round((float(latest["close"]) - float(prev["close"])) / float(prev["close"]) * 100, 2),
                    "5日涨跌幅(%)": change5d,
                    "近5日收盘价(从旧到新)": close5,
                    "成交量": int(latest["volume"]) if "volume" in latest else None,
                }
        except Exception as e:
            logger.error(f"获取上证指数失败: {e}")
            result["上证指数"] = "获取失败"

        # 2. 深证成指（含5日趋势）
        try:
            sz_index = api_cache.call("stock_zh_index_daily:sz399001", ak.stock_zh_index_daily, symbol="sz399001")
            if sz_index is not None and not sz_index.empty:
                latest = sz_index.iloc[-1]
                prev = sz_index.iloc[-2] if len(sz_index) > 1 else latest
                # Fix: 同上证逻辑，base5 与展示数据分开
                base5_row = sz_index.iloc[-6] if len(sz_index) >= 6 else sz_index.iloc[0]
                base5 = float(base5_row["close"])
                change5d = round((float(latest["close"]) - base5) / base5 * 100, 2)
                recent5 = sz_index.iloc[-5:] if len(sz_index) >= 5 else sz_index
                close5 = [round(float(v), 2) for v in recent5["close"].tolist()]
                result["深证成指"] = {
                    "收盘价": float(latest["close"]),
                    "今日涨跌幅(%)": round((float(latest["close"]) - float(prev["close"])) / float(prev["close"]) * 100, 2),
                    "5日涨跌幅(%)": change5d,
                    "近5日收盘价(从旧到新)": close5,
                }
        except Exception as e:
            logger.error(f"获取深证成指失败: {e}")
            result["深证成指"] = "获取失败"

        # 3. 沪深港通资金（替代已停止公布的北向资金净流入数据）
        # 3a. 沪深港通每日资金流向汇总
        try:
            hsgt_summary = api_cache.call("stock_hsgt_fund_flow_summary_em", ak.stock_hsgt_fund_flow_summary_em)
            if hsgt_summary is not None and not hsgt_summary.empty:
                hsgt_data = {}
                for _, row in hsgt_summary.iterrows():
                    block_name = str(row["板块"]) if "板块" in hsgt_summary.columns else ""
                    direction = str(row["资金方向"]) if "资金方向" in hsgt_summary.columns else ""
                    net_buy = row["成交净买额"] if "成交净买额" in hsgt_summary.columns else None
                    fund_inflow = row["资金净流入"] if "资金净流入" in hsgt_summary.columns else None
                    up_count = row["上涨数"] if "上涨数" in hsgt_summary.columns else None
                    down_count = row["下跌数"] if "下跌数" in hsgt_summary.columns else None
                    related_index = str(row["相关指数"]) if "相关指数" in hsgt_summary.columns else ""
                    index_change = row["指数涨跌幅"] if "指数涨跌幅" in hsgt_summary.columns else None

                    import pandas as pd
                    entry = {}
                    if pd.notna(net_buy):
                        entry["成交净买额(亿)"] = round(float(net_buy), 2)
                    if pd.notna(fund_inflow):
                        entry["资金净流入(亿)"] = round(float(fund_inflow), 2)
                    if pd.notna(up_count):
                        entry["上涨数"] = int(float(up_count))
                    if pd.notna(down_count):
                        entry["下跌数"] = int(float(down_count))
                    if related_index:
                        entry["相关指数"] = related_index
                    if pd.notna(index_change):
                        entry["指数涨跌幅(%)"] = float(index_change)

                    hsgt_data[f"{block_name}({direction})"] = entry

                hsgt_data["说明"] = "沪深港通资金流向汇总，北向成交净买额已停止公布，改用北向资金ETF成交额作为替代指标"
                result["沪深港通成交"] = hsgt_data
        except Exception as e:
            logger.error(f"获取沪深港通成交数据失败: {e}")
            result["沪深港通成交"] = "获取失败"

        # 3a2. 北向资金ETF成交额（替代已停止公布的北向成交净买额）
        # 使用A50/MSCI/互联互通相关ETF的成交额之和，作为外资参与活跃度的替代指标
        try:
            etf_spot = api_cache.call("fund_etf_spot_em", ak.fund_etf_spot_em)
            if etf_spot is not None and not etf_spot.empty:
                # 筛选北向资金相关ETF：A50、MSCI、互联互通等追踪外资偏好的ETF
                north_etf = etf_spot[etf_spot["名称"].str.contains("A50|MSCI|互联互通|陆股通", na=False)]
                if not north_etf.empty:
                    total_amount = north_etf["成交额"].sum()
                    total_amount_yi = round(total_amount / 1e8, 2)
                    # 按涨跌幅加权平均，反映北向ETF整体涨跌情况
                    avg_change = round(north_etf["涨跌幅"].mean(), 2)
                    etf_count = len(north_etf)
                    if isinstance(result.get("沪深港通成交"), dict):
                        result["沪深港通成交"]["北向资金ETF成交额(亿)"] = total_amount_yi
                        result["沪深港通成交"]["北向资金ETF平均涨跌幅(%)"] = avg_change
                        result["沪深港通成交"]["北向资金ETF数量"] = etf_count
                        result["沪深港通成交"]["说明"] = (
                            "北向成交净买额已停止公布，以A50/MSCI/互联互通ETF成交额作为外资活跃度替代指标。"
                            "ETF成交额越大表示外资参与度越高。"
                        )
        except Exception as e:
            logger.error(f"获取北向资金ETF成交额失败: {e}")

        # 3b. 北向资金增持个股排行前10（替代原沪股通活跃股）
        try:
            hold_rank = api_cache.call("stock_hsgt_hold_stock_em:北向:今日排行", ak.stock_hsgt_hold_stock_em, market="北向", indicator="今日排行")
            if hold_rank is not None and not hold_rank.empty:
                top10 = hold_rank.head(10)
                result["沪深港通活跃股前10"] = [
                    {
                        "排名": str(row["序号"]) if "序号" in hold_rank.columns else str(i + 1),
                        "代码": str(row["代码"]) if "代码" in hold_rank.columns else "",
                        "名称": str(row["名称"]) if "名称" in hold_rank.columns else "",
                        "涨跌幅(%)": str(row["今日涨跌幅"]) if "今日涨跌幅" in hold_rank.columns else "",
                        "增持市值(万)": str(row["今日增持估计-市值"]) if "今日增持估计-市值" in hold_rank.columns else "",
                        "持股市值(万)": str(row["今日持股-市值"]) if "今日持股-市值" in hold_rank.columns else "",
                        "所属行业": str(row["所属板块"]) if "所属板块" in hold_rank.columns else "",
                    }
                    for i, (_, row) in enumerate(top10.iterrows())
                ]
        except Exception as e:
            logger.error(f"获取沪深港通活跃股失败: {e}")
            result["沪深港通活跃股前10"] = "获取失败"

        # 3c. 行业资金成交集中度（使用同花顺行业资金流数据）
        try:
            industry_flow = api_cache.call("stock_fund_flow_industry:即时", ak.stock_fund_flow_industry, symbol="即时")
            if industry_flow is not None and not industry_flow.empty:
                top8 = industry_flow.head(8)
                result["沪深港通行业成交集中度"] = [
                    {
                        "行业": str(row["行业"]) if "行业" in industry_flow.columns else "",
                        "行业涨跌幅(%)": str(row["行业-涨跌幅"]) if "行业-涨跌幅" in industry_flow.columns else "",
                        "净额(亿)": str(row["净额"]) if "净额" in industry_flow.columns else "",
                        "流入资金(亿)": str(row["流入资金"]) if "流入资金" in industry_flow.columns else "",
                        "领涨股": str(row["领涨股"]) if "领涨股" in industry_flow.columns else "",
                    }
                    for _, row in top8.iterrows()
                ]
        except Exception as e:
            logger.error(f"获取行业资金分布失败: {e}")
            result["沪深港通行业成交集中度"] = "获取失败"

        # 4. 涨跌比（使用新浪数据源，东方财富接口被限制）
        try:
            stock_changes = api_cache.call("stock_zh_a_spot", ak.stock_zh_a_spot)
            if stock_changes is not None and not stock_changes.empty:
                up_count = len(stock_changes[stock_changes["涨跌幅"] > 0])
                down_count = len(stock_changes[stock_changes["涨跌幅"] < 0])
                flat_count = len(stock_changes[stock_changes["涨跌幅"] == 0])
                total = up_count + down_count + flat_count
                result["涨跌统计"] = {
                    "上涨": up_count,
                    "下跌": down_count,
                    "平盘": flat_count,
                    "涨跌比": round(up_count / max(down_count, 1), 2),
                    "上涨占比": round(up_count / max(total, 1) * 100, 2),
                }
        except Exception as e:
            logger.error(f"获取涨跌统计失败: {e}")
            result["涨跌统计"] = "获取失败"

        return result

    except ImportError:
        logger.error("akshare未安装，返回空指标")
        return {"提示": "akshare未安装，无法获取实时数据"}
    except Exception as e:
        logger.error(f"计算大盘指标失败: {e}")
        return {"错误": str(e)}


def compute_sector_indicators(api_cache: ApiCache) -> Dict[str, Any]:
    """主线板块分析师指标计算：涨停集中度/5日强度等"""
    try:
        import akshare as ak

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 1. 板块涨跌幅排行（使用同花顺数据源，东方财富接口被限制）
        # 尽量提取所有列，包含5日/10日涨跌幅
        try:
            import pandas as pd
            sector_rank = api_cache.call("stock_board_industry_summary_ths", ak.stock_board_industry_summary_ths)
            if sector_rank is not None and not sector_rank.empty:
                top_sectors = sector_rank.nlargest(10, "涨跌幅") if "涨跌幅" in sector_rank.columns else sector_rank.head(10)
                result["涨幅前10板块"] = []
                for _, row in top_sectors.iterrows():
                    item = {}
                    for col in sector_rank.columns:
                        if col in ["序号"]:
                            continue
                        val = row[col]
                        item[col] = round(float(val), 2) if pd.notna(val) and isinstance(val, (int, float)) else (str(val) if pd.notna(val) else "")
                    result["涨幅前10板块"].append(item)
        except Exception as e:
            logger.error(f"获取板块排行失败: {e}")
            result["涨幅前10板块"] = "获取失败"

        # 2. 涨停股统计（保留全部字段，尤其是连板数、所属行业，供LLM分析涨停集中度）
        try:
            import pandas as pd
            today_str = datetime.now().strftime("%Y%m%d")
            zt_stocks = api_cache.call(f"stock_zt_pool_em:{today_str}", ak.stock_zt_pool_em, date=today_str)
            if zt_stocks is not None and not zt_stocks.empty:
                result["涨停统计"] = {
                    "涨停数量": len(zt_stocks),
                    "涨停股列表": [
                        {col: str(row[col]) for col in zt_stocks.columns if col != "序号"}
                        for _, row in zt_stocks.head(30).iterrows()
                    ] if len(zt_stocks) > 0 else [],
                }
        except Exception as e:
            logger.error(f"获取涨停统计失败: {e}")
            result["涨停统计"] = "获取失败"

        # 3. 连板股统计（强势股池：连续涨停2板及以上的股票，保留全字段）
        try:
            import pandas as pd
            today_str = datetime.now().strftime("%Y%m%d")
            lb_stocks = api_cache.call(f"stock_zt_pool_strong_em:{today_str}", ak.stock_zt_pool_strong_em, date=today_str)
            if lb_stocks is not None and not lb_stocks.empty:
                # 过滤掉成交额为0的停牌股票
                if "成交额" in lb_stocks.columns:
                    lb_stocks = lb_stocks[lb_stocks["成交额"].astype(float) > 0]
                lb_stocks_length = len(lb_stocks)
                max_len = 50 if lb_stocks_length > 50 else lb_stocks_length
                result["强势股池统计"] = {
                    "强势股池数量": lb_stocks_length,
                    "强势股池股列表(前50)": [
                        {col: str(row[col]) for col in lb_stocks.columns if col != "序号"}
                        for _, row in lb_stocks.head(max_len).iterrows()
                    ] if lb_stocks_length > 0 else [],
                }
        except Exception as e:
            logger.error(f"获取连板统计失败: {e}")
            result["强势股池统计"] = "获取失败"

        return result

    except ImportError:
        return {"提示": "akshare未安装，无法获取实时数据"}
    except Exception as e:
        logger.error(f"计算板块指标失败: {e}")
        return {"错误": str(e)}


def compute_force_indicators(api_cache: ApiCache) -> Dict[str, Any]:
    """市场合力分析师指标计算：主力+散户双向净流入等"""
    try:
        import akshare as ak

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 1. 行业资金流向（使用同花顺行业资金流，替代东方财富接口）
        try:
            industry_fund = api_cache.call("stock_fund_flow_industry:即时", ak.stock_fund_flow_industry, symbol="即时")
            if industry_fund is not None and not industry_fund.empty:
                result["主力资金流向前20"] = []
                for _, row in industry_fund.head(20).iterrows():
                    item = {}
                    if "行业" in industry_fund.columns:
                        item["行业"] = str(row["行业"])
                    if "行业-涨跌幅" in industry_fund.columns:
                        item["涨跌幅(%)"] = str(row["行业-涨跌幅"])
                    if "净额" in industry_fund.columns:
                        item["净额(亿)"] = str(row["净额"])
                    if "流入资金" in industry_fund.columns:
                        item["流入资金(亿)"] = str(row["流入资金"])
                    if "流出资金" in industry_fund.columns:
                        item["流出资金(亿)"] = str(row["流出资金"])
                    if "领涨股" in industry_fund.columns:
                        item["领涨股"] = str(row["领涨股"])
                    result["主力资金流向前20"].append(item)
        except Exception as e:
            logger.error(f"获取主力资金流向失败: {e}")
            result["主力资金流向"] = "获取失败"

        # 2. 个股资金流向（使用同花顺个股资金流，替代东方财富接口）
        # 只保留主板股票，排除科创板(688)、创业板(300/301)、北交所(8开头)
        try:
            stock_fund = api_cache.call("stock_fund_flow_individual:即时", ak.stock_fund_flow_individual, symbol="即时")
            if stock_fund is not None and not stock_fund.empty:
                # 过滤：只保留主板股票
                if "股票代码" in stock_fund.columns:
                    stock_fund = stock_fund[stock_fund["股票代码"].astype(str).apply(is_main_board_stock)]
                    logger.info(f"个股资金流向过滤后剩余主板股票: {len(stock_fund)} 条")

                # 按净额降序排序，确保 top20 是资金净流入最多的主板股
                if "净额" in stock_fund.columns:
                    stock_fund = stock_fund.sort_values("净额", ascending=False)
                top_inflow = stock_fund.head(20)
                result["个股主力净流入前20"] = []
                for _, row in top_inflow.iterrows():
                    item = {}
                    if "股票代码" in stock_fund.columns:
                        item["代码"] = str(row["股票代码"])
                    if "股票简称" in stock_fund.columns:
                        item["名称"] = str(row["股票简称"])
                    if "涨跌幅" in stock_fund.columns:
                        item["涨跌幅"] = str(row["涨跌幅"])
                    if "净额" in stock_fund.columns:
                        item["净额"] = str(row["净额"])
                    if "流入资金" in stock_fund.columns:
                        item["流入资金"] = str(row["流入资金"])
                    if "流出资金" in stock_fund.columns:
                        item["流出资金"] = str(row["流出资金"])
                    if "换手率" in stock_fund.columns:
                        item["换手率"] = str(row["换手率"])
                    result["个股主力净流入前20"].append(item)
        except Exception as e:
            logger.error(f"获取个股资金流向失败: {e}")
            result["个股资金流向"] = "获取失败"

        return result

    except ImportError:
        return {"提示": "akshare未安装，无法获取实时数据"}
    except Exception as e:
        logger.error(f"计算合力指标失败: {e}")
        return {"错误": str(e)}


def compute_leader_indicators(api_cache: ApiCache) -> Dict[str, Any]:
    """股票龙头分析师指标计算：连板/板块排名/成交量等"""
    try:
        import akshare as ak

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 1. 涨停池（龙头候选）
        try:
            today_str = datetime.now().strftime("%Y%m%d")
            zt_pool = api_cache.call(f"stock_zt_pool_em:{today_str}", ak.stock_zt_pool_em, date=today_str)
            if zt_pool is not None and not zt_pool.empty:
                # 过滤掉成交额为0的停牌股票
                if "成交额" in zt_pool.columns:
                    zt_pool = zt_pool[zt_pool["成交额"].astype(float) > 0]
                zt_pool_length = len(zt_pool)
                max_len = 20 if zt_pool_length > 20 else zt_pool_length
                result["涨停龙头股前20"] = []
                for _, row in zt_pool.head(max_len).iterrows():
                    item = {}
                    for col in zt_pool.columns:
                        if col == "序号":
                            continue
                        item[col] = str(row[col])
                    result["涨停龙头股前20"].append(item)
        except Exception as e:
            logger.error(f"获取涨停池失败: {e}")
            result["涨停龙头股"] = "获取失败"

        # 2. 强势股（涨幅前20，使用新浪数据源，只保留主板）
        try:
            stock_rank = api_cache.call("stock_zh_a_spot", ak.stock_zh_a_spot)
            if stock_rank is not None and not stock_rank.empty:
                # 过滤主板，避免科创/创业/北交混入
                if "代码" in stock_rank.columns:
                    stock_rank = stock_rank[stock_rank["代码"].astype(str).apply(is_main_board_stock)]
                top_stocks = stock_rank.nlargest(20, "涨跌幅")
                result["强势股前20"] = []
                for _, row in top_stocks.iterrows():
                    item = {}
                    for col in ["代码", "名称", "最新价", "涨跌幅", "成交量", "成交额", "换手率"]:
                        if col in stock_rank.columns:
                            item[col] = str(row[col])
                    result["强势股前20"].append(item)
        except Exception as e:
            logger.error(f"获取强势股排行失败: {e}")
            result["强势股排行"] = "获取失败"

        return result

    except ImportError:
        return {"提示": "akshare未安装，无法获取实时数据"}
    except Exception as e:
        logger.error(f"计算龙头指标失败: {e}")
        return {"错误": str(e)}


def compute_risk_indicators(api_cache: ApiCache, candidate_stock_codes: List[str] = None) -> Dict[str, Any]:
    """风险分析师指标计算：排除ST/新股/退市等，并拉取候选股票实时行情"""
    try:
        import akshare as ak
        import pandas as pd

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 1. 次新股（使用新浪数据源，东方财富接口被限制）
        try:
            new_stocks = api_cache.call("stock_zh_a_new", ak.stock_zh_a_new)
            if new_stocks is not None and not new_stocks.empty:
                result["次新股"] = {
                    "数量": len(new_stocks),
                    "部分列表": [
                        {"代码": str(row["code"]) if "code" in new_stocks.columns else "",
                         "名称": str(row["name"]) if "name" in new_stocks.columns else ""}
                        for _, row in new_stocks.head(20).iterrows()
                    ],
                }
        except Exception as e:
            logger.error(f"获取次新股失败: {e}")
            result["次新股"] = "获取失败"

        # 2. 候选股票实时行情（让LLM能真正评估涨幅异常、流动性、ST等）
        if candidate_stock_codes:
            try:
                spot = api_cache.call("stock_zh_a_spot", ak.stock_zh_a_spot)
                if spot is not None and not spot.empty and "代码" in spot.columns:
                    target = spot[spot["代码"].isin(candidate_stock_codes)]
                    if not target.empty:
                        result["候选标的实时行情"] = [
                            {col: str(row[col]) for col in
                             ["代码", "名称", "最新价", "涨跌幅", "成交量", "成交额", "换手率", "振幅", "52周最高", "52周最低"]
                             if col in spot.columns}
                            for _, row in target.iterrows()
                        ]
                    else:
                        result["候选标的实时行情"] = f"未找到候选代码 {candidate_stock_codes} 的实时数据"
            except Exception as e:
                logger.error(f"获取候选标的实时行情失败: {e}")
                result["候选标的实时行情"] = "获取失败"

        # 3. ST / 退市 / 停牌 / 板块规则由 RISK_ANALYST_PROMPT 中的风险排查规则定义，
        # 此处不重复写入数据，保持数据层与提示层分离

        return result

    except ImportError:
        return {"提示": "akshare未安装，无法获取实时数据"}
    except Exception as e:
        logger.error(f"计算风险指标失败: {e}")
        return {"错误": str(e)}


# ============================================================
# Agent提示词模板
# ============================================================

MARKET_ANALYST_PROMPT = """你是一位资深的大盘分析师，专注于分析A股大盘走势与市场环境。

以下是通过代码计算获取的最新市场指标数据：

{indicators_data}

**数据说明**：
- "近5日收盘价(从旧到新)" 提供了上证/深证最近5个交易日的收盘序列，请据此判断趋势方向。
- "5日涨跌幅(%)" 为近5个交易日的累计涨跌幅，正值表示上涨趋势，负值表示下跌趋势。

请基于上述数据，从以下维度进行分析：

1. **指数走势判断**：结合近5日收盘价序列和5日涨跌幅，判断上证/深成当前处于上涨、下跌还是震荡趋势？幅度如何？
2. **沪深港通资金分析**：注意北向成交净买额已停止公布（值为0），改用"北向资金ETF成交额(亿)"替代——越大说明外资参与度越高。南向资金仍可正常使用。重点关注外资增持方向和行业资金集中度。
3. **涨跌比分析**：上涨/下跌家数比值如何？是普涨（上涨占比>60%）、普跌（<40%）还是分化？
4. **综合判断**：综合指数趋势、资金方向、市场广度，给出偏多/偏空/中性结论。

请给出明确的大盘分析结论：
- 大盘环境：偏多/偏空/中性
- 核心依据（2-3条，引用具体数据）
- 重点关注的风险点

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "market_sentiment": "偏多/偏空/中性",
  "confidence": 0.8,
  "trend_5d": "上涨/下跌/震荡",
  "key_points": ["依据1（含数据）", "依据2（含数据）"]
}}
```

使用中文输出，简洁专业。"""

SECTOR_ANALYST_PROMPT = """你是一位资深的主线板块分析师，专注于识别当前市场的热点主线板块。

## 上游大盘分析师结论
{market_summary}

以下是通过代码计算获取的最新板块指标数据：

{indicators_data}

**数据说明**：
- "涨幅前10板块" 包含今日涨跌幅，以及数据源提供的5日/10日涨跌幅（如有），请优先引用这些字段判断板块强度而非主观猜测。
- "涨停统计.涨停股列表" 包含各涨停股的所属行业/板块字段（如有），请统计哪些行业的涨停股最多，判断资金聚焦方向。
- "强势股池统计" 是连续涨停2板及以上的股票，连板数越高说明市场热度越集中。

请基于上述数据，从以下维度进行分析：

1. **板块涨停集中度**：统计涨停股中各行业/板块的数量，哪个方向涨停股最多？是否有明确的板块效应？
2. **板块持续强度**：若数据中包含5日/10日涨跌幅，对比今日与近期涨幅判断是启动还是持续；若无多日数据，说明仅做今日判断。
3. **连板高度**：当前市场连板最多的股票是几板？高度板的存在说明市场情绪如何？
4. **主线认定**：结合上述三个维度，哪个板块具备"量升、涨停集中、有高度连板股"的主线特征？

请给出明确的板块分析结论：
- 当前主线板块（1-2个，须有数据支撑）
- 核心逻辑（各板块走强的具体依据，引用数据）
- 持续性判断：持续/一日游，依据是什么？

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "has_main_sector": true,
  "main_sectors": ["板块1", "板块2"],
  "max_consecutive_limit": 3,
  "sustainability": "持续/一日游",
  "reasoning": "简要逻辑（含具体数据依据）"
}}
```
注意：只有当板块涨停数量明显集中（同一板块≥3支涨停）、或有高度连板（≥3板）时，才判断has_main_sector为true；否则设为false。

使用中文输出，简洁专业。"""

FORCE_ANALYST_PROMPT = """你是一位资深的市场合力分析师，专注于分析主力与散户的资金动向。

## 上游分析师传入的主线板块
主线板块分析师识别出以下主线板块：{sector_themes}
请重点围绕这些主线板块分析资金合力。

以下是通过代码计算获取的最新资金流向数据：

{indicators_data}

**重要约束**：
- 你只能从"个股主力净流入前20"列表中推荐股票，**严禁推荐列表中未出现的股票代码**。
- 如果该列表中没有属于主线板块的股票，则recommended_stocks返回空数组，不得凭空捏造。
- 推荐时必须引用列表中的"代码"字段，确保准确。

请基于上述数据，从以下维度进行分析：

1. **行业资金流向**：主力资金净流入哪些行业？净流出哪些行业？重点关注主线板块（{sector_themes}）的资金净额情况。
2. **个股资金交叉验证**：在"个股主力净流入前20"中，哪些股票属于主线板块？它们的净额、换手率、涨跌幅配合情况如何？
3. **合力判断**：行业资金与个股资金方向是否一致？是"正向共振"（行业+个股同向净流入）、"主力主导"（行业流入但个股分散）还是"反向分歧"？
4. **量价配合**：净流入的股票，其换手率是否同步放大？换手率>3%且净流入为正是强信号。

请给出明确的合力分析结论：
- 合力方向：正向共振/反向分歧/主力主导
- 资金最集中的方向（行业+具体股票）
- 需要警惕的资金信号
- **从"个股主力净流入前20"列表中，筛选出属于主线板块且净额最高的2到3支股票**

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "force_direction": "正向共振/反向分歧/主力主导",
  "recommended_stocks": [
    {{"code": "股票代码（必须来自数据列表）", "name": "股票名称", "net_inflow": "净流入金额", "reason": "属于主线板块且具体依据"}}
  ]
}}
```
注意：recommended_stocks中的股票代码必须完全匹配"个股主力净流入前20"中的代码；如列表中确无主线板块股票，返回空数组。

使用中文输出，简洁专业。"""

LEADER_ANALYST_PROMPT = """你是一位资深的股票龙头分析师，专注于筛选板块龙头与连板强势股。

## 上游分析师传入的候选股票
市场合力分析师筛选出以下候选股票（这些是你重点分析的对象）：
{candidate_stocks}

以下是通过代码计算获取的最新龙头指标数据（用于交叉验证候选股的龙头属性）：

{indicators_data}

**分析指引**：
- 优先在"涨停龙头股前20"中查找候选股票的代码，若出现则说明今日已涨停，重点关注其"连板数"字段——连板数越高龙头地位越稳。
- 同时在"强势股前20"中查找候选股，若出现说明今日涨幅居前且有成交量支撑。
- 若候选股在两个列表中均未出现，说明今日表现一般，需降低评级。
- "换手率"是流动性和市场关注度的重要指标，>5%为活跃标志。

请基于上述分析，从以下维度研判候选股：

1. **连板属性**：候选股中是否有连板股？连板数是多少？在当前市场连板高度中处于什么位置（龙头/跟风/尾部）？
2. **龙头地位**：该股今日是否涨停或涨幅居前？在所属板块中是领头羊还是跟风股？
3. **量价配合**：换手率与成交额是否同步放大？是否有"放量涨停"或"缩量跌停"等异常信号？
4. **板块共振**：候选股所属板块今日表现如何？龙头股应与板块形成共振，而非逆势。

请从候选股票中筛选出最多1到2支龙头股（宁缺毋滥）：
- 龙头股推荐及理由（必须引用数据中的具体字段值）
- 所属板块及该板块今日表现
- 龙头强度评级：强（连板≥3或涨停封板比高）、中（涨停但非连板）、弱（未涨停）

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "leading_stocks": [
    {{"code": "股票代码", "name": "股票名称", "sector": "所属板块", "consecutive_limit": 连板数或0, "strength": "强/中/弱", "in_zt_pool": true或false}}
  ]
}}
```
注意：leading_stocks只推荐能从数据中找到支撑的股票；如候选股全部表现平庸，leading_stocks返回空数组。

使用中文输出，简洁专业。"""

RISK_ANALYST_PROMPT = """你是一位资深的风险分析师，专注于排除高风险标的，保障投资安全边际。

## 强制风险排查规则（不可违背）
1. **ST / *ST**：名称含 ST 或 *ST 的股票，直接标记为高风险，必须排除，禁止买入。
2. **退市风险**：名称含"退市"或"退"字的股票，直接标记为高风险，必须排除。
3. **停牌股票**：名称含"停牌"或"停"字的股票，直接标记为高风险，必须排除。
4. **板块限制**：只允许买入沪深主板股票（代码以 600/601/603/605/000/001/002/003 开头），
   排除科创板（688）、创业板（300/301）、北交所（8 开头）。

以下是通过代码计算获取的最新风险指标数据：

{indicators_data}

**数据说明**：
- "候选标的实时行情" 包含上游龙头分析师推荐股票的实时价格、涨跌幅、成交量、换手率等，请基于这些**真实数据**评估涨幅异常和流动性风险，而非主观猜测。
- "次新股" 列表用于判断推荐标的是否为上市不满60个交易日的次新股。

同时，上游龙头分析师推荐了以下标的：
{recommended_stocks}

请逐一对每只推荐股进行如下风险核查：

1. **名称合规检查**：股票名称是否含 ST/*ST/退市/停牌？（对照候选标的实时行情中的"名称"字段）
2. **代码合规检查**：代码是否属于主板范围？（600/601/603/605/000/001/002/003开头为主板）
3. **次新股核查**：是否出现在次新股列表中？
4. **涨幅异常核查**：基于实时行情中的"涨跌幅"，若今日涨跌幅>9.8%（即涨停），叠加是否已有多个连板，评估追高风险。
5. **流动性核查**：基于实时行情中的"成交额"和"换手率"，成交额<5000万或换手率<0.5%视为流动性不足，高风险。

请给出明确的风险分析结论：
- 对每只推荐标的的逐项风险评估
- 需要排除的标的及具体原因（引用数据字段）
- 最终安全标的清单
- 整体风险评级：低（无风险项）/中（有1项警示）/高（有强制排除项）

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "risk_level": "低/中/高",
  "safe_stocks": [
    {{"code": "股票代码", "name": "股票名称"}}
  ],
  "excluded_stocks": [
    {{"code": "股票代码", "name": "股票名称", "reason": "排除原因（引用具体字段值）"}}
  ]
}}
```
注意：若safe_stocks为空（所有推荐标的均有风险），risk_level必须设为"高"。

使用中文输出，简洁专业。"""

DECISION_ANALYST_PROMPT = """你是一位资深的决策分析师，负责综合所有分析师的结论，给出最终的AI选股决策。

## 当前时间是：{current_time}
## 各分析师的分析结论

### 大盘分析师结论
{market_report}

### 主线板块分析师结论
{sector_report}

### 市场合力分析师结论
{force_report}

### 股票龙头分析师结论
{leader_report}

### 风险分析师结论
{risk_report}

### 风险分析师确认的安全标的
{safe_stocks_info}

请综合以上所有分析师的结论，给出最终的AI选股决策。注意：

**决策要点**：
1. **综合研判**：大盘5日趋势 + 今日情绪是否一致？如果趋势向上但今日偏空可能是短期波动，反之亦然。
2. **主线有效性**：板块分析师确认的主线，是否得到了合力分析师的资金验证？若两者结论一致才是强信号。
3. **标的质量**：安全标的中是否有连板龙头（优先级最高）、涨幅居前但未封板（次之）、普通强势股（最低）？
4. **仓位原则**：
   - 大盘偏多+主线明确+龙头确认 → 可用总仓位30~50%
   - 大盘中性或主线不明确 → 轻仓试探，不超过20%
   - 多只安全标的时，按连板数高低加权分配（龙头股占比高于跟风股）
5. **失败保护**：如安全标的为空或不确定性极高，action应为"观望"，不强制推荐。

请给出JSON格式的最终决策（放在```json代码块中，报告内容放在JSON之后）：
```json
{{
  "action": "强烈推荐/谨慎推荐/观望/规避",
  "stocks": [
    {{"code": "股票代码", "name": "股票名称", "reason": "推荐理由（含连板数/涨幅/板块等具体数据）"}}
  ],
  "reasoning": "综合决策的详细依据（说明大盘+板块+资金三者是否共振）",
  "position_suggestion": "具体仓位建议（如：总仓位40%，XXX股30%+YYY股10%）",
  "risk_warning": "风险提示（针对当前具体标的和市场环境）"
}}
```

在JSON之后，请用中文输出一份完整的决策分析报告，涵盖市场环境、主线逻辑、标的分析、仓位方案、风险管理五个章节。

使用中文输出，专业严谨。"""


# ============================================================
# AI选股服务主类
# ============================================================

class AiSelectorService:
    """AI选股服务"""

    def __init__(self):
        pass

    def _build_llm_config(self) -> Dict[str, Any]:
        """复用已有的模型配置逻辑，构建与'股票分析'一致的配置
        
        始终使用系统自动推荐的已启用模型，确保 API Key、provider 等均来自数据库配置。
        """
        from app.services.model_capability_service import get_model_capability_service

        capability_service = get_model_capability_service()
        research_depth = "标准"

        # 从系统已启用的配置中自动推荐模型（与 simple_analysis_service 逻辑一致）
        quick_model, deep_model = capability_service.recommend_models_for_depth(research_depth)
        logger.info(f"AI选股 - 自动推荐模型: quick={quick_model}, deep={deep_model}")

        # 获取供应商信息
        quick_provider_info = get_provider_and_url_by_model_sync(quick_model)
        deep_provider_info = get_provider_and_url_by_model_sync(deep_model)

        quick_provider = quick_provider_info["provider"]
        deep_provider = deep_provider_info["provider"]
        quick_backend_url = quick_provider_info["backend_url"]
        deep_backend_url = deep_provider_info["backend_url"]

        logger.info(f"AI选股 - 快速模型 {quick_model} -> 供应商: {quick_provider}, URL: {quick_backend_url}")
        logger.info(f"AI选股 - 深度模型 {deep_model} -> 供应商: {deep_provider}, URL: {deep_backend_url}")

        # 使用create_analysis_config构建完整配置（与股票分析完全一致）
        config = create_analysis_config(
            research_depth=research_depth,
            selected_analysts=["market"],  # 仅用于初始化，不影响我们的自定义分析流程
            quick_model=quick_model,
            deep_model=deep_model,
            llm_provider=quick_provider,
            market_type="A股",
        )

        # 添加混合模式配置
        config["quick_provider"] = quick_provider
        config["deep_provider"] = deep_provider
        config["quick_backend_url"] = quick_backend_url
        config["deep_backend_url"] = deep_backend_url
        config["backend_url"] = quick_backend_url

        return config

    def _create_llm_instances(self, config: Dict[str, Any]):
        """通过TradingAgentsGraph创建正确配置的LLM实例

        复用已有逻辑，确保API Key、provider、backend_url等全部正确
        """
        graph = TradingAgentsGraph(
            selected_analysts=config.get("selected_analysts", ["market"]),
            debug=False,
            config=config,
        )
        logger.info(f"AI选股 - TradingAgentsGraph创建成功，LLM已就绪")
        logger.info(f"  quick_llm: {graph.quick_thinking_llm.__class__.__name__}, model={getattr(graph.quick_thinking_llm, 'model_name', 'unknown')}")
        logger.info(f"  deep_llm: {graph.deep_thinking_llm.__class__.__name__}, model={getattr(graph.deep_thinking_llm, 'model_name', 'unknown')}")

        return graph.quick_thinking_llm, graph.deep_thinking_llm

    async def create_task(self, user_id: str) -> Dict[str, Any]:
        """创建AI选股任务"""
        task_id = str(uuid.uuid4())

        try:
            db = get_mongo_db()

            # Fix: 防止并发执行——同一用户有进行中的任务时拒绝新建
            running_task = await db.ai_selector_tasks.find_one(
                {"user_id": user_id, "status": {"$in": ["pending", "running"]}}
            )
            if running_task:
                raise ValueError(
                    f"已有AI选股任务正在执行（ID: {running_task['task_id'][:8]}...），"
                    f"请等待当前任务完成后再试"
                )

            await db.ai_selector_tasks.insert_one({
                "task_id": task_id,
                "user_id": user_id,
                "status": "pending",
                "progress": 0,
                "current_step": "",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            })
        except ValueError:
            raise  # 业务异常直接向上抛出，不吞掉
        except Exception as e:
            logger.error(f"保存AI选股任务到MongoDB失败: {e}")

        return {"task_id": task_id, "status": "pending", "message": "AI选股任务已创建"}

    async def run_analysis(self, quick_llm, deep_llm, api_cache: ApiCache) -> Dict[str, Any]:
        """执行AI选股核心分析流程（不涉及任务管理/MongoDB，可供外部直接调用）

        Args:
            quick_llm: 快速模型LLM实例
            deep_llm: 深度模型LLM实例
            api_cache: API缓存实例

        Returns:
            包含 analyst_results, decision, decision_report, early_stop 等字段的字典
        """
        analyst_results = []
        decision = None
        decision_report = ""
        early_stop_reason = ""

        market_report = ""
        sector_report = ""
        force_report = ""
        leader_report = ""
        risk_report = ""
        sector_themes_str = ""
        candidate_stocks_str = ""
        recommended_stocks_str = ""
        safe_stocks_info = ""
        leading_stocks: List[Dict] = []

        # ====== Step 1: 大盘分析师 ======
        market_indicators = await asyncio.to_thread(compute_market_indicators, api_cache)

        market_report = await asyncio.to_thread(
            self._run_analyst, quick_llm, "大盘分析师", MARKET_ANALYST_PROMPT, market_indicators
        )
        analyst_results.append({
            "name": "大盘分析师",
            "conclusion": self._extract_conclusion(market_report, "大盘分析师"),
            "tag_type": self._get_conclusion_tag_type(market_report),
            "content": market_report,
        })

        market_sentiment = self._extract_market_sentiment(market_report)
        logger.info(f"AI选股 大盘情绪判断: {market_sentiment}")
        if market_sentiment == "偏空":
            early_stop_reason = "大盘环境偏空，建议观望，终止后续分析"
            logger.info(f"AI选股 提前终止: {early_stop_reason}")
            decision = {
                "action": "观望",
                "stocks": [],
                "reasoning": early_stop_reason,
                "position_suggestion": "空仓观望",
                "risk_warning": "大盘环境偏空，不宜入场",
            }
            decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n大盘分析师判断当前大盘环境偏空，建议空仓观望，等待市场企稳后再考虑入场。"

        # ====== Step 2: 主线板块分析师 ======
        if not early_stop_reason:
            sector_indicators = await asyncio.to_thread(compute_sector_indicators, api_cache)

            market_summary = self._extract_conclusion(market_report, "大盘分析师")
            sector_report = await asyncio.to_thread(
                self._run_analyst, quick_llm, "主线板块分析师", SECTOR_ANALYST_PROMPT, sector_indicators,
                extra_params={"market_summary": market_summary}
            )
            analyst_results.append({
                "name": "主线板块分析师",
                "conclusion": self._extract_conclusion(sector_report, "主线板块分析师"),
                "tag_type": self._get_conclusion_tag_type(sector_report),
                "content": sector_report,
            })

            sector_themes = self._extract_sector_themes(sector_report)
            logger.info(f"AI选股 主线板块: {sector_themes}")
            if not sector_themes:
                early_stop_reason = "当前市场无明显主线板块，资金分散，终止后续分析"
                logger.info(f"AI选股 提前终止: {early_stop_reason}")
                decision = {
                    "action": "观望",
                    "stocks": [],
                    "reasoning": early_stop_reason,
                    "position_suggestion": "空仓观望",
                    "risk_warning": "市场无明显主线，不宜追涨",
                }
                decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n主线板块分析师未发现明显主线板块，市场资金分散无序，建议观望等待主线清晰后再入场。"
            else:
                sector_themes_str = "、".join(sector_themes)

        # ====== Step 3: 市场合力分析师 ======
        if not early_stop_reason:
            force_indicators = await asyncio.to_thread(compute_force_indicators, api_cache)

            force_report = await asyncio.to_thread(
                self._run_analyst, quick_llm, "市场合力分析师", FORCE_ANALYST_PROMPT,
                force_indicators,
                extra_params={"sector_themes": sector_themes_str}
            )
            analyst_results.append({
                "name": "市场合力分析师",
                "conclusion": self._extract_conclusion(force_report, "市场合力分析师"),
                "tag_type": self._get_conclusion_tag_type(force_report),
                "content": force_report,
            })

            candidate_stocks = self._extract_candidate_stocks(force_report)
            logger.info(f"AI选股 合力分析师候选股票: {candidate_stocks}")
            if not candidate_stocks:
                early_stop_reason = "市场合力分析师未筛选出资金合力正向的股票，终止后续分析"
                logger.info(f"AI选股 提前终止: {early_stop_reason}")
                decision = {
                    "action": "观望",
                    "stocks": [],
                    "reasoning": early_stop_reason,
                    "position_suggestion": "空仓观望",
                    "risk_warning": "当前市场资金合力不足，不建议入场",
                }
                decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n市场合力分析师围绕主线板块（{sector_themes_str}）分析后，未发现资金合力正向的标的，建议观望。"
            else:
                candidate_stocks_str = self._format_stocks_for_prompt(candidate_stocks)

        # ====== Step 4: 股票龙头分析师 ======
        if not early_stop_reason:
            leader_indicators = await asyncio.to_thread(compute_leader_indicators, api_cache)

            leader_report = await asyncio.to_thread(
                self._run_analyst, quick_llm, "股票龙头分析师", LEADER_ANALYST_PROMPT,
                leader_indicators,
                extra_params={"candidate_stocks": candidate_stocks_str}
            )
            analyst_results.append({
                "name": "股票龙头分析师",
                "conclusion": self._extract_conclusion(leader_report, "股票龙头分析师"),
                "tag_type": self._get_conclusion_tag_type(leader_report),
                "content": leader_report,
            })

            leading_stocks = self._extract_leading_stocks(leader_report)
            logger.info(f"AI选股 龙头股: {leading_stocks}")
            if not leading_stocks:
                early_stop_reason = "股票龙头分析师未筛选出明确的龙头股，终止后续分析"
                logger.info(f"AI选股 提前终止: {early_stop_reason}")
                decision = {
                    "action": "观望",
                    "stocks": [],
                    "reasoning": early_stop_reason,
                    "position_suggestion": "空仓观望",
                    "risk_warning": "候选股票中未发现明确龙头，不建议追涨",
                }
                decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n股票龙头分析师分析候选股票后，未确认具备龙头属性的标的，建议观望。"
            else:
                recommended_stocks_str = self._format_stocks_for_prompt(leading_stocks)

        # ====== Step 5: 风险分析师 ======
        if not early_stop_reason:
            leading_stock_codes = [s.get("code", "") for s in leading_stocks if s.get("code")]
            risk_indicators = await asyncio.to_thread(
                compute_risk_indicators, api_cache, leading_stock_codes
            )

            risk_report = await asyncio.to_thread(
                self._run_analyst, quick_llm, "风险分析师", RISK_ANALYST_PROMPT,
                risk_indicators,
                extra_params={"recommended_stocks": recommended_stocks_str}
            )
            analyst_results.append({
                "name": "风险分析师",
                "conclusion": self._extract_conclusion(risk_report, "风险分析师"),
                "tag_type": self._get_risk_conclusion_tag_type(risk_report),
                "content": risk_report,
            })

            risk_level = self._extract_risk_level(risk_report)
            logger.info(f"AI选股 风险等级: {risk_level}")
            if risk_level == "高":
                early_stop_reason = "风险分析师评估整体风险较高，终止后续分析"
                logger.info(f"AI选股 提前终止: {early_stop_reason}")
                decision = {
                    "action": "规避",
                    "stocks": [],
                    "reasoning": early_stop_reason,
                    "position_suggestion": "空仓规避",
                    "risk_warning": "推荐标的整体风险较高，建议规避",
                }
                decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n风险分析师评估推荐标的整体风险较高，建议规避，等待风险释放后再考虑入场。"
            else:
                safe_stocks = self._extract_safe_stocks(risk_report)
                safe_stocks_info = self._format_stocks_for_prompt(safe_stocks)

        # ====== Step 6: 决策分析师（使用深度模型） ======
        if not early_stop_reason:
            decision_report = await asyncio.to_thread(
                self._run_decision_analyst, deep_llm,
                market_report, sector_report, force_report, leader_report, risk_report,
                safe_stocks_info=safe_stocks_info
            )
            decision = self._parse_decision(decision_report)

        # 补充提前终止的步骤为"跳过"状态
        all_analyst_names = ["大盘分析师", "主线板块分析师", "市场合力分析师", "股票龙头分析师", "风险分析师"]
        completed_names = {r["name"] for r in analyst_results}
        for name in all_analyst_names:
            if name not in completed_names:
                if early_stop_reason:
                    analyst_results.append({
                        "name": name,
                        "conclusion": "已跳过",
                        "tag_type": "info",
                        "content": f"由于{early_stop_reason}，本步骤已跳过。",
                    })
                else:
                    analyst_results.append({
                        "name": name,
                        "conclusion": "未执行",
                        "tag_type": "info",
                        "content": "本步骤未执行。",
                    })

        return {
            "analyst_results": analyst_results,
            "decision": decision,
            "decision_report": decision_report,
            "early_stop": bool(early_stop_reason),
            "early_stop_reason": early_stop_reason,
        }

    async def execute_task(self, task_id: str, user_id: str = None):
        """执行AI选股任务（后台运行）

        Agent间存在依赖关系和条件终止：
        1. 大盘分析师 -> 偏空则终止
        2. 主线板块分析师 -> 无主线板块则终止，有则传板块给合力分析师
        3. 市场合力分析师 -> 根据主线板块分析，筛选2-3支股票，无结果则终止
        4. 股票龙头分析师 -> 从候选股中选出1-2支龙头股，传给风险分析师
        5. 风险分析师 -> 风险高则终止，无风险则传安全标的给决策分析师
        6. 决策分析师 -> 给出最终选股决策
        """
        start_time = time.time()

        api_cache = ApiCache()

        try:
            await self._update_status(task_id, "running", 5, "正在初始化AI选股分析...")

            await self._update_status(task_id, "running", 8, "正在初始化AI模型...")
            config = await asyncio.to_thread(self._build_llm_config)
            quick_llm, deep_llm = await asyncio.to_thread(self._create_llm_instances, config)

            await self._update_status(task_id, "running", 10, "正在执行AI选股分析...")

            # 调用核心分析逻辑
            analysis_result = await self.run_analysis(quick_llm, deep_llm, api_cache)

            # ====== 保存完整结果 ======
            elapsed = time.time() - start_time

            result = {
                "task_id": task_id,
                "status": "completed",
                "progress": 100,
                "current_step": "分析完成",
                "elapsed_time": round(elapsed, 2),
                "early_stop": analysis_result["early_stop"],
                "early_stop_reason": analysis_result["early_stop_reason"],
                "analyst_results": analysis_result["analyst_results"],
                "decision": analysis_result["decision"],
                "decision_report": analysis_result["decision_report"],
                "completed_at": datetime.utcnow().isoformat(),
            }

            try:
                db = get_mongo_db()
                serializable_result = self._make_serializable(result)
                await db.ai_selector_tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {
                        "status": "completed",
                        "progress": 100,
                        "current_step": "分析完成",
                        "result": serializable_result,
                        "elapsed_time": round(elapsed, 2),
                        "updated_at": datetime.utcnow(),
                    }}
                )
            except Exception as e:
                logger.error(f"保存AI选股结果到MongoDB失败: {e}")

            return result

        except Exception as e:
            logger.error(f"AI选股任务执行失败: {e}", exc_info=True)
            await self._update_status(task_id, "failed", 0, f"分析失败: {str(e)}", error_message=str(e))
            raise
        finally:
            api_cache.clear()

    def _invoke_llm(self, llm, messages, analyst_name: str = "") -> Any:
        """LLM 调用，带指数退避重试（最多3次）"""
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        def _do_invoke():
            return llm.invoke(messages)

        return _do_invoke()

    def _run_analyst(self, llm, analyst_name: str, prompt_template: str,
                     indicators_data: Dict, extra_params: Dict[str, str] = None) -> str:
        """运行单个分析师Agent

        Args:
            extra_params: 额外的模板参数，如 sector_themes、candidate_stocks、recommended_stocks
        """
        logger.info(f"AI选股 [{analyst_name}] 开始分析...")

        indicators_str = compress_json_for_llm(indicators_data)

        format_params = {"indicators_data": indicators_str}
        if extra_params:
            format_params.update(extra_params)

        prompt = prompt_template.format(**format_params)

        logger.info(
            f"[LLM提示词] [{analyst_name}] 提示词长度={len(prompt)}\n"
            f"{'='*60}\n{prompt}\n{'='*60}"
        )

        try:
            response = self._invoke_llm(llm, [HumanMessage(content=prompt)], analyst_name)
            report = response.content
            logger.info(f"AI选股 [{analyst_name}] 分析完成，报告长度: {len(report)}")
            logger.info(
                f"[LLM输出] [{analyst_name}] 输出长度={len(report)}\n"
                f"{'='*60}\n{report}\n{'='*60}"
            )
            return report
        except Exception as e:
            logger.error(f"AI选股 [{analyst_name}] LLM调用失败: {e}")
            return f"{analyst_name}分析失败: {str(e)}"

    def _run_decision_analyst(self, llm, market_report, sector_report,
                              force_report, leader_report, risk_report,
                              safe_stocks_info: str = "") -> str:
        """运行决策分析师"""
        logger.info("AI选股 [决策分析师] 开始综合研判...")

        prompt = DECISION_ANALYST_PROMPT.format(
            current_time=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
            market_report=market_report,
            sector_report=sector_report,
            force_report=force_report,
            leader_report=leader_report,
            risk_report=risk_report,
            safe_stocks_info=safe_stocks_info,
        )

        logger.info(
            f"[LLM提示词] [决策分析师] 提示词长度={len(prompt)}\n"
            f"{'='*60}\n{prompt}\n{'='*60}"
        )

        try:
            response = self._invoke_llm(llm, [HumanMessage(content=prompt)], "决策分析师")
            report = response.content
            logger.info(f"AI选股 [决策分析师] 综合研判完成，报告长度: {len(report)}")
            logger.debug(
                f"[LLM输出] [决策分析师] 输出长度={len(report)}\n"
                f"{'='*60}\n{report}\n{'='*60}"
            )
            return report
        except Exception as e:
            logger.error(f"AI选股 [决策分析师] LLM调用失败: {e}")
            return f"决策分析师分析失败: {str(e)}"

    # ============================================================
    # 结构化数据提取方法（用于Agent间数据传递）
    # ============================================================

    def _extract_json_block(self, text: str) -> Optional[Dict]:
        """从文本中提取最后一个```json代码块并解析"""
        import re
        try:
            matches = re.findall(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if matches:
                return json.loads(matches[-1])
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"解析JSON代码块失败: {e}")
        return None

    def _extract_market_sentiment(self, report: str) -> str:
        """从大盘分析师报告中提取市场情绪判断"""
        try:
            data = self._extract_json_block(report)
            if data and "market_sentiment" in data:
                sentiment = str(data["market_sentiment"])
                if "偏空" in sentiment or "看空" in sentiment:
                    return "偏空"
                elif "偏多" in sentiment or "看多" in sentiment:
                    return "偏多"
                return "中性"
        except Exception:
            pass
        if "偏空" in report or "看空" in report or "弱势" in report:
            return "偏空"
        elif "偏多" in report or "看多" in report or "强势" in report:
            return "偏多"
        return "中性"

    def _extract_sector_themes(self, report: str) -> List[str]:
        """从板块分析师报告中提取主线板块列表"""
        try:
            data = self._extract_json_block(report)
            if data:
                if data.get("has_main_sector") is False:
                    return []
                if "main_sectors" in data and isinstance(data["main_sectors"], list):
                    return [str(s) for s in data["main_sectors"] if s]
        except Exception:
            pass
        import re
        match = re.search(r'主线板块[：:]\s*(.+?)[\n。]', report)
        if match:
            sectors = re.split(r'[、,，]', match.group(1))
            return [s.strip().strip('「」""''') for s in sectors if s.strip()]
        return []

    def _extract_candidate_stocks(self, report: str) -> List[Dict]:
        """从合力分析师报告中提取候选股票（2-3支）"""
        try:
            data = self._extract_json_block(report)
            if data and "recommended_stocks" in data:
                stocks = data["recommended_stocks"]
                if isinstance(stocks, list) and len(stocks) > 0:
                    return stocks[:3]
        except Exception:
            pass
        import re
        codes = re.findall(r'\b(\d{6})\b', report)
        if codes:
            unique_codes = list(dict.fromkeys(codes))[:3]
            return [{"code": c, "name": f"股票{c}"} for c in unique_codes]
        return []

    def _extract_leading_stocks(self, report: str) -> List[Dict]:
        """从龙头分析师报告中提取龙头股（1-2支）"""
        try:
            data = self._extract_json_block(report)
            if data and "leading_stocks" in data:
                stocks = data["leading_stocks"]
                if isinstance(stocks, list) and len(stocks) > 0:
                    return stocks[:2]
        except Exception:
            pass
        import re
        codes = re.findall(r'\b(\d{6})\b', report)
        if codes:
            unique_codes = list(dict.fromkeys(codes))[:2]
            return [{"code": c, "name": f"股票{c}"} for c in unique_codes]
        return []

    def _extract_risk_level(self, report: str) -> str:
        """从风险分析师报告中提取风险等级"""
        try:
            data = self._extract_json_block(report)
            if data and "risk_level" in data:
                level = str(data["risk_level"])
                if "高" in level:
                    return "高"
                elif "低" in level:
                    return "低"
                return "中"
        except Exception:
            pass
        if ("高风险" in report or "风险较高" in report or "风险较大" in report
                or ("整体风险评级" in report and "高风险" in report)):
            return "高"
        elif "低风险" in report or "风险较低" in report or "风险可控" in report:
            return "低"
        return "中"

    def _extract_safe_stocks(self, report: str) -> List[Dict]:
        """从风险分析师报告中提取安全标的"""
        try:
            data = self._extract_json_block(report)
            if data and "safe_stocks" in data:
                stocks = data["safe_stocks"]
                if isinstance(stocks, list) and len(stocks) > 0:
                    return stocks
        except Exception:
            pass
        # Fix: 全文 regex 会把 excluded_stocks 里的代码也捞进来，危险！
        # 回退时直接返回空列表，宁可漏选也不错选高风险标的
        logger.warning("_extract_safe_stocks: 无法从风险报告中解析出结构化安全标的，回退返回空列表")
        return []

    def _format_stocks_for_prompt(self, stocks: List[Dict]) -> str:
        """将股票列表格式化为可读文本，用于传入下游Agent提示词"""
        if not stocks:
            return "暂无"
        lines = []
        for s in stocks:
            code = s.get("code", "")
            name = s.get("name", "")
            reason = s.get("reason", "")
            sector = s.get("sector", "")
            # Fix: 改用 | 分隔字段，避免中文顿号被 LLM 误解为并列事物
            line = f"{code} {name}"
            if sector:
                line += f" | 板块:{sector}"
            if reason:
                line += f" | 理由:{reason}"
            lines.append(line)
        return "\n".join(f"- {line}" for line in lines)

    def _parse_decision(self, decision_report: str) -> Dict[str, Any]:
        """解析决策分析师的结论"""
        import re  # 统一在方法顶部导入，避免重复声明
        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', decision_report, re.DOTALL)
            if json_match:
                decision = json.loads(json_match.group(1))
                return decision
        except Exception as e:
            logger.error(f"解析决策JSON失败: {e}")

        # 回退：从文本中提取信息
        action = "谨慎推荐"
        if "强烈推荐" in decision_report:
            action = "强烈推荐"
        elif "规避" in decision_report:
            action = "规避"
        elif "观望" in decision_report:
            action = "观望"

        codes = re.findall(r'\b(\d{6})\b', decision_report)  # Fix: 移除重复的 import re
        stocks = [{"code": c, "name": f"股票{c}"} for c in list(dict.fromkeys(codes))[:5]]

        return {
            "action": action,
            "stocks": stocks,
            "reasoning": decision_report[:500],
        }

    def _extract_conclusion(self, report: str, analyst_name: str = "") -> str:
        """从分析报告中提取简要结论，优先使用 JSON 结构化数据"""
        try:
            data = self._extract_json_block(report)
            if data:
                if analyst_name == "大盘分析师":
                    sentiment = str(data.get("market_sentiment", ""))
                    if sentiment:
                        return sentiment
                elif analyst_name == "主线板块分析师":
                    if data.get("has_main_sector") is False:
                        return "无主线板块"
                    sectors = data.get("main_sectors", [])
                    if sectors:
                        return "、".join(str(s) for s in sectors)
                elif analyst_name == "市场合力分析师":
                    direction = data.get("force_direction", "")
                    if direction:
                        return str(direction)
                elif analyst_name == "股票龙头分析师":
                    stocks = data.get("leading_stocks", [])
                    if stocks:
                        names = [f"{s.get('code', '')} {s.get('name', '')}" for s in stocks[:2]]
                        return "、".join(n.strip() for n in names)
                elif analyst_name == "风险分析师":
                    level = data.get("risk_level", "")
                    if level:
                        return f"风险{level}"
        except Exception:
            pass
        # 回退：关键词匹配
        if "偏多" in report or "看多" in report or "强势" in report:
            return "偏多"
        elif "偏空" in report or "看空" in report or "弱势" in report:
            return "偏空"
        elif "正向共振" in report:
            return "正向共振"
        elif "中性" in report or "震荡" in report:
            return "中性"
        return "中性"

    def _get_conclusion_tag_type(self, report: str) -> str:
        """根据结论获取标签类型"""
        if "偏多" in report or "看多" in report or "强势" in report:
            return "success"
        elif "偏空" in report or "看空" in report or "弱势" in report:
            return "danger"
        return "info"

    def _get_risk_conclusion_tag_type(self, report: str) -> str:
        """风险分析师的结论标签"""
        # Fix: "高" 太宽泛（最高、更高等），改用具体的风险词组
        if "高风险" in report or "风险较高" in report or "风险高" in report:
            return "danger"
        elif "低风险" in report or "风险较低" in report or "风险低" in report or "风险可控" in report:
            return "success"
        return "warning"

    def _make_serializable(self, obj):
        """将对象转换为可序列化的格式"""
        if isinstance(obj, dict):
            return {k: self._make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._make_serializable(v) for v in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, (int, float, str, bool, type(None))):
            return obj
        else:
            return str(obj)

    async def _update_status(self, task_id: str, status: str, progress: int,
                             current_step: str, error_message: str = None):
        """更新任务状态"""
        try:
            db = get_mongo_db()
            update_data = {
                "status": status,
                "progress": progress,
                "current_step": current_step,
                "updated_at": datetime.utcnow(),
            }
            if error_message:
                update_data["error_message"] = error_message
            await db.ai_selector_tasks.update_one(
                {"task_id": task_id},
                {"$set": update_data}
            )
        except Exception as e:
            logger.error(f"更新AI选股任务状态失败: {e}")

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        try:
            db = get_mongo_db()
            task = await db.ai_selector_tasks.find_one(
                {"task_id": task_id},
                {"_id": 0}
            )
            return task
        except Exception as e:
            logger.error(f"获取AI选股任务状态失败: {e}")
            return None

    async def get_task_list(self, user_id: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """获取AI选股任务列表（分页）"""
        try:
            db = get_mongo_db()
            query = {"user_id": user_id}
            total = await db.ai_selector_tasks.count_documents(query)
            skip = (page - 1) * page_size
            cursor = db.ai_selector_tasks.find(
                query,
                {"_id": 0, "result.analyst_results": 0, "result.decision_report": 0}
            ).sort("created_at", -1).skip(skip).limit(page_size)
            tasks = await cursor.to_list(length=page_size)
            return {"tasks": tasks, "total": total, "page": page, "page_size": page_size}
        except Exception as e:
            logger.error(f"获取AI选股任务列表失败: {e}")
            return {"tasks": [], "total": 0, "page": page, "page_size": page_size}

    async def get_task_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务结果"""
        try:
            db = get_mongo_db()
            task = await db.ai_selector_tasks.find_one(
                {"task_id": task_id},
                {"_id": 0}
            )
            if not task:
                logger.warning(f"get_task_result: task_id={task_id} 未找到记录")
                return None
            if task.get("status") == "completed":
                result = task.get("result", task)
                # result 子文档不含 user_id，需要从 task 顶层补上，否则路由层权限校验会 403
                if result and "user_id" not in result:
                    result["user_id"] = task.get("user_id")
                logger.info(
                    f"get_task_result: task_id={task_id}, status=completed, "
                    f"result.user_id={result.get('user_id') if result else 'N/A'}, "
                    f"result_keys={list(result.keys())[:10] if result else 'N/A'}"
                )
                return result
            logger.info(f"get_task_result: task_id={task_id}, status={task.get('status')}")
            return task
        except Exception as e:
            logger.error(f"获取AI选股任务结果失败: {e}")
            return None


# 单例
_ai_selector_service: Optional[AiSelectorService] = None
_ai_selector_service_lock = threading.Lock()

def get_ai_selector_service() -> AiSelectorService:
    global _ai_selector_service
    if _ai_selector_service is None:
        with _ai_selector_service_lock:
            if _ai_selector_service is None:
                _ai_selector_service = AiSelectorService()
    return _ai_selector_service
