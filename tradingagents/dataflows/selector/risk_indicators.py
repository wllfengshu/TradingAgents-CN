"""风险指标计算模块"""
from typing import Dict, List
from tradingagents.utils.common_utils import (
    call_akshare_with_retry,
    cached_ak_call,
    call_provider_method,
    extract_financial_metrics,
    normalize_stock_code,
    normalize_stock_name,
    is_high_risk_stock_name
)
from tradingagents.dataflows.providers.china.akshare import AKShareProvider

import logging
logger = logging.getLogger(__name__)

_provider = AKShareProvider()
_EXPIRE_NEW_STOCKS = 8 * 3600


def compute_risk_indicators(date_str: str, leading_stocks: List[Dict]) -> str:
    """综合计算并格式化风险指标报告"""
    logger.info(f"[风险指标] 开始计算: {date_str}")

    stock_codes = [normalize_stock_code(s.get("code", "")) for s in leading_stocks]
    if not stock_codes:
        return "无候选股票"

    risky_by_name = []
    safe_codes = []
    for item in leading_stocks:
        code = normalize_stock_code(item.get("code", ""))
        if not code:
            continue

        name = normalize_stock_name(item.get("name", "") or item.get("名称", "") or item.get("简称", ""))
        if not name:
            basic_info = call_provider_method(_provider, "get_stock_basic_info", code) or {}
            name = normalize_stock_name(basic_info.get("name", ""))

        if is_high_risk_stock_name(name):
            risky_by_name.append(f"{code} {name}".strip())
        else:
            safe_codes.append(code)

    new_str = _new_stock_simple(safe_codes)
    fundamental_str = _fundamentals_simple(safe_codes) if safe_codes else "所有候选股已被名称风险过滤，无需进一步基本面分析。"

    risky_name_str = "无"
    if risky_by_name:
        risky_name_str = "\n".join(f"  - {item}" for item in risky_by_name)

    report = f"""# 风险指标分析报告

**分析日期**: {date_str}

**待评估股票**:
{stock_codes}

## 名称前缀高风险过滤（直接剔除）
{risky_name_str}

## 排除新股
{new_str}

## 基本面分析
{fundamental_str}

## 风险评估建议
基于上述数据，请对龙头股进行风险评估：
1. 排除ST股票（名称含"ST"或"*ST"）
2. 排除上市不足30天的新股
3. 排除有退市风险的股票
4. 评估财务状况（PE、PB、负债率）
5. 给出风险等级（低/中/高）
6. 输出安全标的列表

**风险等级判定**:
- 低风险：无ST、无退市风险、财务健康
- 中风险：存在一定财务压力，但无重大风险
- 高风险：存在ST、退市风险、财务恶化
"""
    logger.info(f"[风险指标] 报告生成完成，长度: {len(report)}")
    return report


def _new_stock_simple(stock_codes: List[str]) -> str:
    """按规则直接判定候选股中的新股（只要在新股列表中即排除）。"""
    try:
        import akshare as ak
        df = cached_ak_call(
            "stock_zh_a_new",
            lambda: call_akshare_with_retry(ak.stock_zh_a_new, context="获取新股列表"),
            expire=_EXPIRE_NEW_STOCKS,
        )
        if df is None or df.empty:
            return "新股列表数据暂不可用"

        name_col = next((c for c in ["名称", "name", "简称"] if c in df.columns), None)
        code_col = next((c for c in ["代码", "code", "symbol"] if c in df.columns), None)

        if code_col is None:
            return "新股规则判定暂不可用（接口缺少代码字段），请谨慎人工复核。"

        candidate_codes = {normalize_stock_code(c) for c in stock_codes if normalize_stock_code(c)}
        recent_codes = {
            normalize_stock_code(row.get(code_col, ""))
            for _, row in df.iterrows()
            if normalize_stock_code(row.get(code_col, ""))
        }
        excluded_codes = sorted(candidate_codes & recent_codes)
        if not excluded_codes:
            return "新股规则判定：候选股不在新股列表中。"

        code_name_map = {}
        for _, row in df.iterrows():
            code_norm = normalize_stock_code(row.get(code_col, ""))
            if code_norm and code_norm in excluded_codes:
                code_name_map[code_norm] = str(row.get(name_col, "")) if name_col else ""

        excluded_display = [
            f"{code} {code_name_map.get(code, '').strip()}".strip()
            for code in excluded_codes
        ]
        return (
            "新股规则判定：候选股中发现位于新股列表的标的，已标记为应排除。\n"
            f"应排除：{', '.join(excluded_display)}"
        )
    except Exception as e:
        return f"新股列表获取失败: {e}"


def _fundamentals_simple(stock_codes: List[str]) -> str:
    """获取个股基本面数据（名称、行业、PE、PB、负债率等）。"""
    codes = [normalize_stock_code(code) for code in stock_codes if normalize_stock_code(code)]
    if not codes:
        return "无候选股票"

    blocks = []
    for code in codes[:3]:
        info = _fetch_stock_fundamentals(code)
        blocks.append(
            "\n".join(
                [
                    f"基本面数据（{code}）",
                    f"- 名称: {info.get('name', 'N/A')}",
                    f"- 行业: {info.get('industry', 'N/A')}",
                    f"- 地区: {info.get('area', 'N/A')}",
                    f"- 上市日期: {info.get('list_date', 'N/A')}",
                    f"- PE: {info.get('pe', 'N/A')}",
                    f"- PB: {info.get('pb', 'N/A')}",
                    f"- 资产负债率: {info.get('debt_ratio', 'N/A')}",
                ]
            )
        )

    if len(codes) > 3:
        blocks.append(f"...其余 {len(codes) - 3} 只股票已省略")

    return "\n\n".join(blocks)


def _fetch_stock_fundamentals(code: str) -> Dict[str, str]:
    """获取单只股票的基础信息和财务指标。"""
    basic_info = call_provider_method(_provider, "get_stock_basic_info", code) or {}
    financial_data = call_provider_method(_provider, "get_financial_data", code) or {}

    return {
        "code": normalize_stock_code(code),
        "name": normalize_stock_name(basic_info.get("name", f"股票{code}")),
        "industry": normalize_stock_name(basic_info.get("industry", "未知")),
        "area": normalize_stock_name(basic_info.get("area", "未知")),
        "list_date": normalize_stock_name(basic_info.get("list_date", "")) or "未知",
        **extract_financial_metrics(financial_data),
    }
