"""
补充脚本 — 为已落库的 OHLCV 文档回填 turnover_rate（换手率）字段

    换手率 = 成交量(手) × 10000 / 流通股本(股)，结果为百分数（2.0 = 2%）
    流通股本取 xtquant get_instrument_detail 的 FloatVolume 当前快照，历史区间为近似值。
    与 sync_ohlcv.py 的 _enrich_turnover_rate 口径完全一致。

适用场景：sync_ohlcv.py 升级（新增 turnover_rate 列）前已落库的历史 OHLCV 文档
        缺失该字段，用本脚本一次性补齐，避免 force_factors._score_turnover_quality
        因 turnover_rate=0 误判为冷盘。

用法：
    python supply_ohlcv_turnover_rate.py                # 回填最近 30 天缺失的 turnover_rate
    python supply_ohlcv_turnover_rate.py --days 60      # 回填最近 60 天缺失的
    python supply_ohlcv_turnover_rate.py --rebuild      # 强制重算并覆盖已有值（含非0的）
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


async def backfill(days: int, rebuild: bool) -> None:
    from app.core import database as db_module
    from zstock.data_management.query_service import COL_OHLCV
    from zstock.common.utils import xtquant_data_utils as xtu
    from zstock.common.utils.common_utils import normalize_date
    from pymongo import UpdateOne

    await db_module.db_manager.init_mongodb()
    db = db_module.db_manager.mongo_db
    col = db[COL_OHLCV]

    # OHLCV trade_date 存储为 'YYYY-MM-DD'
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    logger.info(f"回填区间 {start} ~ {end}  rebuild={rebuild}  (最近 {days} 天)")

    # rebuild=True 取全部；否则只取 turnover_rate 缺失/为0/为null 的文档
    base_q = {'period': 'D', 'trade_date': {'$gte': start, '$lte': end}}
    if rebuild:
        query = base_q
    else:
        query = {
            **base_q,
            '$or': [
                {'turnover_rate': {'$exists': False}},
                {'turnover_rate': {'$in': [0, 0.0, None]}},
            ],
        }

    docs = await col.find(
        query, {'code': 1, 'volume': 1, 'trade_date': 1, 'turnover_rate': 1, '_id': 1}
    ).to_list(None)
    logger.info(f"待回填文档 {len(docs)} 条")
    if not docs:
        logger.info("无需要回填的文档，退出")
        await db_module.db_manager.close_connections()
        return

    # 收集涉及的 code，批量取流通股本快照
    codes = sorted({d.get('code') for d in docs if d.get('code')})
    float_map = xtu.fetch_float_shares_map(codes)
    logger.info(f"取到流通股本 {len(float_map)} / {len(codes)} 只（取不到的将跳过）")

    ops = []
    skipped = 0
    for d in docs:
        code = d.get('code')
        vol = d.get('volume')
        fv = float_map.get(code, 0.0)
        if not code or vol is None or fv <= 0:
            skipped += 1
            continue
        try:
            tr = float(vol) * 10000.0 / fv
        except (ValueError, TypeError):
            skipped += 1
            continue
        ops.append(UpdateOne({'_id': d['_id']}, {'$set': {'turnover_rate': tr}}))

    if ops:
        res = await col.bulk_write(ops, ordered=False)
        logger.info(f"✅ 回填 turnover_rate {res.modified_count} 条（提交 {len(ops)} 条）")
    else:
        logger.info("无可用流通股本，未产生更新")
    if skipped:
        logger.info(f"跳过 {skipped} 条（无 volume 或无流通股本）")

    await db_module.db_manager.close_connections()


def main():
    ap = argparse.ArgumentParser(description="回填 OHLCV turnover_rate 字段")
    ap.add_argument('--days', type=int, default=30, help='回填最近 N 天（默认 30）')
    ap.add_argument('--rebuild', action='store_true', help='强制重算并覆盖已有 turnover_rate')
    args = ap.parse_args()
    asyncio.run(backfill(args.days, args.rebuild))


if __name__ == '__main__':
    main()
