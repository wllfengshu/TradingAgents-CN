"""
龙虎榜数据同步脚本（QMT → MongoDB）

用法：
    python -m zstock.data_management.script.sync_lhb --date 2026-08-07
    python -m zstock.data_management.script.sync_lhb --start 2026-08-01 --end 2026-08-07
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def sync_longhubang(start_date: str, end_date: str) -> int:
    """同步龙虎榜数据"""
    logger.info("=" * 70)
    logger.info(f"🚀 龙虎榜数据同步")
    logger.info(f"   区间: {start_date} → {end_date}")
    logger.info("=" * 70)

    try:
        from app.core import database as db_module
        from zstock.common.utils import xtquant_data_utils as xtu

        # 初始化 MongoDB
        await db_module.db_manager.init_mongodb()
        db = db_module.db_manager.mongo_db
        logger.info("✅ MongoDB 已连接")
        col = db['zstock_lhb']

        # 从 QMT 获取龙虎榜（需要提供股票代码列表）
        # 用真实全市场代码，而非硬编码前 500 只
        logger.info("📊 准备股票代码列表...")
        all_stocks = xtu.fetch_all_stocks()
        all_codes = [s["code"] for s in all_stocks if s.get("code")]
        logger.info(f"✅ 准备查询 {len(all_codes)} 只股票的龙虎榜数据...")

        # 从 QMT 获取龙虎榜
        lhb_records = await asyncio.get_event_loop().run_in_executor(
            None, lambda: xtu.fetch_lhb(all_codes, start_date, end_date)
        )
        logger.info(f"✅ 从 QMT 获取到 {len(lhb_records)} 条龙虎榜记录")

        if not lhb_records:
            logger.warning("⚠️ 无龙虎榜数据可保存")
            return 0

        # 插入或更新到 MongoDB
        upserted = 0
        for rec in lhb_records:
            code = rec.get("code")
            trade_date = rec.get("trade_date")
            if not code or not trade_date:
                continue

            result = await col.replace_one(
                {"code": code, "trade_date": trade_date},
                rec,
                upsert=True
            )
            upserted += 1 if (result.upserted_id is not None or result.modified_count > 0) else 0

        logger.info(f"✅ 保存龙虎榜到 MongoDB: {upserted} 条记录")

        logger.info("=" * 70)
        logger.info(f"✅ 龙虎榜同步完成: {upserted} 条")
        logger.info("=" * 70)
        return upserted

    except Exception as e:
        logger.error(f"❌ 龙虎榜同步失败: {e}", exc_info=True)
        return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="龙虎榜数据同步")
    parser.add_argument("--date", help="同步单个日期 YYYY-MM-DD")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    args, unknown = parser.parse_known_args()

    if args.date:
        start_date = end_date = args.date
    elif args.start and args.end:
        start_date = args.start
        end_date = args.end
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        start_date = end_date = today

    return await sync_longhubang(start_date, end_date)


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
