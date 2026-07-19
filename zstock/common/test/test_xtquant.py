import asyncio
import sys
from pathlib import Path
import pandas as pd
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_fetch_ohlcv():
    from zstock.common.utils.xtquant_data_utils import fetch_ohlcv
    print("\n--- test_fetch_ohlcv ---")
    res = fetch_ohlcv('000001', '2026-07-01', '2026-07-08')
    print(res.to_string())
    assert not res.empty, "fetch_ohlcv 返回空 DataFrame"
    assert 'name' in res.columns, "缺少 name 列"
    assert 'code' in res.columns, "缺少 code 列"
    assert 'trade_date' in res.columns, "缺少 trade_date 列"
    assert (res['code'] == '000001').all(), "code 列值不正确"
    assert res['name'].iloc[0] != '', "name 列为空"
    print("✅ test_fetch_ohlcv 通过")


def test_fetch_ohlcv_batch():
    from zstock.common.utils.xtquant_data_utils import fetch_ohlcv_batch
    print("\n--- test_fetch_ohlcv_batch ---")
    symbols = ['000001', '600036', '000651']
    res = fetch_ohlcv_batch(symbols, '2026-07-01', '2026-07-08')
    print(res.to_string())
    assert not res.empty, "fetch_ohlcv_batch 返回空 DataFrame"
    assert 'name' in res.columns, "缺少 name 列"
    assert 'code' in res.columns, "缺少 code 列"
    assert 'trade_date' in res.columns, "缺少 trade_date 列"
    returned_codes = set(res['code'].unique())
    for s in symbols:
        assert s in returned_codes, f"{s} 不在返回结果中"
    assert res['name'].ne('').any(), "所有 name 列均为空"
    print(f"✅ test_fetch_ohlcv_batch 通过，共 {len(res)} 行，{res['code'].nunique()} 只股票")


def test_fetch_sector_list():
    from zstock.common.utils.xtquant_data_utils import fetch_sector_list
    print("\n--- test_fetch_sector_list ---")
    res = fetch_sector_list()
    for item in res:
        print(item)
    assert len(res) > 0, "fetch_sector_list 返回空列表"
    concepts = [x for x in res if x['sector_type'] == 'concept']
    print(f"\n概念板块数量: {len(concepts)}")
    assert len(concepts) > 0, "没有获取到概念板块，板块数据可能未下载"
    print("✅ test_fetch_sector_list 通过")


def test_fetch_sector_stocks():
    from zstock.common.utils.xtquant_data_utils import fetch_sector_stocks
    print("\n--- test_fetch_sector_stocks ---")
    res = fetch_sector_stocks('THY2贵金属')
    print(f"贵金属板块成分股（共 {len(res)} 只）: {res}")
    assert len(res) > 0, "贵金属板块返回空列表"
    assert all(len(c) == 6 and c.isdigit() for c in res), "代码格式不是 6 位纯数字"
    print("✅ test_fetch_sector_stocks 通过")


async def main():
    from app.core.database import init_database, close_database
    try:
        print("初始化 MongoDB 连接...")
        await init_database()
        print("MongoDB 已连接\n")

        test_fetch_ohlcv()
        test_fetch_ohlcv_batch()
        test_fetch_sector_list()
        test_fetch_sector_stocks()

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print("\n🔌 清理资源...")
        await close_database()
        print("✅ 已断开数据库连接\n")

    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
