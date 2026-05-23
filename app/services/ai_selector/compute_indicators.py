"""
计算指标
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from app.utils.stock_utils import is_main_board_stock
import pandas as pd
import akshare as ak

logger = logging.getLogger("app.services.ai_selector.compute_indicators")


# ============================================================
# 大盘指标（compute_market_indicators 的子项）
# ============================================================

def stock_zh_index_daily(api_cache) -> Any:
    """上证指数：收盘价/今日涨跌幅/5日涨跌幅/近5日收盘价/成交量"""
    try:
        sh_index = api_cache.call(
            "stock_zh_index_daily:sh000001", ak.stock_zh_index_daily, symbol="sh000001"
        )
        if sh_index is None or sh_index.empty:
            return None
        latest = sh_index.iloc[-1]
        prev = sh_index.iloc[-2] if len(sh_index) > 1 else latest
        # Fix: base5 取 5 日前的收盘价（iloc[-6]），用于计算 5 日涨跌幅（5 个区间）
        # 展示用的 close5 则取最近 5 日（iloc[-5:]），共 5 个数据点，与名称一致
        base5_row = sh_index.iloc[-6] if len(sh_index) >= 6 else sh_index.iloc[0]
        base5 = float(base5_row["close"])
        change5d = round((float(latest["close"]) - base5) / base5 * 100, 2)
        recent5 = sh_index.iloc[-5:] if len(sh_index) >= 5 else sh_index
        close5 = [round(float(v), 2) for v in recent5["close"].tolist()]
        return {
            "收盘价": float(latest["close"]),
            "今日涨跌幅(%)": round((float(latest["close"]) - float(prev["close"])) / float(prev["close"]) * 100, 2),
            "5日涨跌幅(%)": change5d,
            "近5日收盘价(从旧到新)": close5,
            "成交量": int(latest["volume"]) if "volume" in latest else None,
        }
    except Exception as e:
        logger.error(f"获取上证指数失败: {e}")
        return None


def stock_zh_index_daily_sz(api_cache) -> Any:
    """深证成指：收盘价/今日涨跌幅/5日涨跌幅/近5日收盘价"""
    try:
        sz_index = api_cache.call(
            "stock_zh_index_daily:sz399001", ak.stock_zh_index_daily, symbol="sz399001"
        )
        if sz_index is None or sz_index.empty:
            return "获取失败"
        latest = sz_index.iloc[-1]
        prev = sz_index.iloc[-2] if len(sz_index) > 1 else latest
        # Fix: 同上证逻辑，base5 与展示数据分开
        base5_row = sz_index.iloc[-6] if len(sz_index) >= 6 else sz_index.iloc[0]
        base5 = float(base5_row["close"])
        change5d = round((float(latest["close"]) - base5) / base5 * 100, 2)
        recent5 = sz_index.iloc[-5:] if len(sz_index) >= 5 else sz_index
        close5 = [round(float(v), 2) for v in recent5["close"].tolist()]
        return {
            "收盘价": float(latest["close"]),
            "今日涨跌幅(%)": round((float(latest["close"]) - float(prev["close"])) / float(prev["close"]) * 100, 2),
            "5日涨跌幅(%)": change5d,
            "近5日收盘价(从旧到新)": close5,
        }
    except Exception as e:
        logger.error(f"获取深证成指失败: {e}")
        return "获取失败"


def stock_hsgt_fund_flow_summary(api_cache) -> Any:
    """沪深港通每日资金流向汇总（替代已停止公布的北向资金净流入数据）"""
    try:
        hsgt_summary = api_cache.call(
            "stock_hsgt_fund_flow_summary_em", ak.stock_hsgt_fund_flow_summary_em
        )
        if hsgt_summary is None or hsgt_summary.empty:
            return "获取失败"

        hsgt_data: Dict[str, Any] = {}
        for _, row in hsgt_summary.iterrows():
            block_name = str(row["板块"]) if "板块" in hsgt_summary.columns else ""
            direction = str(row["资金方向"]) if "资金方向" in hsgt_summary.columns else ""
            net_buy = row["成交净买额"] if "成交净买额" in hsgt_summary.columns else None
            fund_inflow = row["资金净流入"] if "资金净流入" in hsgt_summary.columns else None
            up_count = row["上涨数"] if "上涨数" in hsgt_summary.columns else None
            down_count = row["下跌数"] if "下跌数" in hsgt_summary.columns else None
            related_index = str(row["相关指数"]) if "相关指数" in hsgt_summary.columns else ""
            index_change = row["指数涨跌幅"] if "指数涨跌幅" in hsgt_summary.columns else None

            entry: Dict[str, Any] = {}
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
        return hsgt_data
    except Exception as e:
        logger.error(f"获取沪深港通成交数据失败: {e}")
        return "获取失败"


def stock_north_etf_direction(api_cache) -> Optional[Dict[str, Any]]:
    """北向资金ETF方向指标（改进：以涨跌幅方向为主，成交额仅作活跃度参考）

    ⚠️ ETF成交额不区分买卖方向；ETF平均涨跌幅>0=外资看多，<0=外资看空。
    返回需要合并到 "沪深港通成交" 字段下的补充键值；若无可用数据返回 None。
    """
    try:
        etf_spot = api_cache.call("fund_etf_spot_em", ak.fund_etf_spot_em)
        if etf_spot is None or etf_spot.empty:
            return None
        north_etf = etf_spot[etf_spot["名称"].str.contains("A50|MSCI|互联互通|陆股通", na=False)]
        if north_etf.empty:
            return None
        avg_change = round(north_etf["涨跌幅"].mean(), 2)
        bull_count = int((north_etf["涨跌幅"] > 0).sum())
        bear_count = int((north_etf["涨跌幅"] < 0).sum())
        total_amount_yi = round(north_etf["成交额"].sum() / 1e8, 2)
        direction_label = "偏多" if avg_change > 0 else ("偏空" if avg_change < 0 else "中性")
        return {
            "北向ETF方向信号_平均涨跌幅(%)": float(round(avg_change, 2)),
            "北向ETF方向信号_涨跌方向": direction_label,
            "北向ETF上涨只数": bull_count,
            "北向ETF下跌只数": bear_count,
            "北向ETF成交额合计(亿,仅活跃度参考)": total_amount_yi,
            "说明": (
                "北向成交净买额已停止公布。"
                "【方向信号】北向ETF平均涨跌幅>0为偏多/外资看多，<0为偏空/外资看空，比成交额更可靠。"
                "【活跃度】ETF成交额仅反映活跃度，不代表资金净流向。"
            ),
        }
    except Exception as e:
        logger.error(f"获取北向资金ETF数据失败: {e}")
        return None


def stock_industry_concentration(api_cache) -> Any:
    """行业资金成交集中度（使用同花顺行业资金流数据，取前8）"""
    try:
        industry_flow = api_cache.call(
            "stock_fund_flow_industry:即时", ak.stock_fund_flow_industry, symbol="即时"
        )
        if industry_flow is None or industry_flow.empty:
            return "获取失败"
        top8 = industry_flow.head(8)
        return [
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
        return "获取失败"


def stock_up_down_count(api_cache) -> Any:
    """涨跌比统计（使用新浪数据源，东方财富接口被限制）"""
    try:
        stock_changes = api_cache.call("stock_zh_a_spot", ak.stock_zh_a_spot)
        if stock_changes is None or stock_changes.empty:
            return "获取失败"
        up_count = len(stock_changes[stock_changes["涨跌幅"] > 0])
        down_count = len(stock_changes[stock_changes["涨跌幅"] < 0])
        flat_count = len(stock_changes[stock_changes["涨跌幅"] == 0])
        total = up_count + down_count + flat_count
        return {
            "上涨": up_count,
            "下跌": down_count,
            "平盘": flat_count,
            "涨跌比": round(up_count / max(down_count, 1), 2),
            "上涨占比": round(up_count / max(total, 1) * 100, 2),
        }
    except Exception as e:
        logger.error(f"获取涨跌统计失败: {e}")
        return "获取失败"


# ============================================================
# 板块指标（compute_sector_indicators 的子项）
# ============================================================

def stock_board_industry_rank(api_cache) -> Any:
    """板块涨跌幅排行 top10（同花顺数据源，包含5日/10日涨跌幅）"""
    try:
        sector_rank = api_cache.call(
            "stock_board_industry_summary_ths", ak.stock_board_industry_summary_ths
        )
        if sector_rank is None or sector_rank.empty:
            return "获取失败"
        top_sectors = (
            sector_rank.nlargest(10, "涨跌幅")
            if "涨跌幅" in sector_rank.columns
            else sector_rank.head(10)
        )
        out: List[Dict[str, Any]] = []
        for _, row in top_sectors.iterrows():
            item: Dict[str, Any] = {}
            for col in sector_rank.columns:
                if col in ["序号"]:
                    continue
                val = row[col]
                item[col] = (
                    round(float(val), 2)
                    if pd.notna(val) and isinstance(val, (int, float))
                    else (str(val) if pd.notna(val) else "")
                )
            out.append(item)
        return out
    except Exception as e:
        logger.error(f"获取板块排行失败: {e}")
        return "获取失败"


def stock_zt_pool_stats(api_cache) -> Any:
    """涨停股统计（保留全部字段，尤其是连板数、所属行业）"""
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        zt_stocks = api_cache.call(
            f"stock_zt_pool_em:{today_str}", ak.stock_zt_pool_em, date=today_str
        )
        if zt_stocks is None or zt_stocks.empty:
            return "获取失败"
        return {
            "涨停数量": len(zt_stocks),
            "涨停股列表top30": [
                {col: str(row[col]) for col in zt_stocks.columns if col != "序号"}
                for _, row in zt_stocks.head(30).iterrows()
            ] if len(zt_stocks) > 0 else [],
        }
    except Exception as e:
        logger.error(f"获取涨停统计失败: {e}")
        return "获取失败"


def stock_lb_pool_stats(api_cache) -> Any:
    """连板股统计（强势股池：连续涨停2板及以上的股票，保留全字段）"""
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        lb_stocks = api_cache.call(
            f"stock_zt_pool_strong_em:{today_str}", ak.stock_zt_pool_strong_em, date=today_str
        )
        if lb_stocks is None or lb_stocks.empty:
            return "获取失败"
        # 过滤掉成交额为0的停牌股票
        if "成交额" in lb_stocks.columns:
            lb_stocks = lb_stocks[lb_stocks["成交额"].astype(float) > 0]
        lb_stocks_length = len(lb_stocks)
        max_len = 50 if lb_stocks_length > 50 else lb_stocks_length
        return {
            "强势股池数量": lb_stocks_length,
            "强势股池股列表top50": [
                {col: str(row[col]) for col in lb_stocks.columns if col != "序号"}
                for _, row in lb_stocks.head(max_len).iterrows()
            ] if lb_stocks_length > 0 else [],
        }
    except Exception as e:
        logger.error(f"获取连板统计失败: {e}")
        return "获取失败"


def stock_seal_ratio(api_cache) -> Any:
    """封板比（Seal Ratio）——衡量涨停板封单意愿强度

    封板比 = 封板资金 / 成交额，越高说明主力封板意愿越强、涨停越牢固
    """
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        # 复用已缓存的涨停池数据
        zt_for_seal = api_cache.call(
            f"stock_zt_pool_em:{today_str}", ak.stock_zt_pool_em, date=today_str
        )
        if zt_for_seal is None or zt_for_seal.empty:
            return "获取失败"
        seal_df = zt_for_seal.copy()
        # 过滤停牌（成交额为0）
        if "成交额" in seal_df.columns:
            seal_df = seal_df[pd.to_numeric(seal_df["成交额"], errors="coerce") > 0]
        if "封板资金" not in seal_df.columns or "成交额" not in seal_df.columns:
            return "获取失败"
        seal_df["_封板资金_num"] = pd.to_numeric(seal_df["封板资金"], errors="coerce")
        seal_df["_成交额_num"] = pd.to_numeric(seal_df["成交额"], errors="coerce")
        seal_df["_封板比"] = seal_df["_封板资金_num"] / seal_df["_成交额_num"].replace(0, float("nan"))
        valid = seal_df["_封板比"].dropna()
        if valid.empty:
            return "获取失败"
        avg_seal = round(float(valid.mean()), 3)
        high_seal_count = int((valid > 1.0).sum())   # 封板比>1意味着封单超过当日成交额，极牢固
        seal_ratios = [
            {
                "代码": str(row["代码"]) if "代码" in seal_df.columns else "",
                "名称": str(row["名称"]) if "名称" in seal_df.columns else "",
                "封板比": round(float(row["_封板比"]), 3),
                "连板数": str(row["连板数"]) if "连板数" in seal_df.columns else "",
            }
            for _, row in seal_df.nlargest(10, "_封板比").iterrows()
            if not pd.isna(row["_封板比"])
        ]
        return {
            "平均封板比": avg_seal,
            "封板比>1的个股数(极牢固)": high_seal_count,
            "封板比前10": seal_ratios,
            "说明": (
                "封板比 = 封板资金/成交额。>1表示封单超过全日成交额，主力锁仓意愿极强；"
                "平均封板比越高，整体涨停质量越好，明日溢价概率更高。"
            ),
        }
    except Exception as e:
        logger.error(f"计算封板比失败: {e}")
        return "获取失败"


def stock_broken_limit_rate(api_cache, zt_stats: Any) -> Any:
    """炸板率（Broken Limit Rate）——市场惜售意愿的反向指标

    炸板率 = 曾涨停但未收涨停数 / (当日涨停收盘数 + 炸板数)
    炸板率越高说明市场获利了结意愿强、情绪不稳定。
    zt_stats: 上游 stock_zt_pool_stats 的返回值，用来取"涨停数量"。
    """
    # Fix: 当涨停统计获取失败时（非dict），跳过炸板率计算，避免 0/(0+N)=100% 的误判
    if not isinstance(zt_stats, dict):
        return {"提示": "涨停数据获取失败，无法计算炸板率"}

    zt_closed = zt_stats.get("涨停数量", 0)
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        dtgc_stocks = api_cache.call(
            f"stock_zt_pool_dtgc_em:{today_str}", ak.stock_zt_pool_dtgc_em, date=today_str
        )
        if dtgc_stocks is None or dtgc_stocks.empty:
            return {
                "炸板数": 0,
                "涨停收盘数": zt_closed,
                "炸板率(%)": 0.0,
                "情绪判断": "情绪偏强（无炸板）",
            }
        broken_count = len(dtgc_stocks)
        total_attempts = zt_closed + broken_count
        broken_rate = round(broken_count / max(total_attempts, 1) * 100, 1)
        emotion_label = (
            "情绪极度不稳" if broken_rate >= 40 else
            "情绪偏弱" if broken_rate >= 25 else
            "情绪正常" if broken_rate >= 10 else
            "情绪偏强"
        )
        return {
            "炸板数": broken_count,
            "涨停收盘数": zt_closed,
            "炸板率(%)": broken_rate,
            "情绪判断": emotion_label,
            "炸板股列表(前10)": [
                {col: str(row[col]) for col in dtgc_stocks.columns if col != "序号"}
                for _, row in dtgc_stocks.head(10).iterrows()
            ],
            "说明": (
                "炸板率 = 炸板数/(涨停收盘数+炸板数)。"
                "<10%为情绪偏强（主力锁仓）；≥25%为情绪偏弱（散户派发）；≥40%为情绪极度不稳，慎追板。"
            ),
        }
    except Exception as e:
        logger.error(f"计算炸板率失败: {e}")
        return "获取失败"


# ============================================================
# 合力指标（compute_force_indicators 的子项）
# ============================================================

def stock_industry_fund_flow(api_cache) -> Any:
    """行业资金流向 top20（同花顺行业资金流，替代东方财富接口）"""
    try:
        industry_fund = api_cache.call(
            "stock_fund_flow_industry:即时", ak.stock_fund_flow_industry, symbol="即时"
        )
        if industry_fund is None or industry_fund.empty:
            return "获取失败"
        out: List[Dict[str, Any]] = []
        for _, row in industry_fund.head(20).iterrows():
            item: Dict[str, Any] = {}
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
            out.append(item)
        return out
    except Exception as e:
        logger.error(f"获取主力资金流向失败: {e}")
        return "获取失败"


def stock_individual_fund_flow(api_cache) -> Any:
    """个股资金流向 top20（同花顺个股资金流，只保留主板股票）

    排除科创板(688)、创业板(300/301)、北交所(8开头)
    """
    try:
        stock_fund = api_cache.call(
            "stock_fund_flow_individual:即时", ak.stock_fund_flow_individual, symbol="即时"
        )
        if stock_fund is None or stock_fund.empty:
            return "获取失败"
        # 过滤：只保留主板股票
        if "股票代码" in stock_fund.columns:
            stock_fund = stock_fund[stock_fund["股票代码"].astype(str).apply(is_main_board_stock)]
            logger.info(f"个股资金流向过滤后剩余主板股票: {len(stock_fund)} 条")

        # 按净额降序排序，确保 top20 是资金净流入最多的主板股
        if "净额" in stock_fund.columns:
            stock_fund = stock_fund.sort_values("净额", ascending=False)
        top_inflow = stock_fund.head(20)
        out: List[Dict[str, Any]] = []
        for _, row in top_inflow.iterrows():
            item: Dict[str, Any] = {}
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
            out.append(item)
        return out
    except Exception as e:
        logger.error(f"获取个股资金流向失败: {e}")
        return "获取失败"


# ============================================================
# 龙头指标（compute_leader_indicators 的子项）
# ============================================================

def stock_zt_pool_leader(api_cache) -> Any:
    """涨停池龙头候选 top20（过滤掉成交额为0的停牌股票）"""
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        zt_pool = api_cache.call(
            f"stock_zt_pool_em:{today_str}", ak.stock_zt_pool_em, date=today_str
        )
        if zt_pool is None or zt_pool.empty:
            return "获取失败"
        # 过滤掉成交额为0的停牌股票
        if "成交额" in zt_pool.columns:
            zt_pool = zt_pool[zt_pool["成交额"].astype(float) > 0]
        zt_pool_length = len(zt_pool)
        max_len = 20 if zt_pool_length > 20 else zt_pool_length
        out: List[Dict[str, Any]] = []
        for _, row in zt_pool.head(max_len).iterrows():
            item: Dict[str, Any] = {}
            for col in zt_pool.columns:
                if col == "序号":
                    continue
                item[col] = str(row[col])
            out.append(item)
        return out
    except Exception as e:
        logger.error(f"获取涨停池失败: {e}")
        return "获取失败"


def stock_strong_rank(api_cache) -> Any:
    """强势股（涨幅前20，使用新浪数据源，只保留主板）"""
    try:
        stock_rank = api_cache.call("stock_zh_a_spot", ak.stock_zh_a_spot)
        if stock_rank is None or stock_rank.empty:
            return "获取失败"
        # 过滤主板，避免科创/创业/北交混入
        if "代码" in stock_rank.columns:
            stock_rank = stock_rank[stock_rank["代码"].astype(str).apply(is_main_board_stock)]
        top_stocks = stock_rank.nlargest(20, "涨跌幅")
        out: List[Dict[str, Any]] = []
        for _, row in top_stocks.iterrows():
            item: Dict[str, Any] = {}
            for col in ["代码", "名称", "最新价", "涨跌幅", "成交量", "成交额", "换手率"]:
                if col in stock_rank.columns:
                    item[col] = str(row[col])
            out.append(item)
        return out
    except Exception as e:
        logger.error(f"获取强势股排行失败: {e}")
        return "获取失败"


# ============================================================
# 风险指标（compute_risk_indicators 的子项）
# ============================================================

def stock_new_list(api_cache) -> Any:
    """次新股（使用新浪数据源，东方财富接口被限制）"""
    try:
        new_stocks = api_cache.call("stock_zh_a_new", ak.stock_zh_a_new)
        if new_stocks is None or new_stocks.empty:
            return "获取失败"
        return {
            "数量": len(new_stocks),
            "部分列表": [
                {
                    "代码": str(row["code"]) if "code" in new_stocks.columns else "",
                    "名称": str(row["name"]) if "name" in new_stocks.columns else "",
                }
                for _, row in new_stocks.head(20).iterrows()
            ],
        }
    except Exception as e:
        logger.error(f"获取次新股失败: {e}")
        return "获取失败"


def stock_candidate_spot(api_cache, candidate_stock_codes: List[str]) -> Any:
    """候选股票实时行情（让LLM能真正评估涨幅异常、流动性、ST等）"""
    if not candidate_stock_codes:
        return None
    try:
        spot = api_cache.call("stock_zh_a_spot", ak.stock_zh_a_spot)
        if spot is None or spot.empty or "代码" not in spot.columns:
            return "获取失败"
        target = spot[spot["代码"].isin(candidate_stock_codes)]
        if target.empty:
            return f"未找到候选代码 {candidate_stock_codes} 的实时数据"
        return [
            {
                col: str(row[col])
                for col in [
                    "代码", "名称", "最新价", "涨跌幅", "成交量", "成交额",
                    "换手率", "振幅", "52周最高", "52周最低",
                ]
                if col in spot.columns
            }
            for _, row in target.iterrows()
        ]
    except Exception as e:
        logger.error(f"获取候选标的实时行情失败: {e}")
        return "获取失败"


def stock_candidate_fundamentals(api_cache, candidate_stock_codes: List[str]) -> Any:
    """候选股票基本面数据（防止纯资金流策略选出垃圾股）

    获取 PE、PB、总市值等基础财务指标，作为基本面安全底线过滤。
    最多取前3支，控制API调用次数。
    """
    if not candidate_stock_codes:
        return None
    try:
        fundamentals: Dict[str, Any] = {}
        for code in candidate_stock_codes[:3]:
            try:
                info_df = api_cache.call(
                    f"stock_individual_info_em:{code}",
                    ak.stock_individual_info_em,
                    symbol=code,
                )
                if (info_df is not None and not info_df.empty
                        and "item" in info_df.columns and "value" in info_df.columns):
                    info_dict = dict(zip(info_df["item"].astype(str), info_df["value"].astype(str)))
                    fundamentals[code] = {
                        "市盈率_动(PE)": info_dict.get("市盈率(动)", "N/A"),
                        "市净率(PB)": info_dict.get("市净率", "N/A"),
                        "总市值": info_dict.get("总市值", "N/A"),
                        "流通市值": info_dict.get("流通市值", "N/A"),
                        "所属行业": info_dict.get("行业", "N/A"),
                    }
                else:
                    fundamentals[code] = {"提示": "无法获取基本面数据"}
            except Exception as e_code:
                logger.error(f"获取候选股基本面数据失败 {code}: {e_code}")
                fundamentals[code] = {"提示": f"获取失败: {e_code}"}
        return fundamentals if fundamentals else None
    except Exception as e:
        logger.error(f"批量获取候选标的基本面失败: {e}")
        return "获取失败"


