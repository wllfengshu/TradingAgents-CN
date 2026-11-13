"""
同步资金流到 zstock_capital_flow（stockdb → MongoDB）

落库约定与因子查询对齐：
  - period = L2_daily（DataQueryService 默认读这个 period）
  - main_net / xl_net / ... 单位：万元
  - turnover 优先用同日 OHLCV.amount（元），供合力因子分母使用

用法：
    python -m zstock.data_management.script.sync_capital_flow
    python -m zstock.data_management.script.sync_capital_flow --start 2026-11-01 --end 2026-11-12
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.data_management.query_service import COL_CAPITAL_FLOW, COL_OHLCV, PERIOD_L2_DAILY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 15


async def _persist_capital_flow_bulk(df: pd.DataFrame) -> int:
    """批量 upsert 资金流。返回写入条数。"""
    if df is None or df.empty:
        return 0
    try:
        from pymongo import UpdateOne
        from zstock.common.utils import db_utils as db_module

        await db_module.db_manager.init_mongodb()
        db = db_module.db_manager.mongo_db

        if "code" in df.columns and not pd.api.types.is_numeric_dtype(df["code"]):
            df = df.copy()
            df["code"] = df["code"].astype(str).str.zfill(6)

        now = datetime.now(timezone.utc)
        ops = []
        for record in df.to_dict(orient="records"):
            code = record.get("code")
            td = record.get("trade_date")
            period = record.get("period") or PERIOD_L2_DAILY
            if not code or not td:
                continue
            payload = {**record, "period": period, "updated_at": now}
            ops.append(
                UpdateOne(
                    {"code": code, "trade_date": td, "period": period},
                    {"$set": payload},
                    upsert=True,
                )
            )
        if ops:
            await db[COL_CAPITAL_FLOW].bulk_write(ops, ordered=False)
        return len(ops)
    except Exception as e:
        logger.error(f"capital_flow 批量落库失败: {e}")
        return 0


async def _load_ohlcv_amount_map(
    codes: List[str],
    start: str,
    end: str,
) -> Dict[tuple, float]:
    """(code, trade_date) → amount（元）。"""
    from zstock.data_management.query_service import DataQueryService

    qs = DataQueryService()
    grouped = await qs.get_ohlcv_batch(codes, start, end, period="daily")
    out: Dict[tuple, float] = {}
    for code, frame in (grouped or {}).items():
        if frame is None or getattr(frame, "empty", True):
            continue
        for _, row in frame.iterrows():
            td = str(row.get("trade_date") or "")
            if not td:
                continue
            try:
                amt = float(row.get("amount") or 0.0)
            except (TypeError, ValueError):
                amt = 0.0
            if amt > 0:
                out[(str(code).zfill(6), td)] = amt
    return out


def _frames_to_df(
    frames: Dict[str, pd.DataFrame],
    amount_map: Optional[Dict[tuple, float]] = None,
) -> pd.DataFrame:
    parts: List[pd.DataFrame] = []
    amount_map = amount_map or {}
    for code, frame in frames.items():
        if frame is None or frame.empty:
            continue
        part = frame.copy()
        part["code"] = str(code).zfill(6)
        part["period"] = PERIOD_L2_DAILY
        if "turnover" not in part.columns:
            part["turnover"] = 0.0
        turnovers = []
        for _, row in part.iterrows():
            key = (str(row["code"]).zfill(6), str(row.get("trade_date") or ""))
            turnovers.append(float(amount_map.get(key, row.get("turnover") or 0.0) or 0.0))
        part["turnover"] = turnovers
        parts.append(part)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _load_money_flow(codes: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    from zstock.common.utils.stockdb_utils import StockDbClient

    client = StockDbClient()
    client.connect()
    return client.get_money_flow(codes, start, end)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    today = datetime.now().strftime("%Y-%m-%d")
    default_start = (datetime.now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(description="同步 zstock_capital_flow（stockdb → MongoDB）")
    parser.add_argument("--start", default=default_start, help=f"起始日期，默认 {default_start}")
    parser.add_argument("--end", default=today, help=f"结束日期，默认 {today}")
    parser.add_argument("--code-batch", type=int, default=80, help="每批股票数，默认 80")
    parser.add_argument("--sleep", type=float, default=0.2, help="批次间隔秒")
    args, _ = parser.parse_known_args(argv)
    return args


async def sync_capital_flow_range(
    start: str,
    end: str,
    *,
    code_batch: int = 80,
    pause: float = 0.2,
) -> int:
    """从 stockdb 增量同步资金流，返回写入条数。"""
    from zstock.common.utils import db_utils as db_module
    from zstock.data_management.query_service import DataQueryService

    await db_module.db_manager.init_mongodb()
    qs = DataQueryService()
    await qs.ensure_indexes()

    all_stocks, source = await qs.get_all_stocks()
    all_codes = [s["code"] for s in all_stocks if s.get("code")]
    logger.info(f"资金流同步 {len(all_codes)} 只 ({source})  {start} ~ {end}")

    total = 0
    failed = 0
    n_batches = (len(all_codes) + code_batch - 1) // code_batch if all_codes else 0
    for bi, i in enumerate(range(0, len(all_codes), code_batch), 1):
        chunk = all_codes[i : i + code_batch]
        logger.info(f"  批 {bi}/{n_batches}  {chunk[0]}..{chunk[-1]} ({len(chunk)})")
        try:
            frames = await asyncio.get_running_loop().run_in_executor(
                None, _load_money_flow, chunk, start, end
            )
            amount_map = await _load_ohlcv_amount_map(chunk, start, end)
            df = _frames_to_df(frames, amount_map)
            written = await _persist_capital_flow_bulk(df)
            total += written
            logger.info(f"  写入 {written}  累计 {total}")
        except Exception as e:
            failed += 1
            logger.error(f"  批 {bi} 失败: {e}")
        if pause:
            await asyncio.sleep(pause)

    logger.info(f"资金流同步完成: 写入 {total}, 失败批 {failed}")
    return total


async def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        await sync_capital_flow_range(
            args.start,
            args.end,
            code_batch=max(1, int(args.code_batch)),
            pause=max(0.0, float(args.sleep)),
        )
        return 0
    finally:
        try:
            from zstock.common.utils import db_utils as db_module

            await db_module.db_manager.close_connections()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
