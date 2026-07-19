"""
同步资金流数据到 zstock_capital_flow

东财全市场排名接口只能拿当日快照，因此需要每天跑一次来积累历史。
支持拉取多个时间窗口（today/3day/5day/10day），一次跑完。

建议 cron: 每个交易日 15:30 执行
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

# 要同步的时间窗口（today 必须，其余按需）
PERIODS = ['today']

async def _persist_capital_flow_bulk(df) -> int:
    """批量写入资金流 DataFrame（a_stock_data 格式）。返回写入条数。"""
    if df is None or df.empty:
        return 0
    try:
        from pymongo import UpdateOne
        from app.core import database as db_module
        await db_module.db_manager.init_mongodb()
        db = db_module.db_manager.mongo_db
        logger.info("MongoDB 已连接")
        ops = []
        for _, row in df.iterrows():
            ops.append(UpdateOne(
                {'code': row['code'], 'trade_date': row['trade_date'], 'period': row.get('period', '')},
                {'$set': {**row.to_dict(), 'updated_at': datetime.utcnow()}},
                upsert=True,
            ))
        if ops:
            from zstock.data_management.query_service import DataQueryService, COL_CAPITAL_FLOW
            await db[COL_CAPITAL_FLOW].bulk_write(ops, ordered=False)
        return len(ops)
    except Exception as e:
        logger.error(f"capital_flow 批量落库失败: {e}")
        return 0

async def sync_one_period(qs, period: str, max_retries: int = 100) -> int:
    """同步单个时间窗口的资金流，返回写入条数。"""
    from zstock.common.utils import a_stock_data_utils as emu

    for attempt in range(max_retries):
        try:
            loop = asyncio.get_running_loop()
            df = await loop.run_in_executor(None, emu.fetch_money_flow_all, period)
            if df.empty:
                logger.warning(f"  {period}: 返回空（可能非交易时段或被风控）")
                return 0
            written = await _persist_capital_flow_bulk(df)
            logger.info(f"  {period}: {len(df)} 只, 写入 {written} 条")
            return written
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 20 * (attempt + 1)
                logger.warning(f"  {period} 第{attempt+1}次失败, {wait}s后重试: {e}")
                await asyncio.sleep(wait)
            else:
                logger.error(f"  {period} {max_retries}次全部失败: {e}")
    return 0


async def main():
    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()
    db = db_module.db_manager.mongo_db
    logger.info("MongoDB 已连接")

    from zstock.data_management.query_service import DataQueryService
    qs = DataQueryService()

    col = db['zstock_capital_flow']
    before = await col.count_documents({})

    total = 0
    for period in PERIODS:
        n = await sync_one_period(qs, period)
        total += n

    after = await col.count_documents({})
    dates = await col.distinct('trade_date')

    logger.info("=" * 50)
    logger.info(f"同步完成: 前={before}, 后={after}, 新增={after - before}")
    logger.info(f"累计 {len(dates)} 个交易日: {sorted(dates)}")
    logger.info("=" * 50)

    await db_module.db_manager.close_connections()


if __name__ == '__main__':
    asyncio.run(main())
