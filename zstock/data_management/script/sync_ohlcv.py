"""
手动数据同步脚本 — 一个 main 同步 1 张表

    zstock_ohlcv       — 日线 OHLCV

用法：
    python sync_ohlcv.py
    python sync_ohlcv.py --start 2025-01-01 --end 2026-08-02
    python sync_ohlcv.py --start 2000-01-01 --end 2022-12-31 --code-batch 80 --year-step 2
"""
import argparse
import asyncio
import gc
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# ─────────────────── 可调参数 ───────────────────
# 需同步的指数列表（code, name）。
# 注意：沪深300 用 399300（深交所版），不用 000300（后者库里 0 条且与个股 code 易冲突）。
# 000016（上证50）不在此列——to_xt_code 会把 000016 映射成 .SZ，与平安银行撞 code。
INDEX_CODES = [
    ('399300', '沪深300'),
    ('399001', '深证成指'),
    ('399006', '创业板指'),
    ('899050', '北证50'),
]
# 默认补齐缺口：2025Q1 空洞 + 续同步到最近交易日
DEFAULT_START = '2025-01-01'
# ────────────────────────────────────────────────

async def _persist_ohlcv(df: pd.DataFrame, period: str = 'D') -> None:
    if df is None or df.empty:
        return
    try:
        from pymongo import UpdateOne
        from app.core import database as db_module
        from zstock.data_management.query_service import COL_OHLCV

        db = db_module.db_manager.mongo_db
        if db is None:
            await db_module.db_manager.init_mongodb()
            db = db_module.db_manager.mongo_db

        ops = []
        for record in df.to_dict(orient='records'):
            code = record.get('code')
            td = record.get('trade_date')
            if not code or not td:
                continue
            ops.append(UpdateOne(
                {'code': code, 'trade_date': td, 'period': period},
                {'$set': {**record, 'period': period}},
                upsert=True,
            ))
        if ops:
            await db[COL_OHLCV].bulk_write(ops, ordered=False)
            logger.info(f"💾 落库 {COL_OHLCV}({period}): {len(ops)} 条")
    except Exception as e:
        logger.error(f"ohlcv 落库失败: {e}")


def _enrich_turnover_rate(df: pd.DataFrame, codes: List[str]) -> pd.DataFrame:
    """为 OHLCV DataFrame 补充 turnover_rate 列（换手率，百分数）。

    换手率 = 成交量(手) × 10000 / 流通股本(股)。xtquant 日线 volume 单位为"手"，
    FloatVolume 单位为"股"：×100 把手换算成股、再 ×100 化为百分数，合计 ×10000。

    流通股本分母优先级：
      1. 日线数据逐日 floatVolume 列（历史真实值，无前视偏差）——若 QMT 日线返回该字段
      2. get_instrument_detail 当前快照（近似值，含前视偏差，仅作降级兜底）

    结果为百分数（如 2.0 表示换手 2%），与全系统/同花顺口径一致；
    force_factors._score_turnover_quality 的阈值（3.0/5.0/20.0/30.0）即按此口径。
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    df['turnover_rate'] = 0.0

    # 优先：日线自带逐日流通股本（floatVolume，单位股）
    if 'floatVolume' in df.columns and 'volume' in df.columns:
        fv = pd.to_numeric(df['floatVolume'], errors='coerce')
        vol = pd.to_numeric(df['volume'], errors='coerce')
        valid = (fv > 0) & (vol.notna())
        if valid.any():
            df.loc[valid, 'turnover_rate'] = vol[valid] * 10000.0 / fv[valid]
            # 有 floatVolume 的股票不再走快照降级
            covered_codes = set(df.loc[valid, 'code'].astype(str))
            logger.info(f"  ✓ 换手率用逐日 floatVolume 计算: {len(covered_codes)} 只股票")
        else:
            covered_codes = set()
    else:
        covered_codes = set()

    # 降级：floatVolume 缺失的股票，用当前快照流通股本近似
    fallback_codes = [c for c in codes if c not in covered_codes]
    if fallback_codes:
        from zstock.common.utils import xtquant_data_utils as xtu
        float_map = xtu.fetch_float_shares_map(fallback_codes)
        if 'volume' in df.columns and float_map:
            for code, fv in float_map.items():
                mask = df['code'] == code
                if mask.any():
                    df.loc[mask, 'turnover_rate'] = df.loc[mask, 'volume'].astype(float) * 10000.0 / fv
        if float_map:
            logger.warning(
                f"  ⚠️ {len(fallback_codes) - len(float_map)} 只股票无 floatVolume 且无快照股本，"
                f"{len(float_map)} 只用当前快照近似（含前视偏差）"
            )
    return df


def _iter_year_windows(start: str, end: str, year_step: int) -> Iterator[Tuple[str, str]]:
    """按年切分日期区间；year_step<=0 时不切分。"""
    if year_step <= 0:
        yield start, end
        return
    start_y = int(start[:4])
    end_y = int(end[:4])
    y = start_y
    while y <= end_y:
        ye = min(y + year_step - 1, end_y)
        ws = start if y == start_y else f'{y:04d}-01-01'
        we = end if ye == end_y else f'{ye:04d}-12-31'
        yield ws, we
        y = ye + 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='同步 zstock_ohlcv 日线数据（xtquant → MongoDB）')
    parser.add_argument('--start', default=DEFAULT_START, help=f'起始日期 YYYY-MM-DD，默认 {DEFAULT_START}')
    parser.add_argument(
        '--end',
        default=datetime.now().strftime('%Y-%m-%d'),
        help='结束日期 YYYY-MM-DD，默认今天',
    )
    parser.add_argument('--code-batch', type=int, default=80, help='每批股票数，默认 80')
    parser.add_argument('--year-step', type=int, default=2, help='每个窗口覆盖年数，0=整段一次，默认 2')
    parser.add_argument('--sleep', type=float, default=0.4, help='批次间隔秒，默认 0.4')
    parser.add_argument(
        '--newest-first',
        action='store_true',
        default=True,
        help='年份窗口从新到旧（默认开启，中断时优先保住近年）',
    )
    parser.add_argument('--oldest-first', action='store_true', help='年份窗口从旧到新')
    args, _ = parser.parse_known_args()
    return args


async def _write_ohlcv_df(df: pd.DataFrame, codes: List[str], persist_batch: int) -> int:
    if df is None or df.empty:
        return 0
    df = _enrich_turnover_rate(df, codes)
    n = len(df)
    for batch_start in range(0, n, persist_batch):
        await _persist_ohlcv(df.iloc[batch_start:batch_start + persist_batch], 'D')
    return n


async def main():
    args = _parse_args()
    start = args.start
    end = args.end
    code_batch = max(1, int(args.code_batch))
    year_step = int(args.year_step)
    pause = max(0.0, float(args.sleep))
    newest_first = bool(args.newest_first) and not bool(args.oldest_first)

    # 分批写入，避免单次 bulk_write 过大
    BATCH_SIZE = 5000
    # 1. 连接 MongoDB
    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()
    logger.info("MongoDB 已连接")

    # 2. 初始化服务
    from zstock.data_management.query_service import DataQueryService
    qs = DataQueryService()
    await qs.ensure_indexes()
    logger.info("数据源")

    t0 = datetime.now()

    # 查到所有股票
    all_stocks, source = await qs.get_all_stocks()
    logger.info(f"  ✓ 全市场 {len(all_stocks)} 只 (来源: {source})")

    from zstock.common.utils import xtquant_data_utils as xtu

    all_codes = [s['code'] for s in all_stocks]
    windows = list(_iter_year_windows(start, end, year_step))
    if newest_first:
        windows = list(reversed(windows))
    n_code_batches = (len(all_codes) + code_batch - 1) // code_batch if all_codes else 0
    logger.info(
        f"▶ ohlcv — {len(all_codes)} 只股票  {start} ~ {end}  "
        f"窗口 {len(windows)} × 股票批 {n_code_batches}（每批 {code_batch}）"
    )

    total_ohlcv = 0
    failed_batches = 0
    for wi, (ws, we) in enumerate(windows, 1):
        logger.info(f"▶ 窗口 {wi}/{len(windows)}  {ws} ~ {we}")
        for bi, i in enumerate(range(0, len(all_codes), code_batch), 1):
            chunk = all_codes[i:i + code_batch]
            logger.info(f"  股票批 {bi}/{n_code_batches}  {chunk[0]}..{chunk[-1]} ({len(chunk)} 只)")
            try:
                df_all = xtu.fetch_ohlcv_batch(chunk, ws, we)
            except Exception as e:
                failed_batches += 1
                logger.error(f"  ✗ 拉取失败 {ws}~{we} 批 {bi}: {e}")
                continue
            n = await _write_ohlcv_df(df_all, chunk, BATCH_SIZE)
            total_ohlcv += n
            logger.info(f"  ✓ 本批 {n} 行  累计 {total_ohlcv}")
            del df_all
            gc.collect()
            if pause:
                time.sleep(pause)
        logger.info(f"  ✓ 窗口完成 {ws} ~ {we}  累计 {total_ohlcv} 行")

    # ── 表: 指数 ohlcv ──
    total_index = 0
    logger.info(f"▶ index ohlcv — {len(INDEX_CODES)} 个指数  {start} ~ {end}")
    for idx_code, idx_name in INDEX_CODES:
        try:
            df_idx = xtu.fetch_ohlcv(idx_code, start, end)
        except Exception as e:
            logger.warning(f"  ⚠️ 指数 {idx_name}({idx_code}) 拉取失败: {e}")
            continue
        if df_idx is None or df_idx.empty:
            logger.warning(f"  ⚠️ 指数 {idx_name}({idx_code}) 无数据")
            continue
        # 指数没有 FloatVolume，不补换手率（MarketFactors 只用 volume 不用 turnover_rate）
        n = len(df_idx)
        for batch_start in range(0, n, BATCH_SIZE):
            batch_df = df_idx.iloc[batch_start:batch_start + BATCH_SIZE]
            await _persist_ohlcv(batch_df, 'D')
        total_index += n
        logger.info(f"  ✓ {idx_name}({idx_code}): {n} 行")
    logger.info(f"  ✓ 指数 ohlcv 共写入 {total_index} 行")

    # ── 汇总 ──
    elapsed = (datetime.now() - t0).total_seconds()
    logger.info("=" * 50)
    logger.info(f"同步完成  耗时 {elapsed:.1f}s")
    logger.info(f"  ohlcv      : {total_ohlcv} 行")
    logger.info(f"  index ohlcv: {total_index} 行")
    logger.info(f"  失败批次   : {failed_batches}")
    logger.info("=" * 50)

    await db_module.db_manager.close_connections()


if __name__ == '__main__':
    from zstock.data_management.script.qmt_subprocess_bridge import patch_xtquant_data_utils
    patch_xtquant_data_utils()
    asyncio.run(main())
