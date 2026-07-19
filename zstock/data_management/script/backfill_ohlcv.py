"""
补数据脚本：zstock_ohlcv

流程：
  1. 删除 MongoDB 中 2025-03-31 之前的所有日线数据
  2. 检查 2025-04-01 ~ 今天 的数据完整度
  3. 对缺失数据的股票用 xtquant 补数据

用法：
    python backfill_ohlcv.py              # 执行完整流程
    python backfill_ohlcv.py --dry-run    # 仅打印计划，不执行
"""
import asyncio
import logging
import sys
from datetime import datetime
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

# ─────────────── 常量 ───────────────
COL_OHLCV = 'zstock_ohlcv'
CUTOFF_DATE = '2025-03-31'  # 删除此日期之前的数据
BACKFILL_START = '2025-04-01'  # 从此日期开始检查完整度


async def step1_delete_old_data(db, dry_run: bool = False):
    """删除 2025-03-31 之前的所有日线数据"""
    logger.info("=" * 60)
    logger.info(f"▶ Step 1: 删除 {CUTOFF_DATE} 之前的日线数据")

    filter_query = {
        'period': 'D',
        'trade_date': {'$lt': CUTOFF_DATE},
    }

    if dry_run:
        count = await db[COL_OHLCV].count_documents(filter_query)
        logger.info(f"  (dry-run) 将删除 {count:,} 条记录")
        return count

    result = await db[COL_OHLCV].delete_many(filter_query)
    logger.info(f"  ✓ 已删除 {result.deleted_count:,} 条记录")
    return result.deleted_count


async def step2_check_completeness(db) -> dict:
    """
    检查 2025-04-01 ~ 今天 的数据完整度

    Returns:
        dict: {
            'total_stocks': int,        # 应有数据的股票总数
            'complete_stocks': int,     # 数据完整的股票数
            'incomplete_stocks': list,  # 数据不完整的股票 [(code, count, expected)]
            'expected_days': int,       # 预期交易日数
        }
    """
    logger.info("=" * 60)
    logger.info(f"▶ Step 2: 检查 {BACKFILL_START} ~ 今天 的数据完整度")

    # 1. 获取全市场股票列表（从 stock_info）
    from zstock.data_management.query_service import DataQueryService
    qs = DataQueryService()
    all_codes, source = await qs.get_all_stocks()
    total_stocks = len(all_codes)
    logger.info(f"  全市场 {total_stocks} 只股票 (来源: {source})")

    # 2. 统计预期交易日数（用上证指数近似）
    expected_days = await _count_trading_days(db, BACKFILL_START)
    logger.info(f"  预期交易日数: ~{expected_days} 天")

    # 3. 统计每只股票在范围内的数据条数
    pipeline = [
        {'$match': {
            'period': 'D',
            'trade_date': {'$gte': BACKFILL_START},
            'code': {'$in': all_codes},
        }},
        {'$group': {'_id': '$code', 'count': {'$sum': 1}}},
    ]
    cursor = db[COL_OHLCV].aggregate(pipeline)
    code_count = {}
    async for doc in cursor:
        code_count[doc['_id']] = doc['count']

    # 4. 找出数据不完整的股票
    # 允许一定的容差（新上市股票交易日少）
    threshold = max(expected_days * 0.8, 10)  # 至少 80% 或 10 天

    complete = 0
    incomplete = []
    missing_codes = []

    for code in all_codes:
        cnt = code_count.get(code, 0)
        if cnt >= threshold:
            complete += 1
        elif cnt == 0:
            missing_codes.append(code)
            incomplete.append((code, 0, expected_days))
        else:
            incomplete.append((code, cnt, expected_days))

    logger.info(f"  数据完整: {complete} 只")
    logger.info(f"  数据不完整: {len(incomplete)} 只 (其中 {len(missing_codes)} 只完全缺失)")

    if incomplete:
        # 打印前 10 个示例
        sample = incomplete[:10]
        for code, cnt, exp in sample:
            logger.info(f"    {code}: {cnt}/{exp} 天")
        if len(incomplete) > 10:
            logger.info(f"    ... 还有 {len(incomplete) - 10} 只")

    return {
        'total_stocks': total_stocks,
        'complete_stocks': complete,
        'incomplete_stocks': incomplete,
        'missing_codes': missing_codes,
        'all_codes': all_codes,
        'expected_days': expected_days,
    }


async def _count_trading_days(db, start_date: str) -> int:
    """估算交易日数：用 600000.SH（上证指数）的数据条数近似"""
    result = await db[COL_OHLCV].count_documents({
        'code': '600000',  # 浦发银行，流动性好
        'period': 'D',
        'trade_date': {'$gte': start_date},
    })
    if result > 0:
        return result

    # 如果 600000 没数据，用 000001 试试
    result = await db[COL_OHLCV].count_documents({
        'code': '000001',
        'period': 'D',
        'trade_date': {'$gte': start_date},
    })
    if result > 0:
        return result

    # 都没有数据，估算：工作日减去节假日（约 240 天/年）
    start = datetime.strptime(start_date, '%Y-%m-%d')
    today = datetime.now()
    days = (today - start).days
    # 粗略估算：工作日占 70%
    return max(int(days * 0.7), 10)


async def step3_backfill(db, completeness_info: dict, dry_run: bool = False):
    """用 xtquant 补充缺失数据"""
    logger.info("=" * 60)
    logger.info("▶ Step 3: 用 xtquant 补充缺失数据")

    incomplete = completeness_info['incomplete_stocks']
    if not incomplete:
        logger.info("  所有股票数据完整，无需补数据")
        return 0

    # 提取需要补数据的股票代码
    codes_to_fill = [code for code, _, _ in incomplete]
    logger.info(f"  需要补数据: {len(codes_to_fill)} 只股票")

    if dry_run:
        logger.info(f"  (dry-run) 将补 {len(codes_to_fill)} 只股票的数据")
        return 0

    # 导入 xtquant 工具
    from zstock.common.utils import xtquant_data_utils as xtu

    # 分批处理，每批 100 只
    BATCH_SIZE = 100
    total_written = 0

    for i in range(0, len(codes_to_fill), BATCH_SIZE):
        batch_codes = codes_to_fill[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(codes_to_fill) + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"  [{batch_num}/{total_batches}] 批量拉取 {len(batch_codes)} 只 ...")

        # 批量拉取
        try:
            df = xtu.fetch_ohlcv_batch(batch_codes, BACKFILL_START, '')
            if df is None or df.empty:
                logger.warning(f"    远程返回空")
                continue

            logger.info(f"    拉取到 {len(df):,} 行")

            # 分批写入 MongoDB
            from pymongo import UpdateOne
            ops = []
            for record in df.to_dict(orient='records'):
                code = record.get('code')
                td = record.get('trade_date')
                if not code or not td:
                    continue
                ops.append(UpdateOne(
                    {'code': code, 'trade_date': td, 'period': 'D'},
                    {'$set': {**record, 'period': 'D'}},
                    upsert=True,
                ))

            if ops:
                await db[COL_OHLCV].bulk_write(ops, ordered=False)
                total_written += len(ops)
                logger.info(f"    ✓ 写入 {len(ops):,} 条")

        except Exception as e:
            logger.error(f"    批量拉取失败: {e}")
            continue

    logger.info(f"  ✓ 补数据完成: 共写入 {total_written:,} 条")
    return total_written


async def main():
    import argparse
    parser = argparse.ArgumentParser(description='补数据脚本')
    parser.add_argument('--dry-run', action='store_true', help='仅打印计划，不执行')
    args = parser.parse_args()

    # 1. 连接 MongoDB
    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()
    db = db_module.get_database()
    logger.info("✓ MongoDB 已连接")

    t0 = datetime.now()

    # 2. Step 1: 删除旧数据
    # deleted = await step1_delete_old_data(db, args.dry_run)

    # 3. Step 2: 检查完整度
    completeness = await step2_check_completeness(db)

    # 4. Step 3: 补数据
    written = await step3_backfill(db, completeness, args.dry_run)

    # 5. 汇总
    elapsed = (datetime.now() - t0).total_seconds()
    logger.info("=" * 60)
    # if args.dry_run:
    #     logger.info(f"dry-run 完成，耗时 {elapsed:.1f}s")
    # else:
    #     logger.info(f"全部完成: 删除 {deleted:,} 条，补入 {written:,} 条，耗时 {elapsed:.1f}s")
    logger.info("=" * 60)

    await db_module.db_manager.close_connections()


if __name__ == '__main__':
    asyncio.run(main())
