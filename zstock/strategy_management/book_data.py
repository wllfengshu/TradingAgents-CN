"""Book 三态账户：从 stockdb/Mongo 拉指数+ETF+债行情，口径与回测一致（不含当日 bar）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from zstock.common.utils.common_utils import normalize_code, normalize_date
from zstock.strategy_management.market_book import (
    BOOK_OHLCV_CODES,
    INDEX_CODE,
    MIN_HISTORY,
    grade_from_index_bars,
)

logger = logging.getLogger(__name__)

_LOOKBACK_CALENDAR_DAYS = max(MIN_HISTORY + 80, 150)


@dataclass(frozen=True)
class BookMarketInputs:
    """截至 trade_date 前一交易日的 Book 输入。"""

    trade_date: str
    index_bars: List[dict]
    index_closes: List[float]
    asset_closes: Dict[str, List[float]]
    book_grade: str


def _date_before(trade_date: str, days: int = 1) -> str:
    td = datetime.strptime(normalize_date(trade_date), "%Y-%m-%d").date()
    return (td - timedelta(days=days)).strftime("%Y-%m-%d")


def _filter_through_exclusive(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "trade_date" not in out.columns:
        return out
    out["trade_date"] = out["trade_date"].astype(str).map(normalize_date)
    end_exclusive = normalize_date(trade_date)
    return out[out["trade_date"] < end_exclusive].sort_values("trade_date")


def build_index_bars(index_df: pd.DataFrame) -> List[dict]:
    rows: List[dict] = []
    if index_df is None or index_df.empty:
        return rows
    for _, row in index_df.iterrows():
        try:
            close = float(row.get("close", float("nan")))
        except (TypeError, ValueError):
            continue
        if not close > 0:
            continue

        def _num(key: str, default: float) -> float:
            try:
                value = float(row.get(key, default))
            except (TypeError, ValueError):
                return default
            if value != value:
                return default
            return value

        rows.append(
            {
                "close": close,
                "high": _num("high", close),
                "low": _num("low", close),
                "volume": _num("volume", 0.0),
            }
        )
    return rows


def build_asset_closes(ohlcv_map: Dict[str, pd.DataFrame], trade_date: str) -> Dict[str, List[float]]:
    """各 BOOK 代码的收盘价序列（不含 trade_date 当日）。"""
    out: Dict[str, List[float]] = {}
    for code in BOOK_OHLCV_CODES:
        code_s = normalize_code(code)
        df = ohlcv_map.get(code_s)
        if df is None:
            df = ohlcv_map.get(code)
        if df is None or df.empty:
            continue
        hist = _filter_through_exclusive(df, trade_date)
        if hist.empty or "close" not in hist.columns:
            continue
        closes: List[float] = []
        for v in hist["close"].tolist():
            try:
                px = float(v)
            except (TypeError, ValueError):
                continue
            if px > 0:
                closes.append(px)
        if closes:
            out[code_s] = closes
    return out


def compose_book_inputs(
    trade_date: str,
    ohlcv_map: Dict[str, pd.DataFrame],
) -> BookMarketInputs:
    td = normalize_date(trade_date)
    index_key = normalize_code(INDEX_CODE)
    index_df = ohlcv_map.get(index_key)
    if index_df is None:
        index_df = ohlcv_map.get(INDEX_CODE)
    if index_df is None:
        index_df = pd.DataFrame()
    index_df = _filter_through_exclusive(index_df, td)
    index_bars = build_index_bars(index_df)
    index_closes = [float(r["close"]) for r in index_bars]
    asset_closes = build_asset_closes(ohlcv_map, td)
    book_grade = grade_from_index_bars(index_bars)
    return BookMarketInputs(
        trade_date=td,
        index_bars=index_bars,
        index_closes=index_closes,
        asset_closes=asset_closes,
        book_grade=book_grade,
    )


async def load_book_market_inputs(
    trade_date: str,
    *,
    lookback_calendar_days: int = _LOOKBACK_CALENDAR_DAYS,
) -> BookMarketInputs:
    """从 DataQueryService 加载 Book 所需 OHLCV。"""
    from zstock.data_management.query_service import get_data_query_service

    td = normalize_date(trade_date)
    start = _date_before(td, lookback_calendar_days)
    qs = get_data_query_service()
    batch = await qs.get_ohlcv_batch(
        [normalize_code(c) for c in BOOK_OHLCV_CODES],
        start,
        td,
        batch_size=20,
        query_concurrency=4,
    )
    normalized: Dict[str, pd.DataFrame] = {}
    for code, df in (batch or {}).items():
        normalized[normalize_code(code)] = df
    if not normalized:
        logger.warning("Book 行情为空: trade_date=%s", td)
    return compose_book_inputs(td, normalized)


def positions_to_weights_frame(
    positions: Optional[list],
    total_capital: float,
) -> pd.DataFrame:
    """QMT 持仓 → 策略层 current_positions（code, weight）。"""
    if not positions or total_capital <= 0:
        return pd.DataFrame(columns=["code", "weight", "score"])
    rows: List[dict] = []
    for p in positions:
        code = normalize_code(getattr(p, "code", p.get("code") if isinstance(p, dict) else ""))
        try:
            mv = float(getattr(p, "market_value", None))
        except (TypeError, ValueError):
            mv = None
        if mv is None and isinstance(p, dict):
            vol = int(p.get("volume", 0) or 0)
            px = float(p.get("current_price", 0) or 0)
            mv = vol * px
        if mv is None or mv <= 0:
            continue
        rows.append({"code": code, "weight": mv / float(total_capital), "score": 0.0})
    if not rows:
        return pd.DataFrame(columns=["code", "weight", "score"])
    return pd.DataFrame(rows)
