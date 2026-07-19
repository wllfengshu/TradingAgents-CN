"""Check MongoDB CSI 300 index data"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from zstock.data_management.database_service import get_database_service


async def check_index_data():
    # Initialize MongoDB
    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()

    db = db_module.db_manager.mongo_db

    # 1. Find all possible CSI 300 codes
    print("=" * 80)
    print("1. Find all possible CSI 300 codes")
    print("=" * 80)

    possible_codes = ['399300', '399300.SZ', '000300', '000300.SH', 'HS300']

    for code in possible_codes:
        count = await db.zstock_ohlcv.count_documents({'code': code, 'period': 'D'})
        if count > 0:
            print(f"[OK] Found code: {code}, total {count} records")

            # Query date range
            dates = await db.zstock_ohlcv.distinct('trade_date', {'code': code, 'period': 'D'})
            dates_sorted = sorted(dates)
            print(f"   Date range: {dates_sorted[0]} ~ {dates_sorted[-1]}")
            print(f"   Data around 2026-05-27:")
            for d in dates_sorted:
                if '2026-05-2' in d or '2026-04-2' in d or '2026-04-3' in d:
                    print(f"     - {d}")

    # 2. Query detailed data for 399300
    print("\n" + "=" * 80)
    print("2. Query detailed data for 399300 in May 2026")
    print("=" * 80)

    cursor = db.zstock_ohlcv.find({
        'code': '399300',
        'period': 'D',
        'trade_date': {'$regex': '^2026-0[45]'}
    }).sort('trade_date', 1)

    count = 0
    async for doc in cursor:
        print(f"  {doc['trade_date']}: close={doc['close']:.2f}, volume={doc['volume']:.0f}")
        count += 1

    print(f"\nTotal: {count} records")

    # 3. Test query_service query
    print("\n" + "=" * 80)
    print("3. Test query_service.get_ohlcv_batch")
    print("=" * 80)

    from zstock.data_management.query_service import get_data_query_service
    qs = get_data_query_service()

    test_dates = [
        ('2026-04-27', '2026-05-27'),
        ('2026-04-28', '2026-05-28'),
        ('2026-04-29', '2026-05-29'),
    ]

    for start, end in test_dates:
        print(f"\nQuery range: {start} ~ {end}")
        result = await qs.get_ohlcv_batch(['399300'], start, end)
        if '399300' in result:
            df = result['399300']
            print(f"  Returned {len(df)} records")
            print(f"  Date range: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
        else:
            print(f"  [ERROR] No data returned")


if __name__ == '__main__':
    asyncio.run(check_index_data())
