"""板块指标计算模块"""
import pandas as pd
from datetime import datetime
from tradingagents.utils.common_utils import cached_ak_call, ak_date_call_with_fallback, _latest_date_fmt

import logging
logger = logging.getLogger(__name__)


def compute_sector_indicators(date_str: str) -> str:
    """综合计算并格式化板块指标报告"""
    logger.info(f"[板块指标] 开始计算: {date_str}")

    date_fmt = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y%m%d")

    sector_rank_str    = _sector_rank_simple()
    zt_str, zt_count   = _zt_pool_simple(date_fmt)
    lb_str             = _lb_pool_simple(date_fmt)
    seal_str           = _seal_ratio_simple(date_fmt)
    broken_str         = _broken_rate_simple(date_fmt, zt_count)

    report = f"""# A股板块指标分析报告

**分析日期**: {date_str}

## 涨幅前10板块
{sector_rank_str}

## 涨停统计
{zt_str}

## 强势股池统计
{lb_str}

## 封板比统计
{seal_str}

## 炸板率统计
{broken_str}

## 板块筛选建议
基于上述数据，请综合分析哪些板块具有主线特征（涨幅领先 + 涨停集中 + 封板比高 + 炸板率低），
筛选出2-3个候选板块，如果只有1个符合条件可直接确认。

**关键指标参考**:
- 封板比 > 1: 主力锁仓意愿强
- 炸板率 < 10%: 情绪稳定
- 炸板率 >= 25%: 情绪偏弱
- 炸板率 >= 40%: 极度不稳
"""
    logger.info(f"[板块指标] 报告生成完成，长度: {len(report)}")
    return report


def _sector_rank_simple() -> str:
    """同花顺行业板块涨幅排行"""
    try:
        import akshare as ak
        df = cached_ak_call("stock_board_industry_summary_ths", ak.stock_board_industry_summary_ths, expire=600)
        if df is None or df.empty:
            return "板块涨幅数据暂不可用"
        sort_col = next((c for c in ["涨跌幅", "涨幅"] if c in df.columns), None)
        name_col = next((c for c in ["板块", "板块名称", "名称"] if c in df.columns), df.columns[0])
        df_sorted = df.sort_values(sort_col, ascending=False).head(10) if sort_col else df.head(10)
        lines = ["涨幅前10板块："]
        for i, (_, row) in enumerate(df_sorted.iterrows(), 1):
            name = str(row.get(name_col, f"板块{i}"))
            pct  = pd.to_numeric(row.get(sort_col, 0) if sort_col else 0, errors="coerce")
            lines.append(f"  {i}. {name}：{float(pct) if pd.notna(pct) else 0.0:+.2f}%")
        return "\n".join(lines)
    except Exception as e:
        return f"板块涨幅获取失败: {e}"


def _zt_pool_simple(date_fmt: str):
    """涨停池统计，返回 (描述字符串, 涨停数量)"""
    try:
        import akshare as ak
        df, data_date = ak_date_call_with_fallback("stock_zt_pool_em", ak.stock_zt_pool_em, date_fmt, "[涨停池]")
        if df is None or df.empty:
            return "涨停数据暂不可用", 0
        total = len(df)
        lines = []
        if data_date != date_fmt:
            lines.append(f"注：{date_fmt} 超出可查窗口，以下为最新交易日 {data_date} 数据")
        lines.append(f"涨停股总数：{total} 只")
        if "连板数" in df.columns:
            multi = df[df["连板数"] >= 2]
            lines.append(f"连板股数量：{len(multi)} 只")
            if not multi.empty:
                lines.append(f"最高连板数：{int(multi['连板数'].max())} 连板")
        if "所属行业" in df.columns:
            lines.append("涨停集中板块（前5）：")
            for sector, cnt in df["所属行业"].value_counts().head(5).items():
                lines.append(f"  - {sector}：{cnt} 只")
        return "\n".join(lines), total
    except Exception as e:
        return f"涨停统计获取失败: {e}", 0


def _lb_pool_simple(date_fmt: str) -> str:
    """强势股池统计"""
    try:
        import akshare as ak
        df, data_date = ak_date_call_with_fallback("stock_zt_pool_strong_em", ak.stock_zt_pool_strong_em, date_fmt, "[强势股池]")
        if df is None or df.empty:
            return "强势股池数据暂不可用"
        lines = []
        if data_date != date_fmt:
            lines.append(f"注：{date_fmt} 超出可查窗口，以下为最新交易日 {data_date} 数据")
        lines.append(f"强势股池总数：{len(df)} 只")
        if "所属行业" in df.columns:
            lines.append("集中板块（前3）：")
            for s, c in df["所属行业"].value_counts().head(3).items():
                lines.append(f"  - {s}：{c} 只")
        return "\n".join(lines)
    except Exception as e:
        return f"强势股池获取失败: {e}"


def _seal_ratio_simple(date_fmt: str) -> str:
    """平均封板比"""
    try:
        import akshare as ak
        df, data_date = ak_date_call_with_fallback("stock_zt_pool_em", ak.stock_zt_pool_em, date_fmt, "[封板比]")
        if df is None or df.empty:
            return "封板比数据暂不可用"
        if "封板资金" not in df.columns or "成交额" not in df.columns:
            return f"封板比字段不存在（列：{list(df.columns)}）"
        df = df.copy()
        amount = pd.to_numeric(df["成交额"], errors="coerce").replace(0, float("nan"))
        seal   = pd.to_numeric(df["封板资金"], errors="coerce")
        ratio  = (seal / amount).dropna()
        if ratio.empty:
            return "封板比计算失败"
        avg  = round(float(ratio.mean()), 2)
        high = int((ratio > 1.0).sum())
        lines = []
        if data_date != date_fmt:
            lines.append(f"注：{date_fmt} 超出可查窗口，以下为最新交易日 {data_date} 数据")
        lines.append(f"平均封板比：{avg}")
        lines.append(f"封板比>1（极牢固）：{high} 只")
        lines.append("评估：" + ("主力锁仓意愿强" if avg > 1 else ("封板意愿一般" if avg >= 0.5 else "炸板风险偏高")))
        return "\n".join(lines)
    except Exception as e:
        return f"封板比获取失败: {e}"


def _broken_rate_simple(date_fmt: str, zt_count: int) -> str:
    """炸板率"""
    try:
        import akshare as ak
        df, data_date = ak_date_call_with_fallback("stock_zt_pool_dtgc_em", ak.stock_zt_pool_dtgc_em, date_fmt, "[炸板池]")
        zb    = len(df) if (df is not None and not df.empty) else 0
        total = zt_count + zb
        rate  = round(zb / max(total, 1) * 100, 1)
        label = (
            "极度不稳，高风险" if rate >= 40 else
            "情绪偏弱"        if rate >= 25 else
            "情绪稳定"        if rate >= 10 else
            "极稳定，惜售意愿强"
        )
        lines = []
        if data_date != date_fmt:
            lines.append(f"注：{date_fmt} 超出可查窗口，以下为最新交易日 {data_date} 数据")
        lines.append(f"炸板家数：{zb} 只")
        lines.append(f"炸板率：{rate}%")
        lines.append(f"评估：{label}")
        return "\n".join(lines)
    except Exception as e:
        return f"炸板率计算失败: {e}"
