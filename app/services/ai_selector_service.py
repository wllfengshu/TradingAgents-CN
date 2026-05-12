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
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from langchain_core.messages import HumanMessage

from tradingagents.graph.trading_graph import TradingAgentsGraph
from app.services.simple_analysis_service import (
    create_analysis_config,
    get_provider_and_url_by_model_sync,
)
from app.core.database import get_mongo_db

logger = logging.getLogger("app.services.ai_selector_service")


# ============================================================
# 指标计算函数
# ============================================================

def compute_market_indicators() -> Dict[str, Any]:
    """大盘分析师指标计算：指数/北向资金/涨跌比等"""
    try:
        import akshare as ak

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 1. 上证指数
        try:
            sh_index = ak.stock_zh_index_daily(symbol="sh000001")
            if sh_index is not None and not sh_index.empty:
                latest = sh_index.iloc[-1]
                prev = sh_index.iloc[-2] if len(sh_index) > 1 else latest
                result["上证指数"] = {
                    "收盘价": float(latest["close"]),
                    "涨跌幅": round((float(latest["close"]) - float(prev["close"])) / float(prev["close"]) * 100, 2),
                    "成交量": int(latest["volume"]) if "volume" in latest else None,
                }
        except Exception as e:
            logger.error(f"获取上证指数失败: {e}")
            result["上证指数"] = "获取失败"

        # 2. 深证成指
        try:
            sz_index = ak.stock_zh_index_daily(symbol="sz399001")
            if sz_index is not None and not sz_index.empty:
                latest = sz_index.iloc[-1]
                prev = sz_index.iloc[-2] if len(sz_index) > 1 else latest
                result["深证成指"] = {
                    "收盘价": float(latest["close"]),
                    "涨跌幅": round((float(latest["close"]) - float(prev["close"])) / float(prev["close"]) * 100, 2),
                }
        except Exception as e:
            logger.error(f"获取深证成指失败: {e}")
            result["深证成指"] = "获取失败"

        # 3. 沪深港通资金（替代已停止公布的北向资金净流入数据）
        # 3a. 沪深港通每日资金流向汇总
        try:
            hsgt_summary = ak.stock_hsgt_fund_flow_summary_em()
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

                    entry = {}
                    if net_buy is not None and not (isinstance(net_buy, float) and str(net_buy) == "nan"):
                        entry["成交净买额(亿)"] = round(float(net_buy), 2)
                    if fund_inflow is not None and not (isinstance(fund_inflow, float) and str(fund_inflow) == "nan"):
                        entry["资金净流入(亿)"] = round(float(fund_inflow), 2)
                    if up_count is not None and not (isinstance(up_count, float) and str(up_count) == "nan"):
                        entry["上涨数"] = int(float(up_count))
                    if down_count is not None and not (isinstance(down_count, float) and str(down_count) == "nan"):
                        entry["下跌数"] = int(float(down_count))
                    if related_index:
                        entry["相关指数"] = related_index
                    if index_change is not None and not (isinstance(index_change, float) and str(index_change) == "nan"):
                        entry["指数涨跌幅(%)"] = float(index_change)

                    hsgt_data[f"{block_name}({direction})"] = entry

                hsgt_data["说明"] = "沪深港通资金流向汇总，反映外资参与活跃程度"
                result["沪深港通成交"] = hsgt_data
        except Exception as e:
            logger.error(f"获取沪深港通成交数据失败: {e}")
            result["沪深港通成交"] = "获取失败"

        # 3b. 北向资金增持个股排行前10（替代原沪股通活跃股）
        try:
            hold_rank = ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")
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
            industry_flow = ak.stock_fund_flow_industry(symbol="即时")
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
            stock_changes = ak.stock_zh_a_spot()
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


def compute_sector_indicators() -> Dict[str, Any]:
    """主线板块分析师指标计算：涨停集中度/5日强度等"""
    try:
        import akshare as ak

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 1. 板块涨跌幅排行（使用同花顺数据源，东方财富接口被限制）
        try:
            sector_rank = ak.stock_board_industry_summary_ths()
            if sector_rank is not None and not sector_rank.empty:
                top_sectors = sector_rank.nlargest(10, "涨跌幅") if "涨跌幅" in sector_rank.columns else sector_rank.head(10)
                result["涨幅前10板块"] = []
                for _, row in top_sectors.iterrows():
                    item = {"板块名称": str(row["板块"]) if "板块" in sector_rank.columns else "未知"}
                    if "涨跌幅" in sector_rank.columns:
                        item["涨跌幅"] = float(row["涨跌幅"])
                    if "总成交额" in sector_rank.columns:
                        item["总成交额"] = str(row["总成交额"])
                    if "净流入" in sector_rank.columns:
                        item["净流入"] = str(row["净流入"])
                    if "领涨股" in sector_rank.columns:
                        item["领涨股"] = str(row["领涨股"])
                    result["涨幅前10板块"].append(item)
        except Exception as e:
            logger.error(f"获取板块排行失败: {e}")
            result["涨幅前10板块"] = "获取失败"

        # 2. 涨停股统计
        try:
            zt_stocks = ak.stock_zt_pool_em(date=datetime.now().strftime("%Y%m%d"))
            if zt_stocks is not None and not zt_stocks.empty:
                result["涨停统计"] = {
                    "涨停数量": len(zt_stocks),
                    "涨停股列表": [
                        {"代码": str(row.iloc[1]), "名称": str(row.iloc[2])}
                        for _, row in zt_stocks.head(20).iterrows()
                    ] if len(zt_stocks) > 0 else [],
                }
        except Exception as e:
            logger.error(f"获取涨停统计失败: {e}")
            result["涨停统计"] = "获取失败"

        # 3. 连板股统计（强势股池：连续涨停2板及以上的股票）
        try:
            lb_stocks = ak.stock_zt_pool_strong_em(date=datetime.now().strftime("%Y%m%d"))
            if lb_stocks is not None and not lb_stocks.empty:
                # 过滤掉成交额为0的停牌股票
                if "成交额" in lb_stocks.columns:
                    lb_stocks = lb_stocks[lb_stocks["成交额"].astype(float) > 0]
                lb_stocks_length = len(lb_stocks)
                max_len = 50 if lb_stocks_length > 50 else lb_stocks_length
                result["强势股池统计"] = {
                    "强势股池数量": lb_stocks_length,
                    "强势股池股列表(前50)": [
                        {"代码": str(row.iloc[1]), "名称": str(row.iloc[2])}
                        for _, row in lb_stocks.head(max_len).iterrows()
                    ] if lb_stocks_length > 0 else [],
                }
        except Exception as e:
            logger.error(f"获取连板统计失败: {e}")
            result["连板统计"] = "获取失败"

        return result

    except ImportError:
        return {"提示": "akshare未安装，无法获取实时数据"}
    except Exception as e:
        logger.error(f"计算板块指标失败: {e}")
        return {"错误": str(e)}


def compute_force_indicators() -> Dict[str, Any]:
    """市场合力分析师指标计算：主力+散户双向净流入等"""
    try:
        import akshare as ak

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 1. 行业资金流向（使用同花顺行业资金流，替代东方财富接口）
        try:
            industry_fund = ak.stock_fund_flow_industry(symbol="即时")
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
                    result["主力资金流向前10"].append(item)
        except Exception as e:
            logger.error(f"获取主力资金流向失败: {e}")
            result["主力资金流向"] = "获取失败"

        # 2. 个股资金流向（使用同花顺个股资金流，替代东方财富接口）
        try:
            stock_fund = ak.stock_fund_flow_individual(symbol="即时")
            if stock_fund is not None and not stock_fund.empty:
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
                    result["个股主力净流入前10"].append(item)
        except Exception as e:
            logger.error(f"获取个股资金流向失败: {e}")
            result["个股资金流向"] = "获取失败"

        return result

    except ImportError:
        return {"提示": "akshare未安装，无法获取实时数据"}
    except Exception as e:
        logger.error(f"计算合力指标失败: {e}")
        return {"错误": str(e)}


def compute_leader_indicators() -> Dict[str, Any]:
    """股票龙头分析师指标计算：连板/板块排名/成交量等"""
    try:
        import akshare as ak

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 1. 涨停池（龙头候选）
        try:
            zt_pool = ak.stock_zt_pool_em(date=datetime.now().strftime("%Y%m%d"))
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

        # 2. 强势股（涨幅前20，使用新浪数据源）
        try:
            stock_rank = ak.stock_zh_a_spot()
            if stock_rank is not None and not stock_rank.empty:
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


def compute_risk_indicators() -> Dict[str, Any]:
    """风险分析师指标计算：排除ST/新股/退市等"""
    try:
        import akshare as ak

        result = {"指标来源": "akshare实时数据", "计算时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # 1. ST股票列表
        try:
            result["ST风险股票"] = "直接判断股票名称是否包含ST或*ST"
        except Exception as e:
            logger.error(f"获取ST股票失败: {e}")
            result["ST风险股票"] = "获取失败"

        # 2. 次新股（使用新浪数据源，东方财富接口被限制）
        try:
            new_stocks = ak.stock_zh_a_new()
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

        # 3. 退市风险
        try:
            result["ST风险股票"] = "直接判断股票名称是否包含 退市 或者 退字"
        except Exception as e:
            logger.error(f"获取涨幅异常失败: {e}")

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

请基于上述数据，从以下维度进行分析：

1. **指数走势判断**：上证指数、深证成指当前走势如何？是上升趋势、下降趋势还是震荡？
2. **沪深港通资金分析**：注意，"沪深港通成交"数据包含沪股通(北向)、深股通(北向)等的成交净买额、资金净流入、涨跌数等。分析北向资金整体是净流入还是净流出？净买额大小反映外资参与活跃程度。"沪深港通活跃股前10"为北向资金增持排行，关注外资增持的个股和行业方向。"沪深港通行业成交集中度"为行业资金流向排行，关注资金集中流入的行业。
3. **涨跌比分析**：上涨家数与下跌家数的比值如何？市场广度如何？
4. **综合判断**：当前大盘整体偏多、偏空还是中性？

请给出明确的大盘分析结论，包括：
- 大盘环境：偏多/偏空/中性
- 核心依据（2-3条）
- 重点关注的风险点

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "market_sentiment": "偏多/偏空/中性",
  "confidence": 0.8,
  "key_points": ["依据1", "依据2"]
}}
```

使用中文输出，简洁专业。"""

SECTOR_ANALYST_PROMPT = """你是一位资深的主线板块分析师，专注于识别当前市场的热点主线板块。

以下是通过代码计算获取的最新板块指标数据：

{indicators_data}

请基于上述数据，从以下维度进行分析：

1. **涨停集中度**：哪些板块的涨停股最多？说明资金聚焦方向
2. **5日强度**：近期持续强势的板块有哪些？是否形成趋势？
3. **板块轮动**：当前市场的主线板块是什么？是否有板块切换迹象？
4. **资金共识**：市场资金最集中攻击的方向是什么？

请给出明确的板块分析结论，包括：
- 当前主线板块（1-2个）
- 核心逻辑（各板块走强的原因）
- 持续性判断（是持续还是一日游）

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "has_main_sector": true,
  "main_sectors": ["板块1", "板块2"],
  "sustainability": "持续/一日游",
  "reasoning": "简要逻辑"
}}
```
注意：如果当前市场确实没有明显的主线板块（如板块涨跌幅都很小、板块内连板高度不够、资金分散无序），请将has_main_sector设为false。

使用中文输出，简洁专业。"""

FORCE_ANALYST_PROMPT = """你是一位资深的市场合力分析师，专注于分析主力与散户的资金动向。

## 上游分析师传入的主线板块
主线板块分析师识别出以下主线板块：{sector_themes}
请重点围绕这些主线板块分析资金合力。

以下是通过代码计算获取的最新资金流向数据：

{indicators_data}

请基于上述数据，从以下维度进行分析：

1. **行业资金流向**：主力资金净流入哪些行业？净流出哪些行业？尤其是主线板块的资金情况如何？
2. **个股资金流向**：个股主力资金净流入前10的股票有哪些？流入流出比例如何？
3. **合力判断**：行业资金和个股资金方向是否一致？合力方向是正向还是反向？
4. **量能配合**：资金流入是否伴随成交量放大？

请给出明确的合力分析结论，包括：
- 合力方向：正向共振/反向分歧/主力主导
- 资金最集中的方向
- 需要警惕的资金信号
- **从主线板块中筛选出2到3支资金合力最正向的股票**

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "force_direction": "正向共振/反向分歧/主力主导",
  "recommended_stocks": [
    {{"code": "股票代码", "name": "股票名称", "reason": "推荐理由"}}
  ]
}}
```
注意：如果分析后确实无法筛选出资金合力正向的股票，recommended_stocks可以为空数组。

使用中文输出，简洁专业。"""

LEADER_ANALYST_PROMPT = """你是一位资深的股票龙头分析师，专注于筛选板块龙头与连板强势股。

## 上游分析师传入的候选股票
市场合力分析师筛选出以下候选股票：{candidate_stocks}
请重点分析这些候选股票的龙头属性。

以下是通过代码计算获取的最新龙头指标数据：

{indicators_data}

请基于上述数据，从以下维度进行分析：

1. **连板股分析**：候选股票中是否有连板股？最高连板数是多少？龙头地位是否稳固？
2. **板块龙头**：候选股票在其所属板块中的龙头地位如何？龙头强度如何？
3. **成交量分析**：候选股票的成交量是否配合？是否有缩量加速或放量滞涨的信号？
4. **龙头持续性**：候选股票的持续性如何？是否有新龙头崛起的迹象？

请给出明确的龙头分析结论，从候选股票中筛选出1到2支真正的龙头股，包括：
- 龙头股推荐（1-2支）
- 各龙头股所属板块及逻辑
- 龙头股强度评级（强/中/弱）

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "leading_stocks": [
    {{"code": "股票代码", "name": "股票名称", "sector": "所属板块", "strength": "强/中/弱"}}
  ]
}}
```
注意：只推荐1-2支最确定的龙头股，宁缺毋滥。

使用中文输出，简洁专业。"""

RISK_ANALYST_PROMPT = """你是一位资深的风险分析师，专注于排除高风险标的，保障投资安全边际。

以下是通过代码计算获取的最新风险指标数据：

{indicators_data}

同时，上游分析师推荐了以下标的：
{recommended_stocks}

请基于上述数据，从以下维度进行风险分析：

1. **ST风险**：推荐的标的中是否包含ST或*ST股票？必须排除！
2. **新股风险**：推荐标的中是否有上市不满60个交易日的新股？需要警惕！
3. **退市风险**：是否有处于退市整理期或存在退市风险的股票？
4. **涨幅异常**：是否有短期涨幅过大（如连续涨停）的股票？追高风险大！
5. **流动性风险**：是否有成交量极低、存在流动性风险的股票？

请给出明确的风险分析结论，包括：
- 需要排除的标的及原因
- 需要警惕的标的及风险点
- 最终安全标的清单
- 整体风险评级：低/中/高

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "risk_level": "低/中/高",
  "safe_stocks": [
    {{"code": "股票代码", "name": "股票名称"}}
  ],
  "excluded_stocks": [
    {{"code": "股票代码", "name": "股票名称", "reason": "排除原因"}}
  ]
}}
```
注意：如果整体风险评级为"高"（如推荐标的全部存在重大风险），risk_level应设为"高"。

使用中文输出，简洁专业。"""

DECISION_ANALYST_PROMPT = """你是一位资深的决策分析师，负责综合所有分析师的结论，给出最终的AI选股决策。

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

请综合以上所有分析师的结论，给出最终的AI选股决策：

1. **综合研判**：大盘环境是否适合选股入场？
2. **主线方向**：当前最值得关注的板块方向是什么？
3. **标的推荐**：基于风险分析师确认的安全标的，给出最终推荐
4. **仓位建议**：建议的总体仓位和个股仓位分配
5. **风险提示**：需要特别关注的风险

请给出JSON格式的最终决策：
```json
{{
  "action": "强烈推荐/谨慎推荐/观望/规避",
  "stocks": [
    {{"code": "股票代码", "name": "股票名称", "reason": "推荐理由"}}
  ],
  "reasoning": "综合决策的详细依据",
  "position_suggestion": "仓位建议",
  "risk_warning": "风险提示"
}}
```

同时，在JSON之后，请用中文输出一份完整的决策分析报告。

使用中文输出，专业严谨。"""


# ============================================================
# AI选股服务主类
# ============================================================

class AiSelectorService:
    """AI选股服务"""

    def __init__(self):
        pass

    def _build_llm_config(self) -> Dict[str, Any]:
        """复用已有的模型配置逻辑，构建与'股票分析'一致的配置"""
        from app.services.model_capability_service import get_model_capability_service

        capability_service = get_model_capability_service()
        research_depth = "标准"

        # 与simple_analysis_service一致：自动推荐模型
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
            await db.ai_selector_tasks.insert_one({
                "task_id": task_id,
                "user_id": user_id,
                "status": "pending",
                "progress": 0,
                "current_step": "",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            })
        except Exception as e:
            logger.error(f"保存AI选股任务到MongoDB失败: {e}")

        return {"task_id": task_id, "status": "pending", "message": "AI选股任务已创建"}

    async def execute_task(self, task_id: str, user_id: str):
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

        # 用于收集各分析师结果（即使提前终止也能展示已完成的分析）
        analyst_results = []
        decision = None
        decision_report = ""
        early_stop_reason = ""

        try:
            await self._update_status(task_id, "running", 5, "正在初始化AI选股分析...")

            # 构建配置并创建LLM
            await self._update_status(task_id, "running", 8, "正在初始化AI模型...")
            config = await asyncio.to_thread(self._build_llm_config)
            quick_llm, deep_llm = await asyncio.to_thread(self._create_llm_instances, config)

            # ====== Step 1: 大盘分析师 ======
            await self._update_status(task_id, "running", 10, "大盘分析师计算指标中...")
            market_indicators = await asyncio.to_thread(compute_market_indicators)

            await self._update_status(task_id, "running", 15, "大盘分析师分析中...")
            market_report = await asyncio.to_thread(
                self._run_analyst, quick_llm, "大盘分析师", MARKET_ANALYST_PROMPT, market_indicators
            )
            analyst_results.append({
                "name": "大盘分析师",
                "conclusion": self._extract_conclusion(market_report),
                "tag_type": self._get_conclusion_tag_type(market_report),
                "content": market_report,
            })

            # 条件终止：大盘偏空则停止
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
                await self._update_status(task_id, "running", 25, "主线板块分析师计算指标中...")
                sector_indicators = await asyncio.to_thread(compute_sector_indicators)

                await self._update_status(task_id, "running", 30, "主线板块分析师分析中...")
                sector_report = await asyncio.to_thread(
                    self._run_analyst, quick_llm, "主线板块分析师", SECTOR_ANALYST_PROMPT, sector_indicators
                )
                analyst_results.append({
                    "name": "主线板块分析师",
                    "conclusion": self._extract_conclusion(sector_report),
                    "tag_type": self._get_conclusion_tag_type(sector_report),
                    "content": sector_report,
                })

                # 条件终止：无主线板块则停止
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
                    # 将主线板块信息传递给合力分析师
                    sector_themes_str = "、".join(sector_themes)

            # ====== Step 3: 市场合力分析师 ======
            if not early_stop_reason:
                await self._update_status(task_id, "running", 40, "市场合力分析师计算指标中...")
                force_indicators = await asyncio.to_thread(compute_force_indicators)

                await self._update_status(task_id, "running", 45, "市场合力分析师分析中...")
                force_report = await asyncio.to_thread(
                    self._run_analyst, quick_llm, "市场合力分析师", FORCE_ANALYST_PROMPT,
                    force_indicators,
                    extra_params={"sector_themes": sector_themes_str}
                )
                analyst_results.append({
                    "name": "市场合力分析师",
                    "conclusion": self._extract_conclusion(force_report),
                    "tag_type": self._get_conclusion_tag_type(force_report),
                    "content": force_report,
                })

                # 条件终止：未筛选出候选股票则停止
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
                    # 将候选股票信息传递给龙头分析师
                    candidate_stocks_str = self._format_stocks_for_prompt(candidate_stocks)

            # ====== Step 4: 股票龙头分析师 ======
            if not early_stop_reason:
                await self._update_status(task_id, "running", 55, "股票龙头分析师计算指标中...")
                leader_indicators = await asyncio.to_thread(compute_leader_indicators)

                await self._update_status(task_id, "running", 60, "股票龙头分析师分析中...")
                leader_report = await asyncio.to_thread(
                    self._run_analyst, quick_llm, "股票龙头分析师", LEADER_ANALYST_PROMPT,
                    leader_indicators,
                    extra_params={"candidate_stocks": candidate_stocks_str}
                )
                analyst_results.append({
                    "name": "股票龙头分析师",
                    "conclusion": self._extract_conclusion(leader_report),
                    "tag_type": self._get_conclusion_tag_type(leader_report),
                    "content": leader_report,
                })

                # 提取龙头股传给风险分析师
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
                await self._update_status(task_id, "running", 70, "风险分析师计算指标中...")
                risk_indicators = await asyncio.to_thread(compute_risk_indicators)

                await self._update_status(task_id, "running", 75, "风险分析师分析中...")
                risk_report = await asyncio.to_thread(
                    self._run_analyst, quick_llm, "风险分析师", RISK_ANALYST_PROMPT,
                    risk_indicators,
                    extra_params={"recommended_stocks": recommended_stocks_str}
                )
                analyst_results.append({
                    "name": "风险分析师",
                    "conclusion": self._extract_conclusion(risk_report),
                    "tag_type": self._get_risk_conclusion_tag_type(risk_report),
                    "content": risk_report,
                })

                # 条件终止：风险高则停止
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
                    # 提取安全标的传给决策分析师
                    safe_stocks = self._extract_safe_stocks(risk_report)
                    safe_stocks_info = self._format_stocks_for_prompt(safe_stocks)

            # ====== Step 6: 决策分析师（使用深度模型） ======
            if not early_stop_reason:
                await self._update_status(task_id, "running", 85, "决策分析师综合研判中...")
                decision_report = await asyncio.to_thread(
                    self._run_decision_analyst, deep_llm,
                    market_report, sector_report, force_report, leader_report, risk_report,
                    safe_stocks_info=safe_stocks_info
                )
                decision = self._parse_decision(decision_report)

            # ====== 保存完整结果 ======
            elapsed = time.time() - start_time

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

            result = {
                "task_id": task_id,
                "status": "completed",
                "progress": 100,
                "current_step": "分析完成",
                "elapsed_time": round(elapsed, 2),
                "early_stop": bool(early_stop_reason),
                "early_stop_reason": early_stop_reason,
                "analyst_results": analyst_results,
                "decision": decision,
                "decision_report": decision_report,
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

    def _run_analyst(self, llm, analyst_name: str, prompt_template: str,
                     indicators_data: Dict, extra_params: Dict[str, str] = None) -> str:
        """运行单个分析师Agent

        Args:
            extra_params: 额外的模板参数，如 sector_themes、candidate_stocks、recommended_stocks
        """
        logger.info(f"AI选股 [{analyst_name}] 开始分析...")

        indicators_str = json.dumps(indicators_data, ensure_ascii=False, indent=2)

        format_params = {"indicators_data": indicators_str}
        if extra_params:
            format_params.update(extra_params)

        prompt = prompt_template.format(**format_params)

        try:
            response = llm.invoke([HumanMessage(content=prompt)])
            report = response.content
            logger.info(f"AI选股 [{analyst_name}] 分析完成，报告长度: {len(report)}")
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
            market_report=market_report,
            sector_report=sector_report,
            force_report=force_report,
            leader_report=leader_report,
            risk_report=risk_report,
            safe_stocks_info=safe_stocks_info,
        )

        try:
            response = llm.invoke([HumanMessage(content=prompt)])
            report = response.content
            logger.info(f"AI选股 [决策分析师] 综合研判完成，报告长度: {len(report)}")
            return report
        except Exception as e:
            logger.error(f"AI选股 [决策分析师] LLM调用失败: {e}")
            return f"决策分析师分析失败: {str(e)}"

    def _extract_recommended_stocks(self, leader_report: str) -> str:
        """从龙头分析师报告中提取推荐的标的"""
        import re
        codes = re.findall(r'\b(\d{6})\b', leader_report)
        if codes:
            unique_codes = list(dict.fromkeys(codes))[:10]
            return "、".join(unique_codes)
        return "暂无明确推荐的标的代码"

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
        if "高风险" in report or "风险较高" in report or "风险较大" in report or "整体风险评级" in report and "高" in report:
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
        import re
        codes = re.findall(r'\b(\d{6})\b', report)
        if codes:
            unique_codes = list(dict.fromkeys(codes))[:5]
            return [{"code": c, "name": f"股票{c}"} for c in unique_codes]
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
            parts = [f"{code} {name}"]
            if sector:
                parts.append(f"板块:{sector}")
            if reason:
                parts.append(f"理由:{reason}")
            lines.append("、".join(parts))
        return "\n".join(f"- {line}" for line in lines)

    def _parse_decision(self, decision_report: str) -> Dict[str, Any]:
        """解析决策分析师的结论"""
        try:
            import re
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

        import re
        codes = re.findall(r'\b(\d{6})\b', decision_report)
        stocks = [{"code": c, "name": f"股票{c}"} for c in dict.fromkeys(codes)[:5]]

        return {
            "action": action,
            "stocks": stocks,
            "reasoning": decision_report[:500],
        }

    def _extract_conclusion(self, report: str) -> str:
        """从分析报告中提取简要结论"""
        if "偏多" in report or "看多" in report or "强势" in report:
            return "偏多"
        elif "偏空" in report or "看空" in report or "弱势" in report:
            return "偏空"
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
        if "低" in report and "风险较低" in report:
            return "success"
        elif "高" in report:
            return "danger"
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
            if task and task.get("status") == "completed":
                return task.get("result", task)
            return task
        except Exception as e:
            logger.error(f"获取AI选股任务结果失败: {e}")
            return None


# 单例
_ai_selector_service = None

def get_ai_selector_service() -> AiSelectorService:
    global _ai_selector_service
    if _ai_selector_service is None:
        _ai_selector_service = AiSelectorService()
    return _ai_selector_service
