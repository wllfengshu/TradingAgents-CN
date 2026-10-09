# -*- coding: utf-8 -*-
"""历史数据夜间同步 + 完整性检查（QMT 子进程，串行限流）。

只落库，不做因子预计算。默认补 2000-01-01 ~ 2022-12-31
（2023 年起日线已在库中）。已有足够条数的股票批次会跳过，失败批次会重试。

用法：
    python -m zstock.data_management.script.run_hist_sync_qmt
    python -m zstock.data_management.script.run_hist_sync_qmt --check-only
    python -m zstock.data_management.script.run_hist_sync_qmt --fill-holes
    python -m zstock.data_management.script.run_hist_sync_qmt --start 2000-01-01 --end 2022-12-31
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).resolve().parent / 'output'
CHECKPOINT_PATH = OUT_DIR / 'hist_sync_checkpoint.json'
REPORT_PATH = OUT_DIR / 'hist_sync_report.txt'

PROBE_CODES = ['000001', '600000', '000002']
PERSIST_BATCH = 5000
# 一年窗口内已有这么多交易日则视为该票已齐，跳过
MIN_DAYS_PER_YEAR = 80

# 完整性检查里发现的日历空洞（指数有行情、部分个股没有）
DEFAULT_HOLE_WINDOWS = [
    ('2005-09-28', '2005-10-20'),
    ('2007-06-01', '2007-06-19'),
    ('2010-06-30', '2010-09-01'),
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='QMT 历史同步 + 完整性检查')
    p.add_argument('--start', default='2000-01-01')
    p.add_argument('--end', default='2022-12-31')
    p.add_argument('--code-batch', type=int, default=40)
    p.add_argument('--year-step', type=int, default=1)
    p.add_argument('--sleep', type=float, default=0.4)
    p.add_argument('--retries', type=int, default=3)
    p.add_argument('--skip-ohlcv', action='store_true')
    p.add_argument('--skip-fundamental', action='store_true')
    p.add_argument('--check-only', action='store_true', help='只跑完整性检查，不同步')
    p.add_argument(
        '--fill-holes',
        action='store_true',
        help='只补指数有行情但个股缺失的日历空洞（默认三处：2005/2007/2010）',
    )
    return p.parse_args()


def _python_processes() -> List[Tuple[int, str]]:
    cmd = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        raw = subprocess.check_output(
            ['powershell', '-NoProfile', '-Command', cmd],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []
    raw = (raw or '').strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    out: List[Tuple[int, str]] = []
    for item in data or []:
        try:
            out.append((int(item.get('ProcessId')), str(item.get('CommandLine') or '')))
        except (TypeError, ValueError):
            continue
    return out


def _other_sync_pids() -> List[int]:
    me = os.getpid()
    markers = (
        'script.sync_ohlcv',
        'script.sync_fundamental',
        'sync_ohlcv.py',
        'sync_fundamental.py',
    )
    pids = []
    for pid, cmdline in _python_processes():
        if pid == me:
            continue
        if any(m in cmdline for m in markers):
            pids.append(pid)
    return pids


def _wait_for_other_sync() -> None:
    while True:
        pids = _other_sync_pids()
        if not pids:
            return
        logger.info('发现正在运行的同步进程 %s，等待结束后再补洞并检查完整性...', pids)
        time.sleep(30)


def _load_checkpoint() -> dict:
    if not CHECKPOINT_PATH.is_file():
        return {'failed': []}
    try:
        return json.loads(CHECKPOINT_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {'failed': []}


def _save_checkpoint(data: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


async def _codes_needing_fetch(col, codes: List[str], start: str, end: str, min_days: int) -> List[str]:
    if not codes:
        return []
    rows = await col.aggregate(
        [
            {
                '$match': {
                    'code': {'$in': codes},
                    'period': 'D',
                    'trade_date': {'$gte': start, '$lte': end},
                }
            },
            {'$group': {'_id': '$code', 'n': {'$sum': 1}}},
        ]
    ).to_list(None)
    have = {d['_id']: int(d['n']) for d in rows}
    return [c for c in codes if have.get(c, 0) < min_days]


def _fetch_with_retry(xtu, codes: List[str], start: str, end: str, retries: int, pause: float):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            df = xtu.fetch_ohlcv_batch(codes, start, end)
            return df, None
        except Exception as e:
            last_err = e
            logger.warning(f'    第{attempt}/{retries}次失败: {e}')
            time.sleep(max(pause, 2.0) * attempt)
    return None, last_err


async def _sync_ohlcv(args: argparse.Namespace) -> List[dict]:
    from app.core import database as db_module
    from zstock.common.utils import xtquant_data_utils as xtu
    from zstock.data_management.query_service import COL_OHLCV, DataQueryService
    from zstock.data_management.script.sync_ohlcv import (
        INDEX_CODES,
        _iter_year_windows,
        _persist_ohlcv,
        _write_ohlcv_df,
    )

    await db_module.db_manager.init_mongodb()
    col = db_module.db_manager.mongo_db[COL_OHLCV]
    qs = DataQueryService()
    await qs.ensure_indexes()
    stocks, source = await qs.get_all_stocks()
    all_codes = [s['code'] for s in stocks if s.get('code')]
    logger.info(f'全市场 {len(all_codes)} 只 (来源: {source})')

    code_batch = max(1, int(args.code_batch))
    year_step = max(1, int(args.year_step))
    min_days = MIN_DAYS_PER_YEAR * year_step
    pause = max(0.0, float(args.sleep))
    retries = max(1, int(args.retries))

    windows = list(reversed(list(_iter_year_windows(args.start, args.end, year_step))))
    n_batches = (len(all_codes) + code_batch - 1) // code_batch
    logger.info(
        f'▶ OHLCV {args.start}~{args.end}  窗口{len(windows)} × 批{n_batches}（每批{code_batch}）'
    )

    ckpt = _load_checkpoint()
    failed: List[dict] = list(ckpt.get('failed') or [])
    total_written = 0

    async def _run_one(codes: List[str], ws: str, we: str, label: str) -> int:
        need = await _codes_needing_fetch(col, codes, ws, we, min_days)
        if not need:
            logger.info(f'  {label} 已齐，跳过 ({len(codes)} 只)')
            return 0
        logger.info(f'  {label} 待补 {len(need)}/{len(codes)} 只')
        df, err = _fetch_with_retry(xtu, need, ws, we, retries, pause)
        if err is not None:
            failed.append({'start': ws, 'end': we, 'codes': need, 'error': str(err)[:300]})
            _save_checkpoint({'failed': failed, 'updated_at': datetime.now().isoformat(timespec='seconds')})
            logger.error(f'  ✗ {label} 仍失败，记入待补')
            return 0
        n = await _write_ohlcv_df(df, need, PERSIST_BATCH)
        del df
        gc.collect()
        if pause:
            time.sleep(pause)
        return n

    for wi, (ws, we) in enumerate(windows, 1):
        logger.info(f'▶ 窗口 {wi}/{len(windows)}  {ws} ~ {we}')
        for bi, i in enumerate(range(0, len(all_codes), code_batch), 1):
            chunk = all_codes[i:i + code_batch]
            n = await _run_one(chunk, ws, we, f'批 {bi}/{n_batches} {chunk[0]}..{chunk[-1]}')
            total_written += n
            if n:
                logger.info(f'    写入 {n}  累计 {total_written}')
        logger.info(f'  ✓ 窗口结束 {ws}~{we}  累计写入 {total_written}')

    # 窗口全部走完后再打一遍失败队列
    if failed:
        leftover = failed
        failed = []
        logger.info(f'▶ 重试失败批次 {len(leftover)} 个')
        for item in leftover:
            n = await _run_one(item['codes'], item['start'], item['end'],
                               f"补 {item['start']}~{item['end']} {len(item['codes'])}只")
            total_written += n

    logger.info(f'▶ 指数 OHLCV  {args.start} ~ {args.end}')
    for idx_code, idx_name in INDEX_CODES:
        try:
            df_idx = xtu.fetch_ohlcv(idx_code, args.start, args.end)
        except Exception as e:
            logger.warning(f'  ⚠️ {idx_name}({idx_code}) {e}')
            continue
        if df_idx is None or df_idx.empty:
            logger.warning(f'  ⚠️ {idx_name}({idx_code}) 无数据')
            continue
        n = len(df_idx)
        for j in range(0, n, PERSIST_BATCH):
            await _persist_ohlcv(df_idx.iloc[j:j + PERSIST_BATCH], 'D')
        logger.info(f'  ✓ {idx_name}({idx_code}): {n} 行')

    _save_checkpoint({'failed': failed, 'updated_at': datetime.now().isoformat(timespec='seconds')})
    logger.info(f'OHLCV 本轮写入 {total_written} 行，残留失败批 {len(failed)}')
    return failed


def _shift_date(date_str: str, days: int) -> str:
    return (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=days)).strftime('%Y-%m-%d')


async def _fill_calendar_holes(args: argparse.Namespace) -> None:
    """对指数有K线、个股前后都在交易但窗口内条数过少的股票，强制回拉。"""
    from app.core import database as db_module
    from zstock.common.utils import xtquant_data_utils as xtu
    from zstock.data_management.query_service import COL_OHLCV, DataQueryService
    from zstock.data_management.script.sync_ohlcv import _write_ohlcv_df

    await db_module.db_manager.init_mongodb()
    col = db_module.db_manager.mongo_db[COL_OHLCV]
    qs = DataQueryService()
    await qs.ensure_indexes()

    windows = list(DEFAULT_HOLE_WINDOWS)
    if args.start != '2000-01-01' or args.end != '2022-12-31':
        windows = [(args.start, args.end)]

    code_batch = max(1, int(args.code_batch))
    pause = max(0.0, float(args.sleep))
    retries = max(1, int(args.retries))
    total_written = 0

    for ws, we in windows:
        cal = await col.distinct(
            'trade_date',
            {'code': '399001', 'period': 'D', 'trade_date': {'$gte': ws, '$lte': we}},
        )
        cal_n = len(cal)
        if cal_n < 3:
            logger.warning('窗口 %s~%s 指数日历过短 (%s)，跳过', ws, we, cal_n)
            continue
        need_n = max(3, cal_n // 2)
        before_codes = set(await col.distinct('code', {
            'period': 'D',
            'trade_date': {'$gte': _shift_date(ws, -40), '$lt': ws},
        }))
        after_codes = set(await col.distinct('code', {
            'period': 'D',
            'trade_date': {'$gt': we, '$lte': _shift_date(we, 40)},
        }))
        listed = sorted(before_codes & after_codes)
        counts = await col.aggregate(
            [
                {'$match': {
                    'period': 'D',
                    'code': {'$in': listed},
                    'trade_date': {'$gte': ws, '$lte': we},
                }},
                {'$group': {'_id': '$code', 'n': {'$sum': 1}}},
            ],
            allowDiskUse=True,
        ).to_list(None)
        have = {d['_id']: int(d['n']) for d in counts}
        missing = [c for c in listed if have.get(c, 0) < need_n]
        logger.info(
            '▶ 补洞 %s~%s  指数%d日  前后在市%d只  窗口内<%d日 %d只',
            ws, we, cal_n, len(listed), need_n, len(missing),
        )
        if not missing:
            continue
        n_batches = (len(missing) + code_batch - 1) // code_batch
        halt = 0
        filled_codes = 0
        for bi, i in enumerate(range(0, len(missing), code_batch), 1):
            chunk = missing[i:i + code_batch]
            logger.info('  批 %s/%s  %s..%s (%s)', bi, n_batches, chunk[0], chunk[-1], len(chunk))
            df, err = _fetch_with_retry(xtu, chunk, ws, we, retries, pause)
            if err is not None:
                logger.error('  ✗ 补洞失败: %s', err)
                continue
            got = set()
            if df is not None and not df.empty:
                got = set(df['code'].astype(str).unique())
                n = await _write_ohlcv_df(df, chunk, PERSIST_BATCH)
                total_written += n
                filled_codes += len(got)
                logger.info('    写入 %s 行 / %s 只', n, len(got))
            halt += sum(1 for c in chunk if c not in got)
            del df
            gc.collect()
            if pause:
                time.sleep(pause)
        logger.info('  ✓ %s~%s 补到 %s 只，QMT无K线(停牌) %s 只', ws, we, filled_codes, halt)

    logger.info('补洞合计写入 %s 行', total_written)


async def _sync_fundamental(args: argparse.Namespace) -> None:
    logger.info('▶ fundamental %s ~ %s', args.start, args.end)
    sys.argv = [
        'sync_fundamental.py',
        '--start', args.start,
        '--end', args.end,
        '--code-batch', str(max(40, int(args.code_batch))),
        '--sleep', str(args.sleep),
    ]
    from zstock.data_management.script import sync_fundamental
    await sync_fundamental.main()


async def check_integrity(start: str, end: str) -> str:
    from app.core import database as db_module
    from zstock.data_management.query_service import COL_OHLCV
    from zstock.data_management.script.sync_fundamental import (
        COL_FUNDAMENTAL_HOLDER,
        COL_FUNDAMENTAL_PERSHARE,
    )
    from zstock.data_management.script.sync_ohlcv import INDEX_CODES

    await db_module.db_manager.init_mongodb()
    db = db_module.db_manager.mongo_db
    col = db[COL_OHLCV]

    y0, y1 = int(start[:4]), int(end[:4])
    lines: List[str] = []
    lines.append('=' * 70)
    lines.append(f'完整性报告  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'检查区间    {start} ~ {end}')
    lines.append('=' * 70)

    yearly = await col.aggregate(
        [
            {'$match': {'period': 'D'}},
            {'$group': {'_id': {'$substr': ['$trade_date', 0, 4]}, 'n': {'$sum': 1}}},
            {'$sort': {'_id': 1}},
        ]
    ).to_list(50)
    ymap: Dict[str, int] = {str(d['_id']): int(d['n']) for d in yearly}
    dr = await col.aggregate(
        [
            {'$match': {'period': 'D'}},
            {'$group': {'_id': None, 'min': {'$min': '$trade_date'}, 'max': {'$max': '$trade_date'}}},
        ]
    ).to_list(1)
    n_codes = len(await col.distinct('code', {'period': 'D'}))
    lines.append(f"OHLCV 全表: {sum(ymap.values())} 行  codes={n_codes}  "
                 f"range={dr[0]['min'] if dr else '-'} ~ {dr[0]['max'] if dr else '-'}")
    lines.append('')
    lines.append(f"{'年':<6} {'条数':>10} {'000001天数':>10}  状态")
    gaps: List[str] = []
    for y in range(y0, y1 + 1):
        ys = str(y)
        n = ymap.get(ys, 0)
        probe = await col.count_documents(
            {'code': '000001', 'period': 'D', 'trade_date': {'$gte': f'{y}-01-01', '$lte': f'{y}-12-31'}}
        )
        status = 'OK'
        if n == 0:
            status = '空'
            gaps.append(ys)
        elif probe < 100:
            status = '缺（000001<100日）'
            gaps.append(ys)
        elif n < 200_000:
            status = '偏少'
            gaps.append(ys)
        lines.append(f'{ys:<6} {n:>10} {probe:>10}  {status}')

    lines.append('')
    lines.append('指数:')
    for code, name in INDEX_CODES:
        cnt = await col.count_documents({'code': code, 'period': 'D'})
        if cnt == 0:
            lines.append(f'  {code} {name}: 无数据')
            continue
        idr = await col.aggregate(
            [
                {'$match': {'code': code, 'period': 'D'}},
                {'$group': {'_id': None, 'min': {'$min': '$trade_date'}, 'max': {'$max': '$trade_date'}}},
            ]
        ).to_list(1)
        lines.append(f"  {code} {name}: {cnt}  [{idr[0]['min']} ~ {idr[0]['max']}]")

    lines.append('')
    lines.append('个股探针:')
    for code in PROBE_CODES:
        cnt = await col.count_documents({'code': code, 'period': 'D'})
        if not cnt:
            lines.append(f'  {code}: 无数据')
            continue
        idr = await col.aggregate(
            [
                {'$match': {'code': code, 'period': 'D'}},
                {'$group': {'_id': None, 'min': {'$min': '$trade_date'}, 'max': {'$max': '$trade_date'}}},
            ]
        ).to_list(1)
        lines.append(f"  {code}: {cnt}  [{idr[0]['min']} ~ {idr[0]['max']}]")

    lines.append('')
    for cname, dfield in (
        (COL_FUNDAMENTAL_PERSHARE, 'ann_date'),
        (COL_FUNDAMENTAL_HOLDER, 'ann_date'),
    ):
        n = await db[cname].estimated_document_count()
        if n == 0:
            lines.append(f'{cname}: 空')
            continue
        idr = await db[cname].aggregate(
            [{'$group': {'_id': None, 'min': {'$min': '$' + dfield}, 'max': {'$max': '$' + dfield}}}]
        ).to_list(1)
        lines.append(f"{cname}: {n}  {dfield} [{idr[0]['min']} ~ {idr[0]['max']}]")

    ckpt = _load_checkpoint()
    n_fail = len(ckpt.get('failed') or [])
    lines.append('')
    lines.append(f'残留失败批次: {n_fail}')
    if gaps:
        lines.append(f'需关注年份: {", ".join(gaps)}')
    else:
        lines.append('2000-2022 各年 000001 均有 >=100 个交易日，且年度条数未明显空洞。')
    lines.append('=' * 70)
    text = '\n'.join(lines)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(text, encoding='utf-8')
    logger.info('完整性报告已写 %s', REPORT_PATH)
    print(text)
    return text


async def _run(args: argparse.Namespace) -> None:
    from zstock.data_management.script.qmt_subprocess_bridge import patch_xtquant_data_utils

    if args.check_only:
        await check_integrity(args.start, args.end)
        return

    patch_xtquant_data_utils()
    _wait_for_other_sync()
    t0 = datetime.now()
    if args.fill_holes:
        await _fill_calendar_holes(args)
        elapsed = (datetime.now() - t0).total_seconds()
        logger.info('补洞完成  耗时 %.1fs', elapsed)
        return

    if not args.skip_ohlcv:
        await _sync_ohlcv(args)
    if not args.skip_fundamental:
        await _sync_fundamental(args)

    await check_integrity(args.start, args.end)
    elapsed = (datetime.now() - t0).total_seconds()
    logger.info('全部完成  耗时 %.1fs  报告 %s', elapsed, REPORT_PATH)


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == '__main__':
    main()
