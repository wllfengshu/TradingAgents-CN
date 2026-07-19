"""检查 zstock_sector 成分股完整度"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import database as db

async def check():
    await db.db_manager.init_mongodb()
    mongo_db = db.get_database()
    col = mongo_db['zstock_sector']

    total = await col.count_documents({})
    print(f'总板块: {total}')

    # 有成分股: stocks 是非空 list
    with_stocks = await col.count_documents({'stocks': {'$type': 'array', '$ne': []}})
    without_stocks = total - with_stocks
    print(f'有成分股: {with_stocks}')
    print(f'无成分股: {without_stocks}')

    # 按来源
    for src in ['xtquant', 'akshare']:
        src_total = await col.count_documents({'source': src})
        src_with = await col.count_documents({'source': src, 'stocks': {'$type': 'array', '$ne': []}})
        print(f'  {src}: {src_total} 板块, {src_with} 有成分股')

    # 成分股总数
    pipeline = [
        {'$match': {'stocks': {'$type': 'array', '$ne': []}}},
        {'$project': {'n': {'$size': '$stocks'}}},
        {'$group': {'_id': None, 'total': {'$sum': '$n'}, 'sectors': {'$sum': 1}}},
    ]
    r = await col.aggregate(pipeline).to_list(1)
    if r:
        print(f'成分股总数: {r[0]["total"]} (来自 {r[0]["sectors"]} 个板块)')

    await db.db_manager.close_connections()

asyncio.run(check())
