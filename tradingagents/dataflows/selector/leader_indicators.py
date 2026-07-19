"""龙头指标计算模块"""
import pandas as pd
from datetime import datetime
from typing import Dict, List
from tradingagents.utils.common_utils import ak_date_call_with_fallback, call_provider_method
from tradingagents.dataflows.providers.china.akshare import AKShareProvider

import logging
logger = logging.getLogger(__name__)

_provider = AKShareProvider()


def compute_leader_indicators(date_str: str, quality_stocks: List[Dict]) -> str:
    """综合计算并格式化龙头指标报告"""
    logger.info(f"[龙头指标] 开始计算: {date_str}")

    date_fmt   = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y%m%d")
    stock_codes = [str(s.get("code", "")).strip() for s in quality_stocks if str(s.get("code", "")).strip()]

    zt_str = _zt_leader_simple(date_fmt, stock_codes)
    strong_str = _strong_rank_simple(date_fmt, stock_codes)
    basic_info_str = _basic_info_simple(stock_codes)

    report = f"""# 股票龙头指标分析报告

**分析日期**: {date_str}

**优质标的**:
{stock_codes}

## 股票基本信息
{basic_info_str}

## 涨停股连板统计
{zt_str}

## 强势股排名
{strong_str}

## 龙头股筛选建议
基于上述数据，请从优质标的中筛选龙头股：
1. 连板高度最高（优先选择3连板以上）
2. 板块内排名靠前
3. 成交量放大（换手率5%-15%为佳）
4. 筛选出1-2支龙头股
5. 不要选已经涨停的票，因为已经涨停的目前买不到
"""
    logger.info(f"[龙头指标] 报告生成完成，长度: {len(report)}")
    return report


def _basic_info_simple(stock_codes: List[str]) -> str:
    """获取股票的基础信息（精选字段，按业务逻辑分类展示）"""
    candidates = [str(code).strip() for code in (stock_codes or []) if str(code).strip()]
    if not candidates:
        return "未提供待分析股票"

    try:
        quotes_map = call_provider_method(_provider, "get_batch_stock_quotes", candidates) or {}
    except Exception as e:
        logger.error(f"[龙头指标] 批量行情获取失败: {e}")
        return f"批量行情获取失败: {e}"

    if not isinstance(quotes_map, dict):
        return "批量行情返回格式异常"

    lines: List[str] = []
    for code in candidates[:3]:
        quote = quotes_map.get(code)
        if not isinstance(quote, dict) or not quote:
            lines.append(f"- {code}：未获取到批量行情数据")
            continue

        lines.append(f"- {code} 行情信息：")

        # 基本信息
        if quote.get("name"):
            lines.append(f"  名称: {quote['name']}")
        if quote.get("full_symbol"):
            lines.append(f"  完整代码: {quote['full_symbol']}")
        market_info = quote.get("market_info")
        if isinstance(market_info, dict) and market_info.get("exchange_name"):
            lines.append(f"  市场: {market_info['exchange_name']}")

        # 价格信息
        price = quote.get("price")
        if price:
            lines.append(f"  现价: {float(price):.2f}元")
        pre_close = quote.get("pre_close")
        if pre_close:
            lines.append(f"  昨收: {float(pre_close):.2f}元")

        change = quote.get("change")
        change_percent = quote.get("change_percent")
        if change is not None and change_percent is not None:
            change_val = float(change)
            chg_pct = float(change_percent)
            lines.append(f"  涨跌: {change_val:+.2f}元 ({chg_pct:+.2f}%)")

        open_price = quote.get("open_price")
        high_price = quote.get("high_price")
        low_price = quote.get("low_price")
        if all(v is not None for v in [open_price, high_price, low_price]):
            lines.append(f"  开盘: {float(open_price):.2f}元 | 最高: {float(high_price):.2f}元 | 最低: {float(low_price):.2f}元")

        # 成交信息
        volume = quote.get("volume")
        amount = quote.get("amount")
        if volume and amount:
            vol_val = int(volume)
            amt_val = float(amount)
            # 金额单位转亿元
            amt_yi = amt_val / 1e8
            lines.append(f"  成交量: {vol_val:,} | 成交额: {amt_yi:.2f}亿元")

        turnover_rate = quote.get("turnover_rate")
        volume_ratio = quote.get("volume_ratio")
        if turnover_rate and float(turnover_rate) > 0:
            lines.append(f"  换手率: {float(turnover_rate):.2f}%")
        if volume_ratio and float(volume_ratio) > 0:
            lines.append(f"  量比: {float(volume_ratio):.2f}")

        # 财务指标（过滤掉 0.0 和 N/A）
        financial_items = []
        pe = quote.get("pe")
        if pe and float(pe) > 0:
            financial_items.append(f"PE: {float(pe):.2f}")
        pb = quote.get("pb")
        if pb and float(pb) > 0:
            financial_items.append(f"PB: {float(pb):.2f}")
        total_mv = quote.get("total_mv")
        if total_mv and total_mv != "N/A":
            total_mv_val = float(total_mv) / 1e8 if isinstance(total_mv, (int, float)) else 0
            if total_mv_val > 0:
                financial_items.append(f"总市值: {total_mv_val:.2f}亿元")
        circ_mv = quote.get("circ_mv")
        if circ_mv and circ_mv != "N/A":
            circ_mv_val = float(circ_mv) / 1e8 if isinstance(circ_mv, (int, float)) else 0
            if circ_mv_val > 0:
                financial_items.append(f"流通市值: {circ_mv_val:.2f}亿元")

        if financial_items:
            lines.append(f"  财务指标: {' | '.join(financial_items)}")

    return "\n".join(lines)


def _zt_leader_simple(date_fmt: str, stock_codes: List[str] = None) -> str:
    """涨停连板情况"""
    try:
        import akshare as ak
        df, data_date = ak_date_call_with_fallback("stock_zt_pool_em", ak.stock_zt_pool_em, date_fmt, "[龙头-涨停池]")
        if df is None or df.empty:
            return "涨停连板数据暂不可用"

        lines = []
        if data_date != date_fmt:
            lines.append(f"注：{date_fmt} 超出可查窗口，以下为最新交易日 {data_date} 数据")

        # 过滤掉无成交额（集合竞价未完成等异常行）
        if "成交额" in df.columns:
            df = df[pd.to_numeric(df["成交额"], errors="coerce").fillna(0) > 0]

        if "连板数" not in df.columns or "代码" not in df.columns:
            lines.append("涨停池字段不足，无法进行连板比对")
            return "\n".join(lines)

        df_multi = df[df["连板数"] >= 2].copy()
        if df_multi.empty:
            lines.append("当日无连板股")
            return "\n".join(lines)

        candidates = [str(c).strip() for c in (stock_codes or []) if str(c).strip()]
        if not candidates:
            lines.append("未提供待分析股票，无法判断是否在连板股中")
            return "\n".join(lines)

        df_multi["代码"] = df_multi["代码"].astype(str).str.strip()
        matched = df_multi[df_multi["代码"].isin(candidates)].sort_values("连板数", ascending=False)

        if matched.empty:
            lines.append("待分析股票均不在连板股列表中")
        else:
            lines.append("待分析股票连板情况：")
            for _, row in matched.iterrows():
                code = str(row.get("代码", ""))
                name = str(row.get("名称", ""))
                sector = str(row.get("所属行业", ""))
                days = int(pd.to_numeric(row.get("连板数", 1), errors="coerce") or 1)
                lines.append(f"  ✅ {code} {name}（{sector}）：{days} 连板")
        return "\n".join(lines)
    except Exception as e:
        return f"涨停连板数据获取失败: {e}"


def _strong_rank_simple(date_fmt: str, stock_codes: List[str]) -> str:
    """强势股池，并比对候选标的是否入选"""
    try:
        import akshare as ak
        df, data_date = ak_date_call_with_fallback("stock_zt_pool_strong_em", ak.stock_zt_pool_strong_em, date_fmt, "[强势股池]")
        if df is None or df.empty:
            return "强势股排名数据暂不可用"

        lines = []
        if data_date != date_fmt:
            lines.append(f"注：{date_fmt} 超出可查窗口，以下为最新交易日 {data_date} 数据")
        lines.append(f"强势股池总数：{len(df)} 只")

        if stock_codes and "代码" in df.columns:
            matched = df[df["代码"].isin(stock_codes)]
            if not matched.empty:
                lines.append("优质标的在强势股池中的情况：")
                for _, row in matched.iterrows():
                    code = str(row.get("代码", ""))
                    name = str(row.get("名称", ""))
                    lines.append(f"  ✅ {code} {name}：已进入强势股池")
            else:
                lines.append("优质标的未进入强势股池（关注连板高度）")
        return "\n".join(lines)
    except Exception as e:
        return f"强势股排名获取失败: {e}"
