"""截面选股的股票池。

按交易日给出当天可以进入截面漏斗的代码：
全市场、当日有行情的主板非 ST，或漏斗龙头候选。
行业 ETF 和低波策略不用这份名单。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set

from zstock.common.prefilters import PreFilters
from zstock.common.utils.common_utils import normalize_date
from zstock.data_management.query_service import COL_OHLCV, get_data_query_service

logger = logging.getLogger(__name__)

UNIVERSE_MODES = ("full", "prefilter", "funnel")


def normalize_universe_mode(mode: Optional[str], *, default: str = "full") -> str:
    m = str(mode or default).strip().lower()
    if m not in UNIVERSE_MODES:
        raise ValueError(f"不支持的 universe_mode: {mode!r}，可选 {UNIVERSE_MODES}")
    return m


async def list_trade_dates(start: str, end: str) -> list[str]:
    qs = get_data_query_service()
    s = normalize_date(start)
    e = normalize_date(end)
    dates = await qs.db[COL_OHLCV].distinct(
        "trade_date",
        {"period": "D", "trade_date": {"$gte": s, "$lte": e}},
    )
    return sorted(str(d) for d in dates if d)


async def build_universe_by_date(
    start: str,
    end: str,
    mode: str = "full",
    strategy_config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Set[str]]]:
    """
    构建按日宇宙。

    - full: 返回 None（测评侧按全市场）
    - prefilter: 当日有行情 ∩ PreFilters（主板/非ST/黑名单）
    - funnel: 每个交易日独立跑策略漏斗 M3（与实盘一致，不再前填）
    """
    mode = normalize_universe_mode(mode, default="full")
    if mode == "full":
        return None

    dates = await list_trade_dates(start, end)
    if not dates:
        logger.warning("宇宙构建: %s~%s 无交易日", start, end)
        return {}

    if mode == "prefilter":
        return await _build_prefilter_universe(dates)
    return await _build_funnel_universe(dates, strategy_config=strategy_config)


async def _build_prefilter_universe(dates: list[str]) -> Dict[str, Set[str]]:
    qs = get_data_query_service()
    docs, _ = await qs.get_all_stocks()
    stock_infos = {str(d["code"]): d for d in docs if d.get("code")}
    all_codes = list(stock_infos.keys())
    pf = PreFilters()
    try:
        sectors, _ = await qs.get_sector_list()
    except Exception:
        sectors = None

    out: Dict[str, Set[str]] = {}
    for td in dates:
        day_codes = await qs.db[COL_OHLCV].distinct(
            "code",
            {"period": "D", "trade_date": td},
        )
        codes = [str(c) for c in day_codes if c]
        if not codes:
            # 当日无行情：空宇宙（不再回落全市场，避免测评掺入停牌伪码）
            out[td] = set()
            logger.warning("prefilter 宇宙 %s: 当日无 OHLCV，置空", td)
            continue
        res = pf.filter_universe(
            codes,
            stock_infos=stock_infos,
            sectors=sectors,
            stock_data={},
            apply_main_board=True,
            apply_bollinger=False,
            as_of=td,
        )
        out[td] = set(str(c) for c in (res.get("stocks") or []))
        logger.info("prefilter 宇宙 %s: %d", td, len(out[td]))
    return out


async def _build_funnel_universe(
    dates: list[str],
    *,
    strategy_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Set[str]]:
    """每个交易日独立调用 m3_universe_codes（与实盘漏斗一致）。"""
    from zstock.strategy_management.pipeline import StrategyPipeline

    cfg = dict(strategy_config or {})
    cfg["universe_mode"] = "funnel"
    pipe = StrategyPipeline(config_override=cfg)
    out: Dict[str, Set[str]] = {}
    for td in dates:
        try:
            codes = await pipe.m3_universe_codes(td)
        except Exception as e:
            logger.warning("funnel 宇宙 %s 失败: %s", td, e)
            codes = set()
        out[td] = set(codes)
        logger.info("funnel 宇宙 %s: %d", td, len(out[td]))
    return out
