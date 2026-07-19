"""大盘指标计算模块"""
import pandas as pd
from tradingagents.utils.common_utils import to_yi, cached_ak_call

import logging
logger = logging.getLogger(__name__)


def compute_market_indicators(date_str: str) -> str:
    """综合计算并格式化大盘指标报告"""
    logger.info(f"[大盘指标] 开始计算: {date_str}")

    sh_line  = _index_daily_simple("sh000001", "上证指数", date_str)
    sz_line  = _index_daily_simple("sz399001", "深证成指", date_str)
    cyb_line = _index_daily_simple("sz399006", "创业板指", date_str)
    northbound_line = _northbound_simple(date_str)
    breadth_line    = _breadth_simple()

    report = f"""# A股大盘指标分析报告

**分析日期**: {date_str}

## 主要指数行情
{sh_line}
{sz_line}
{cyb_line}

## 北向资金流向
{northbound_line}

## 涨跌家数统计
{breadth_line}

## 市场情绪评估
基于上述数据，请综合分析大盘环境，给出市场情绪判断（偏多/偏空/中性）。
"""
    logger.info(f"[大盘指标] 报告生成完成，长度: {len(report)}")
    return report


def _index_daily_simple(symbol: str, name: str, date_str: str) -> str:
    """获取单个指数近期数据"""
    try:
        import akshare as ak
        df = cached_ak_call("stock_zh_index_daily", ak.stock_zh_index_daily, expire=600, symbol=symbol)
        if df is None or df.empty:
            return f"{name}：数据不可用"
        df["date"] = pd.to_datetime(df["date"])
        df_filtered = df[df["date"] <= pd.to_datetime(date_str)].tail(6)
        if df_filtered.empty:
            return f"{name}：数据不可用"
        latest = df_filtered.iloc[-1]
        prev   = df_filtered.iloc[-2] if len(df_filtered) >= 2 else latest
        change_pct = (latest["close"] - prev["close"]) / prev["close"] * 100 if prev["close"] else 0
        change5d   = (latest["close"] - df_filtered.iloc[0]["close"]) / df_filtered.iloc[0]["close"] * 100
        return (f"{name}：收盘 {latest['close']:.2f}，"
                f"今日 {change_pct:+.2f}%，5日 {change5d:+.2f}%，"
                f"成交量 {latest.get('volume', 0):.0f}手")
    except Exception as e:
        logger.warning(f"获取{name}数据失败: {e}")
        return f"{name}：数据获取失败"


def _northbound_simple(date_str: str) -> str:
    """北向资金净买额"""
    try:
        import akshare as ak
        # stock_hsgt_hist_em 返回：日期 / 当日成交净买额 / 当日资金流入 等列
        df = cached_ak_call("stock_hsgt_hist_em", ak.stock_hsgt_hist_em, expire=600, symbol="北向资金")
        if df is None or df.empty:
            return "北向资金：数据不可用"
        df["日期"] = pd.to_datetime(df["日期"])
        row = df[df["日期"] <= pd.to_datetime(date_str)].iloc[-1]
        # 列名因版本不同可能有差异，做宽泛匹配
        net_col = next((c for c in df.columns if "净买" in c or "净流" in c), None)
        if net_col is None:
            return f"北向资金：字段未找到（列：{list(df.columns)}）"
        net = pd.to_numeric(row[net_col], errors="coerce")
        net = float(net) if pd.notna(net) else 0.0
        direction = "净流入" if net > 0 else "净流出"
        return f"北向资金：当日净买额 {net/1e8:.2f} 亿元，{direction}"
    except Exception as e:
        logger.warning(f"北向资金获取失败: {e}")
        return "北向资金：数据获取失败"


def _breadth_simple() -> str:
    """全市场涨跌家数统计"""
    try:
        import akshare as ak
        spot = cached_ak_call("stock_zh_a_spot_em", ak.stock_zh_a_spot_em, expire=120)
        if spot is None or spot.empty:
            return "涨跌统计：数据不可用"
        # 东方财富实时快照中涨跌幅列
        pct_col = next((c for c in spot.columns if "涨跌幅" in c), None)
        if pct_col is None:
            return "涨跌统计：字段未找到"
        pct = pd.to_numeric(spot[pct_col], errors="coerce")
        up   = int((pct > 0).sum())
        down = int((pct < 0).sum())
        ratio = up / max(down, 1)
        return f"涨跌统计：上涨 {up} 只，下跌 {down} 只，涨跌比 {ratio:.2f}"
    except Exception as e:
        logger.warning(f"涨跌统计获取失败: {e}")
        return "涨跌统计：数据获取失败"
