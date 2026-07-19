"""
手动数据同步脚本 — 一个 main 同步 2 张表
一般不用更新
    zstock_stock_info  — 股票列表 + is_mainboard 标志
    zstock_sector      — 板块元数据 + 成分股

"""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.common.utils.common_utils import is_main_board, is_st


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# ─────────────────── 可调参数 ───────────────────
OHLCV_DAYS = 30          # 同步最近 N 天日线
# ────────────────────────────────────────────────



async def _persist_stock_flags(stock_list: List[Dict[str, str]], batch_size: int = 500) -> None:
    """将股票列表写入 stock_info（根据 code/name 自动判定 is_mainboard / is_st）。

    分批写入避免超时。

    Args:
        stock_list: [{'code': '000001', 'name': '平安银行'}, ...]
        batch_size: 每批写入条数，默认 500
    """
    if not stock_list:
        return
    try:
        from pymongo import UpdateOne
        from app.core import database as db_module
        await db_module.db_manager.init_mongodb()
        db = db_module.db_manager.mongo_db
        now = datetime.utcnow()
        ops = [
            UpdateOne(
                {'code': s['code']},
                {'$set': {
                    'code': s['code'],
                    'name': s.get('name', ''),
                    'is_mainboard': is_main_board(s['code']),
                    'is_st': is_st(s.get('name', '')),
                    'updated_at': now,
                }},
                upsert=True,
            )
            for s in stock_list if s.get('code')
        ]
        total = len(ops)
        from zstock.data_management.query_service import DataQueryService, COL_STOCK_INFO
        for i in range(0, total, batch_size):
            batch = ops[i:i + batch_size]
            await db[COL_STOCK_INFO].bulk_write(batch, ordered=False)
            logger.debug(f"  stock_flags 批次 {i // batch_size + 1}: 写入 {len(batch)} 条")
        logger.info(f"💾 落库 {COL_STOCK_INFO} 标志: {total} 条（分 {(total - 1) // batch_size + 1} 批）")
    except Exception as e:
        logger.error(f"stock_info 标志落库失败: {e}")

async def _persist_sector_meta(sectors: List[Dict[str, str]]) -> None:
    if not sectors:
        return
    try:
        from pymongo import UpdateOne
        from app.core import database as db_module
        await db_module.db_manager.init_mongodb()
        db = db_module.db_manager.mongo_db
        now = datetime.utcnow()
        from zstock.data_management.query_service import DataQueryService, COL_SECTOR
        ops = [
            UpdateOne(
                {'sector_code': s['sector_code'], 'source': 'xtquant'},
                {'$set': {'sector_code': s['sector_code'],
                          'sector_name': s['sector_name'],
                          'sector_type': s['sector_type'],
                          'source': 'xtquant',
                          'updated_at': now}},
                upsert=True,
            )
            for s in sectors if s.get('sector_code')
        ]
        for i in range(0, len(ops), 200):
            await db[COL_SECTOR].bulk_write(ops[i:i + 200], ordered=False)
        logger.info(f"💾 落库 {COL_SECTOR} 元数据(xtquant): {len(ops)} 条")
    except Exception as e:
        logger.error(f"sector 元数据落库失败: {e}")

async def _persist_sector_stocks(sector_code: str, codes: List[str]) -> None:
    if not codes:
        return
    try:
        from app.core import database as db_module
        await db_module.db_manager.init_mongodb()
        db = db_module.db_manager.mongo_db
        from zstock.data_management.query_service import DataQueryService, COL_SECTOR
        await db[COL_SECTOR].update_one(
            {'sector_code': sector_code, 'source': 'xtquant'},
            {'$set': {'stocks': codes, 'stocks_updated_at': datetime.utcnow()}},
            upsert=True,
        )
        logger.info(f"💾 落库 {COL_SECTOR}({sector_code}/xtquant) stocks: {len(codes)} 只")
    except Exception as e:
        logger.error(f"sector stocks 落库失败: {e}")



async def main():
    # 1. 连接 MongoDB
    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()
    logger.info("MongoDB 已连接")

    # 2. 初始化服务
    from zstock.data_management.query_service import DataQueryService
    qs = DataQueryService()
    await qs.ensure_indexes()
    from zstock.common.utils import xtquant_data_utils as xtu
    logger.info(f"数据源: xtquant")

    t0 = datetime.now()

    # ── 表1: stock_info ──
    logger.info("▶ [1/3] stock_info — 股票列表")
    all_codes = xtu.fetch_all_stocks()
    await _persist_stock_flags(all_codes)
    logger.info(f"  ✓ 全市场 {len(all_codes)} 只")

    # ── 表3: sector ──
    # logger.info("▶ [3/3] sector — 板块 + 成分股")
    # sectors = dtu.fetch_sector_list()
    # await _persist_sector_meta(sectors)
    # logger.info(f"  元数据 {len(sectors)} 个板块，开始拉取成分股...")
    # total_stocks = 0
    # for i, s in enumerate(sectors, 1):
    #     try:
    #         stocks = dtu.fetch_sector_stocks(s['sector_code'], sector_name=s.get('sector_name', ''))
    #         if stocks:
    #             await _persist_sector_stocks(s['sector_code'], stocks)
    #             total_stocks += len(stocks)
    #     except Exception as e:
    #         logger.warning(f"  ✗ 板块 {s['sector_code']}: {e}")
    #     if i % 50 == 0:
    #         logger.info(f"  进度 {i}/{len(sectors)}  累计成分股 {total_stocks}")
    # logger.info(f"  ✓ sector {len(sectors)} 个板块，{total_stocks} 条成分股")

    # ── 汇总 ──
    elapsed = (datetime.now() - t0).total_seconds()
    logger.info("=" * 50)
    logger.info(f"同步完成  耗时 {elapsed:.1f}s")
    logger.info(f"  stock_info : {len(all_codes)} 只")
    # logger.info(f"  sector     : {len(sectors)} 个板块 / {total_stocks} 条成分股")
    logger.info("=" * 50)

    await db_module.db_manager.close_connections()




if __name__ == '__main__':
    asyncio.run(main())
