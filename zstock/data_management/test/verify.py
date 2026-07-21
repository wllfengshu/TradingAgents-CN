"""
xtquant 回源 + MongoDB 落库 集成测试。

要求：
- 本机 miniQMT 已启动且 xtquant 可连接
- 本机 MongoDB 已启动

覆盖 query_service 全部公开方法：
    - get_all_stocks          (返回完整 stock_info 文档列表)
    - get_stock_info          (单只股票信息)
    - get_ohlcv               (单只 OHLCV)
    - get_ohlcv_batch         (批量 OHLCV)
    - get_capital_flow        (单只资金流)
    - get_capital_flow_batch  (批量资金流)
    - get_sector_list         (板块列表)
    - get_sector_stocks       (单板块成分股)
    - get_sector_stocks_batch (批量板块成分股)
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def main() -> bool:
    print("\n" + "=" * 70)
    print("✅ query_service 集成测试（MongoDB）")
    print("=" * 70 + "\n")

    # 1. 初始化 MongoDB
    from app.core import database as db_module
    print("📌 步骤 1: 初始化 MongoDB...")
    await db_module.db_manager.init_mongodb()
    db_module.mongo_client = db_module.db_manager.mongo_client
    db_module.mongo_db = db_module.db_manager.mongo_db
    print("✅ MongoDB 已连接\n")

    # 2. 构造查询服务
    print("📌 步骤 2: 获取数据查询服务...")
    from zstock.data_management.query_service import DataQueryService
    query_service = DataQueryService()
    print("✅ 查询服务已就绪\n")

    ok = True
    today = datetime.now().strftime('%Y-%m-%d')

    # ======================== get_all_stocks ========================
    all_stock_docs = []
    try:
        print("📌 测试 get_all_stocks ...")
        all_stock_docs, source = await query_service.get_all_stocks()
        print(f"  ✅ 全市场股票 {len(all_stock_docs)} 只 (来自 {source})")
        # 验证返回的是完整文档（含 code、is_mainboard 等）
        sample = all_stock_docs[0] if all_stock_docs else {}
        expected_keys = {'code'}
        missing = expected_keys - set(sample.keys())
        if missing:
            print(f"  ⚠️ 文档缺少必要字段: {missing}")
        else:
            print(f"  ✅ 文档字段完整, 示例 keys: {list(sample.keys())[:8]}")
        # 统计
        codes = [d['code'] for d in all_stock_docs]
        mainboard_count = sum(1 for d in all_stock_docs if d.get('is_mainboard'))
        st_count = sum(1 for d in all_stock_docs if d.get('is_st'))
        print(f"  📊 主板={mainboard_count}, ST={st_count}")
        print(f"     示例: {all_stock_docs[:2]}")
    except Exception as e:
        ok = False
        codes = []
        print(f"  ❌ get_all_stocks 失败: {e}")

    # ======================== get_stock_info ========================
    try:
        test_code = '600000'
        print(f"\n📌 测试 get_stock_info({test_code}) ...")
        info, source = await query_service.get_stock_info(test_code)
        print(f"  ✅ stock_info (来自 {source}):")
        print(f"     code={info.get('code')}, name={info.get('name')}, "
              f"is_mainboard={info.get('is_mainboard')}, is_st={info.get('is_st')}")
    except Exception as e:
        ok = False
        print(f"  ❌ get_stock_info 失败: {e}")

    # ======================== get_ohlcv ========================
    try:
        print(f"\n📌 测试 get_ohlcv(600000, 2026-06-01 ~ 2026-06-03) ...")
        df, source = await query_service.get_ohlcv('600000', '2026-06-01', '2026-06-03')
        print(f"  ✅ OHLCV {len(df)} 行 (来自 {source})")
        print(df.head(3).to_string(index=False))
    except Exception as e:
        ok = False
        print(f"  ❌ get_ohlcv 失败: {e}")

    # ======================== get_ohlcv_batch ========================
    try:
        batch_codes = codes[:5] if len(codes) >= 5 else codes[:2]
        print(f"\n📌 测试 get_ohlcv_batch({batch_codes}, 2026-06-01 ~ 2026-06-03) ...")
        result = await query_service.get_ohlcv_batch(batch_codes, '2026-06-01', '2026-06-03')
        print(f"  ✅ OHLCV batch: {len(result)} 只有数据")
        for code, df in list(result.items())[:2]:
            print(f"     {code}: {len(df)} 行")
    except Exception as e:
        ok = False
        print(f"  ❌ get_ohlcv_batch 失败: {e}")

    # ======================== get_capital_flow ========================
    try:
        print(f"\n📌 测试 get_capital_flow(600000, 2026-07-06) ...")
        flow, source = await query_service.get_capital_flow('600000', '20260706')
        print(f"  ✅ capital_flow (来自 {source}):")
        print(f"     {flow}")
    except Exception as e:
        ok = False
        print(f"  ❌ get_capital_flow 失败: {e}")

    # ======================== get_capital_flow_batch ========================
    try:
        batch_codes_flow = codes[:10] if len(codes) >= 10 else codes
        print(f"\n📌 测试 get_capital_flow_batch({len(batch_codes_flow)}只, 2026-07-06) ...")
        result = await query_service.get_capital_flow_recent_days(batch_codes_flow, '20260706')
        print(f"  ✅ capital_flow batch: {len(result)} 只有数据")
        for code, flow in list(result.items())[:2]:
            print(f"     {code}: main_inflow={flow.get('main_inflow')}")
    except Exception as e:
        ok = False
        print(f"  ❌ get_capital_flow_batch 失败: {e}")

    # ======================== get_sector_list ========================
    sectors = []
    try:
        print(f"\n📌 测试 get_sector_list ...")
        sectors, source = await query_service.get_sector_list()
        print(f"  ✅ 板块 {len(sectors)} 个 (来自 {source})")
        print(f"     示例: {sectors[:3]}")
    except Exception as e:
        ok = False
        print(f"  ❌ get_sector_list 失败: {e}")

    # ======================== get_sector_stocks ========================
    try:
        test_sector = sectors[0]['sector_code'] if sectors else '银行'
        print(f"\n📌 测试 get_sector_stocks({test_sector}) ...")
        rows, source = await query_service.get_sector_stocks(test_sector)
        print(f"  ✅ {test_sector} 成分 {len(rows)} 只 (来自 {source})")
        if rows:
            print(f"     示例: {rows[:5]}")
    except Exception as e:
        ok = False
        print(f"  ❌ get_sector_stocks 失败: {e}")

    # ======================== get_sector_stocks_batch ========================
    try:
        batch_sectors = [s['sector_code'] for s in sectors[:5]] if len(sectors) >= 5 else [s['sector_code'] for s in sectors]
        print(f"\n📌 测试 get_sector_stocks_batch({batch_sectors}) ...")
        result = await query_service.get_sector_stocks_batch(batch_sectors)
        print(f"  ✅ sector_stocks batch: {len(result)} 个板块有成分股")
        for sc, stocks in list(result.items())[:2]:
            print(f"     {sc}: {len(stocks)} 只")
    except Exception as e:
        ok = False
        print(f"  ❌ get_sector_stocks_batch 失败: {e}")

    # ======================== MongoDB 落库验证 ========================
    print("\n📌 验证 MongoDB 落库结果 ...")
    db = db_module.db_manager.mongo_db
    from zstock.data_management.query_service import (
        COL_STOCK_INFO, COL_OHLCV, COL_CAPITAL_FLOW, COL_SECTOR,
    )
    checks = [
        ('stock_info(总数)',       COL_STOCK_INFO,   {}),
        ('stock_info(主板)',       COL_STOCK_INFO,   {'is_mainboard': True}),
        ('sector(板块数)',         COL_SECTOR,       {}),
        ('sector(含成分股)',       COL_SECTOR,       {'stocks': {'$exists': True, '$not': {'$size': 0}}}),
        ('ohlcv',                 COL_OHLCV,        {}),
        ('capital_flow',          COL_CAPITAL_FLOW,  {}),
    ]
    for label, coll, query in checks:
        cnt = await db[coll].count_documents(query)
        status = "✅" if cnt > 0 else "⚠️"
        print(f"   {status} {label:<30}: {cnt}")

    # ======================== 结果汇总 ========================
    print("\n" + "=" * 70)
    if ok:
        print("🎉 全部测试通过！")
    else:
        print("❌ 部分测试失败，请检查上方日志")
    print("=" * 70)

    # 清理
    print("\n📌 清理 ...")
    await db_module.db_manager.close_connections()
    print("✅ 已断开\n")

    return ok


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
