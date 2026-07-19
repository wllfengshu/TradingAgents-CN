"""
删除 zstock_capital_flow 中 period='today' 的数据

用法：
    python zstock/data_management/script/delete_today_period.py
    python zstock/data_management/script/delete_today_period.py --dry-run   # 只统计不删除
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

async def main():
    dry_run = "--dry-run" in sys.argv

    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()
    db = db_module.db_manager.mongo_db
    from zstock.data_management.query_service import COL_CAPITAL_FLOW
    col = db[COL_CAPITAL_FLOW]

    # 统计数量
    query = {"period": {"$eq": "today"}}
    count = await col.count_documents(query)
    print(f"匹配 period 含 'today' 的记录数: {count}")

    if count == 0:
        print("无需删除")
        await db_module.db_manager.close_connections()
        return

    if dry_run:
        print("(dry-run 模式，不执行删除)")
        # 打印几条样例
        cursor = col.find(query).limit(5)
        async for doc in cursor:
            doc.pop("_id", None)
            print(f"  样例: code={doc.get('code')} trade_date={doc.get('trade_date')} period={doc.get('period')}")
        await db_module.db_manager.close_connections()
        return

    # 确认
    confirm = input(f"确认删除 {count} 条记录？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消")
        await db_module.db_manager.close_connections()
        return

    result = await col.delete_many(query)
    print(f"✅ 已删除 {result.deleted_count} 条记录")
    await db_module.db_manager.close_connections()


if __name__ == "__main__":
    asyncio.run(main())

