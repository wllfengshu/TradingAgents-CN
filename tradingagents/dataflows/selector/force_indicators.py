"""合力指标计算模块"""
import pandas as pd
from typing import List
from tradingagents.utils.common_utils import to_yi, is_main_board_stock,call_akshare_with_retry

import logging
logger = logging.getLogger(__name__)


def compute_force_indicators(date_str: str, confirmed_sectors: List[str]) -> str:
    """综合计算并格式化合力指标报告"""
    logger.info(f"[合力指标] 开始计算: {date_str}, 主线板块: {confirmed_sectors}")

    industry_flow_str  = _industry_flow_simple(confirmed_sectors)
    individual_flow_str = _individual_flow_simple()

    report = f"""# 市场合力指标分析报告

**分析日期**: {date_str}
**主线板块**:
{confirmed_sectors}

## 板块资金流向
{industry_flow_str}

## 个股资金流向（TOP20，主板）
{individual_flow_str}

## 合力股票筛选建议
基于上述数据，请从主线板块中筛选合力股票：
1. 主力净流入排名靠前（TOP10）
2. 换手率适中（3%-10%为佳）
3. 属于确认的主线板块
4. 筛选出2-3支候选股票

**合力方向判断**:
- 正向共振：主力+散户同向流入
- 反向分歧：主力流入+散户流出（或相反）
- 主力主导：主力大幅流入，散户观望
"""
    logger.info(f"[合力指标] 报告生成完成，长度: {len(report)}")
    return report


def _industry_flow_simple(confirmed_sectors: List[str]) -> str:
    """行业资金流向净流入前20"""
    try:
        import akshare as ak
        df = call_akshare_with_retry(
            lambda: ak.stock_fund_flow_industry(symbol="即时"),
            "[合力-行业资金流向]"
        )
        if df is None or df.empty:
            return "行业资金流向数据暂不可用"

        # 找净额列（列名因版本不同可能不同）
        sort_col = next((c for c in df.columns if "净" in c and ("额" in c or "入" in c)), None)
        name_col = next((c for c in df.columns if "行业" in c or "名称" in c), df.columns[0])

        # 转换为数值后排序
        if sort_col:
            df = df.copy()
            df['_sort_val'] = df[sort_col].apply(to_yi)
            df_sorted = df.sort_values('_sort_val', ascending=False).head(20)
        else:
            df_sorted = df.head(20)

        lines = ["行业资金流向（净流入前20）："]
        for i, (_, row) in enumerate(df_sorted.iterrows(), 1):
            name = str(row.get(name_col, f"行业{i}"))
            flow_raw = row.get(sort_col, 0) if sort_col else 0
            flow = to_yi(flow_raw)
            mark = " ★" if any(s in name for s in confirmed_sectors) else ""
            lines.append(f"  {i}. {name}{mark}：{flow:.2f} 亿元")
        return "\n".join(lines)
    except Exception as e:
        return f"行业资金流向获取失败: {e}"


def _individual_flow_simple() -> str:
    """主板个股主力净流入TOP20"""
    try:
        import akshare as ak
        df = call_akshare_with_retry(
            lambda: ak.stock_fund_flow_individual(symbol="即时"),
            "[合力-个股资金流向]"
        )
        if df is None or df.empty:
            return "个股资金流向数据暂不可用"

        code_col = next((c for c in df.columns if "代码" in c), None)
        name_col = next((c for c in df.columns if "简称" in c or "名称" in c), df.columns[0])
        sort_col = next((c for c in df.columns if "净" in c and ("额" in c or "入" in c)), None)

        if code_col:
            df = df[df[code_col].astype(str).apply(is_main_board_stock)]

        # 转换为数值后排序
        if sort_col:
            df = df.copy()
            df['_sort_val'] = df[sort_col].apply(to_yi)
            df_sorted = df.sort_values('_sort_val', ascending=False).head(20)
        else:
            df_sorted = df.head(20)

        lines = []
        for i, (_, row) in enumerate(df_sorted.iterrows(), 1):
            code = str(row.get(code_col, "")) if code_col else ""
            name = str(row.get(name_col, f"股票{i}"))
            flow_raw = row.get(sort_col, 0) if sort_col else 0
            flow = to_yi(flow_raw)
            lines.append(f"  {i}. {code} {name}：{flow:.2f} 亿元")
        return "\n".join(lines)
    except Exception as e:
        return f"个股资金流向获取失败: {e}"
