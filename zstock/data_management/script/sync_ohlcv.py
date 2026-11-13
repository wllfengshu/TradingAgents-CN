"""
同步 OHLCV：stockdb → MongoDB（zstock_ohlcv）

支持周期：
  1d  日线   → period=D
  5m  5分钟  → period=5m
  1m  1分钟  → period=1m

唯一键：(code, trade_date, period)
  - 日线 trade_date = YYYY-MM-DD
  - 分钟 trade_date = YYYY-MM-DD HH:MM:SS

用法：
    python -m zstock.data_management.script.sync_ohlcv
    python -m zstock.data_management.script.sync_ohlcv --freq 1d
    python -m zstock.data_management.script.sync_ohlcv --freq 5m --start 2026-09-01 --end 2026-09-16
    python -m zstock.data_management.script.sync_ohlcv --freq 1d,5m,1m --start 2026-09-10 --end 2026-09-16
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 需同步的指数（仅日线默认同步；分钟线可选 --with-index）
INDEX_CODES = [
    ("399300", "沪深300"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("899050", "北证50"),
]
DEFAULT_START = "2025-01-01"

# OHLCV 落库批量大小（每次 bulk_write 的最大文档数）
PERSIST_BATCH_SIZE = 5000

# freq → stockdb frequency / Mongo period / 默认批与窗口
FREQ_SPEC: Dict[str, Dict[str, object]] = {
    "1d": {
        "stockdb": "1d",
        "mongo_period": "D",
        "code_batch": 80,
        "window": "year",
        "year_step": 2,
        "day_step": 0,
        "enrich_turnover": True,
        "sync_index_default": True,
    },
    "5m": {
        "stockdb": "5m",
        "mongo_period": "5m",
        "code_batch": 40,
        "window": "day",
        "year_step": 0,
        "day_step": 5,
        "enrich_turnover": False,
        "sync_index_default": False,
    },
    "1m": {
        "stockdb": "1m",
        "mongo_period": "1m",
        "code_batch": 20,
        "window": "day",
        "year_step": 0,
        "day_step": 2,
        "enrich_turnover": False,
        "sync_index_default": False,
    },
}


def _parse_freqs(raw: str) -> List[str]:
    parts = [p.strip().lower() for p in (raw or "1d").replace(";", ",").split(",") if p.strip()]
    out: List[str] = []
    for p in parts:
        alias = {"d": "1d", "day": "1d", "daily": "1d", "5min": "5m", "1min": "1m"}.get(p, p)
        if alias not in FREQ_SPEC:
            raise SystemExit(f"不支持的 --freq: {p}（可选: 1d,5m,1m）")
        if alias not in out:
            out.append(alias)
    return out or ["1d"]


async def _persist_ohlcv(df: pd.DataFrame, period: str) -> None:
    if df is None or df.empty:
        return
    try:
        from pymongo import UpdateOne
        from zstock.common.utils import db_utils as db_module
        from zstock.data_management.query_service import COL_OHLCV

        db = db_module.db_manager.mongo_db
        if db is None:
            await db_module.db_manager.init_mongodb()
            db = db_module.db_manager.mongo_db

        ops = []
        for record in df.to_dict(orient="records"):
            code = record.get("code")
            td = record.get("trade_date")
            if not code or not td:
                continue
            # numpy 类型转原生，便于 Mongo 序列化
            clean = {}
            for k, v in record.items():
                if hasattr(v, "item"):
                    try:
                        v = v.item()
                    except Exception:
                        pass
                clean[k] = v
            clean["period"] = period
            ops.append(
                UpdateOne(
                    {"code": code, "trade_date": td, "period": period},
                    {"$set": clean},
                    upsert=True,
                )
            )
        if ops:
            await db[COL_OHLCV].bulk_write(ops, ordered=False)
            logger.info("落库 %s(%s): %s 条", COL_OHLCV, period, len(ops))
    except Exception as e:
        logger.error("ohlcv 落库失败: %s", e)


def _enrich_turnover_rate(df: pd.DataFrame, codes: List[str]) -> pd.DataFrame:
    """日线补充 turnover_rate（百分数）。分钟线不做。"""
    if df is None or df.empty:
        return df
    df = df.copy()
    df["turnover_rate"] = 0.0

    if "floatVolume" in df.columns and "volume" in df.columns:
        fv = pd.to_numeric(df["floatVolume"], errors="coerce")
        vol = pd.to_numeric(df["volume"], errors="coerce")
        valid = (fv > 0) & (vol.notna())
        if valid.any():
            df.loc[valid, "turnover_rate"] = vol[valid] * 10000.0 / fv[valid]
            covered_codes = set(df.loc[valid, "code"].astype(str))
            logger.info("  ✓ 换手率用逐日 floatVolume 计算: %s 只股票", len(covered_codes))
        else:
            covered_codes = set()
    else:
        covered_codes = set()

    missing = [c for c in codes if c not in covered_codes]
    if missing:
        logger.info("  无逐日 floatVolume，换手率保持 0: %s 只", len(missing))
    return df


def _iter_year_windows(start: str, end: str, year_step: int) -> Iterator[Tuple[str, str]]:
    if year_step <= 0:
        yield start, end
        return
    start_y = int(start[:4])
    end_y = int(end[:4])
    y = start_y
    while y <= end_y:
        ye = min(y + year_step - 1, end_y)
        ws = start if y == start_y else f"{y:04d}-01-01"
        we = end if ye == end_y else f"{ye:04d}-12-31"
        yield ws, we
        y = ye + 1


def _iter_day_windows(start: str, end: str, day_step: int) -> Iterator[Tuple[str, str]]:
    """按自然日切窗口；day_step<=0 表示整段一次。"""
    if day_step <= 0:
        yield start, end
        return
    cur = datetime.strptime(start[:10], "%Y-%m-%d")
    last = datetime.strptime(end[:10], "%Y-%m-%d")
    step = max(1, int(day_step))
    while cur <= last:
        we = min(cur + timedelta(days=step - 1), last)
        yield cur.strftime("%Y-%m-%d"), we.strftime("%Y-%m-%d")
        cur = we + timedelta(days=1)


def _iter_windows(start: str, end: str, spec: Dict[str, object]) -> List[Tuple[str, str]]:
    if spec.get("window") == "day":
        return list(_iter_day_windows(start, end, int(spec.get("day_step") or 0)))
    return list(_iter_year_windows(start, end, int(spec.get("year_step") or 0)))


def _load_bars(
    codes: Sequence[str],
    start: str,
    end: str,
    *,
    frequency: str = "1d",
) -> pd.DataFrame:
    """从 stockdb 拉 K 线，拼成带 code 列的表。"""
    from zstock.common.utils.stockdb_utils import StockDbClient

    symbols = [codes] if isinstance(codes, str) else list(codes)
    client = StockDbClient()
    client.connect()
    frames = client.get_bars(symbols, start, end, frequency=frequency)
    parts = []
    for code, frame in frames.items():
        if frame is None or frame.empty:
            continue
        part = frame.copy()
        part["code"] = code
        parts.append(part)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="同步 zstock_ohlcv（stockdb → MongoDB），支持 1d / 5m / 1m"
    )
    parser.add_argument(
        "--freq",
        default="1d",
        help="周期：1d / 5m / 1m，逗号分隔可一次多个，默认 1d",
    )
    parser.add_argument("--start", default=DEFAULT_START, help=f"起始日期，默认 {DEFAULT_START}")
    parser.add_argument(
        "--end",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="结束日期，默认今天",
    )
    parser.add_argument("--code-batch", type=int, default=None, help="每批股票数（默认随 freq）")
    parser.add_argument("--year-step", type=int, default=None, help="日线年份窗口（覆盖默认）")
    parser.add_argument("--day-step", type=int, default=None, help="分钟线日窗口长度（覆盖默认）")
    parser.add_argument("--sleep", type=float, default=0.4, help="批次间隔秒，默认 0.4")
    parser.add_argument(
        "--newest-first",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="窗口从新到旧（默认开启，--no-newest-first 关闭）",
    )
    parser.add_argument(
        "--with-index",
        action="store_true",
        help="同步指数（日线默认开；分钟线需显式加此开关）",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="不同步指数",
    )
    args, _ = parser.parse_known_args(argv)
    return args


async def _write_ohlcv_df(
    df: pd.DataFrame,
    codes: List[str],
    persist_batch: int,
    *,
    mongo_period: str,
    enrich_turnover: bool,
) -> int:
    if df is None or df.empty:
        return 0
    if enrich_turnover:
        df = _enrich_turnover_rate(df, codes)
    n = len(df)
    for batch_start in range(0, n, persist_batch):
        await _persist_ohlcv(df.iloc[batch_start : batch_start + persist_batch], mongo_period)
    return n


async def _sync_one_freq(
    freq: str,
    *,
    start: str,
    end: str,
    all_codes: List[str],
    code_batch: int,
    windows: List[Tuple[str, str]],
    pause: float,
    sync_index: bool,
) -> Tuple[int, int, int]:
    spec = FREQ_SPEC[freq]
    stockdb_freq = str(spec["stockdb"])
    mongo_period = str(spec["mongo_period"])
    enrich = bool(spec["enrich_turnover"])

    n_code_batches = (len(all_codes) + code_batch - 1) // code_batch if all_codes else 0
    logger.info(
        "ohlcv[%s->%s] -- %s 只  %s~%s  窗口 %s x 批 %s（每批 %s）",
        stockdb_freq,
        mongo_period,
        len(all_codes),
        start,
        end,
        len(windows),
        n_code_batches,
        code_batch,
    )

    total_ohlcv = 0
    failed_batches = 0
    for wi, (ws, we) in enumerate(windows, 1):
        logger.info("[%s] 窗口 %s/%s  %s ~ %s", freq, wi, len(windows), ws, we)
        for bi, i in enumerate(range(0, len(all_codes), code_batch), 1):
            chunk = all_codes[i : i + code_batch]
            logger.info(
                "  股票批 %s/%s  %s..%s (%s 只)",
                bi,
                n_code_batches,
                chunk[0],
                chunk[-1],
                len(chunk),
            )
            try:
                df_all = _load_bars(chunk, ws, we, frequency=stockdb_freq)
            except Exception as e:
                failed_batches += 1
                logger.error("  拉取失败 %s~%s 批 %s: %s", ws, we, bi, e)
                continue
            n = await _write_ohlcv_df(
                df_all,
                chunk,
                PERSIST_BATCH_SIZE,
                mongo_period=mongo_period,
                enrich_turnover=enrich,
            )
            total_ohlcv += n
            logger.info("  ✓ 本批 %s 行  累计 %s", n, total_ohlcv)
            del df_all
            gc.collect()
            if pause:
                await asyncio.sleep(pause)
        logger.info("  ✓ 窗口完成 %s ~ %s  累计 %s 行", ws, we, total_ohlcv)

    total_index = 0
    if sync_index:
        logger.info("[%s] index ohlcv -- %s 个指数  %s ~ %s", freq, len(INDEX_CODES), start, end)
        for idx_code, idx_name in INDEX_CODES:
            try:
                df_idx = _load_bars([idx_code], start, end, frequency=stockdb_freq)
            except Exception as e:
                logger.warning("  指数 %s(%s) 拉取失败: %s", idx_name, idx_code, e)
                continue
            if df_idx is None or df_idx.empty:
                logger.warning("  指数 %s(%s) 无数据", idx_name, idx_code)
                continue
            n = len(df_idx)
            for batch_start in range(0, n, PERSIST_BATCH_SIZE):
                batch_df = df_idx.iloc[batch_start : batch_start + PERSIST_BATCH_SIZE]
                await _persist_ohlcv(batch_df, mongo_period)
            total_index += n
            logger.info("  ✓ %s(%s): %s 行", idx_name, idx_code, n)
        logger.info("  ✓ 指数 ohlcv 共写入 %s 行", total_index)

    return total_ohlcv, total_index, failed_batches


async def main(argv: List[str] | None = None):
    args = _parse_args(argv)
    freqs = _parse_freqs(args.freq)
    start = args.start
    end = args.end
    pause = max(0.0, float(args.sleep))
    newest_first = bool(args.newest_first)

    from zstock.common.utils import db_utils as db_module

    await db_module.db_manager.init_mongodb()
    logger.info("MongoDB 已连接")

    from zstock.data_management.query_service import DataQueryService

    qs = DataQueryService()
    await qs.ensure_indexes()

    t0 = datetime.now()
    all_stocks, source = await qs.get_all_stocks()
    logger.info("  ✓ 全市场 %s 只 (来源: %s)", len(all_stocks), source)
    all_codes = [s["code"] for s in all_stocks]

    grand_ohlcv = 0
    grand_index = 0
    grand_fail = 0

    for freq in freqs:
        spec = dict(FREQ_SPEC[freq])
        if args.code_batch is not None:
            spec["code_batch"] = max(1, int(args.code_batch))
        if args.year_step is not None:
            spec["year_step"] = int(args.year_step)
            if int(args.year_step) > 0:
                spec["window"] = "year"
        if args.day_step is not None:
            spec["day_step"] = int(args.day_step)
            spec["window"] = "day"

        windows = _iter_windows(start, end, spec)
        if newest_first:
            windows = list(reversed(windows))

        if args.no_index:
            sync_index = False
        elif args.with_index:
            sync_index = True
        else:
            sync_index = bool(spec["sync_index_default"])

        o, i, f = await _sync_one_freq(
            freq,
            start=start,
            end=end,
            all_codes=all_codes,
            code_batch=max(1, int(spec["code_batch"])),
            windows=windows,
            pause=pause,
            sync_index=sync_index,
        )
        grand_ohlcv += o
        grand_index += i
        grand_fail += f

    elapsed = (datetime.now() - t0).total_seconds()
    logger.info("=" * 50)
    logger.info("同步完成  freq=%s  耗时 %.1fs", ",".join(freqs), elapsed)
    logger.info("  ohlcv      : %s 行", grand_ohlcv)
    logger.info("  index ohlcv: %s 行", grand_index)
    logger.info("  失败批次   : %s", grand_fail)
    logger.info("=" * 50)

    await db_module.db_manager.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
