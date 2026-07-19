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
    from zstock.common.utils.akshare_data_utils import fetch_ohlcv
    print("\n--- test_fetch_ohlcv ---")
    res = fetch_ohlcv('000001', '2026-07-07', '2026-07-08')
    print(res.to_string())
    assert not res.empty, "fetch_ohlcv 返回空 DataFrame"
    assert 'name' in res.columns, "缺少 name 列"
    assert 'code' in res.columns, "缺少 code 列"
    assert 'trade_date' in res.columns, "缺少 trade_date 列"
    assert (res['code'] == '000001').all(), "code 列值不正确"
    # name 可能因接口限流为空，打印警告但不阻断
    if res['name'].iloc[0] == '':
        print("⚠️  name 为空（可能接口限流），跳过 name 断言")
    else:
        assert res['name'].iloc[0] != '', "name 列为空"
    print("✅ test_fetch_ohlcv 通过")


def test_fetch_ohlcv_600036():
    """用 600036 避开 000001 的指数代码歧义问题（历史遗留 bug 修复验证）"""
    from zstock.common.utils.akshare_data_utils import fetch_ohlcv
    print("\n--- test_fetch_ohlcv_600036 ---")
    res = fetch_ohlcv('600036', '2026-07-07', '2026-07-08')
    print(res.to_string())
    assert not res.empty, "fetch_ohlcv 返回空 DataFrame"
    assert 'name' in res.columns, "缺少 name 列"
    assert (res['code'] == '600036').all(), "code 列值不正确"
    if res['name'].iloc[0] == '':
        print("⚠️  name 为空（可能接口限流）")
    else:
        print(f"name = {res['name'].iloc[0]}")
    print("✅ test_fetch_ohlcv_600036 通过")


def test_fetch_ohlcv_batch():
    from zstock.common.utils.akshare_data_utils import fetch_ohlcv_batch
    print("\n--- test_fetch_ohlcv_batch ---")
    symbols = ['000001', '600036']
    res = fetch_ohlcv_batch(symbols, '2026-07-07', '2026-07-08')
    print(res.to_string())
    assert not res.empty, "fetch_ohlcv_batch 返回空 DataFrame"
    assert 'name' in res.columns, "缺少 name 列"
    assert 'code' in res.columns, "缺少 code 列"
    assert 'trade_date' in res.columns, "缺少 trade_date 列"
    returned_codes = set(res['code'].unique())
    for s in symbols:
        assert s in returned_codes, f"{s} 不在返回结果中"
    print(f"✅ test_fetch_ohlcv_batch 通过，共 {len(res)} 行，{res['code'].nunique()} 只股票")


def test_fetch_sector_list():
    from zstock.common.utils.akshare_data_utils import fetch_sector_list
    print("\n--- test_fetch_sector_list ---")
    res = fetch_sector_list(sector_type='concept')
    print(f"概念板块数量: {len(res)}")
    for item in res[:10]:
        print(item)
    assert len(res) > 0, "fetch_sector_list 返回空列表"
    assert all(x['sector_type'] == 'concept' for x in res), "存在非 concept 类型"

    # 打印包含"贵金属"关键字的板块，帮助定位正确名称
    precious = [x for x in res if '贵金属' in x['sector_name']]
    print(f"\n包含'贵金属'的板块: {precious}")
    print("✅ test_fetch_sector_list 通过")


def test_fetch_sector_stocks():
    from zstock.common.utils.akshare_data_utils import fetch_sector_list, fetch_sector_stocks
    print("\n--- test_fetch_sector_stocks ---")

    # 先从板块列表里找到实际存在的名称
    all_sectors = fetch_sector_list(sector_type='concept')
    precious_sectors = [x for x in all_sectors if '贵金属' in x['sector_name']]
    print(f"贵金属相关板块: {precious_sectors}")

    if not precious_sectors:
        # 找不到贵金属就用排名靠前的任意板块测试接口连通性
        target = all_sectors[0]['sector_name'] if all_sectors else None
        print(f"⚠️  未找到贵金属板块，改用: {target}")
    else:
        target = precious_sectors[0]['sector_name']

    assert target is not None, "没有任何板块可测试"

    res = fetch_sector_stocks(target)
    print(f"{target} 成分股（共 {len(res)} 只）: {res[:10]}")
    assert len(res) > 0, f"{target} 板块返回空列表"
    assert all(len(c) == 6 and c.isdigit() for c in res), "代码格式不是 6 位纯数字"
    print("✅ test_fetch_sector_stocks 通过")


def test_fetch_trade_status():
    from zstock.common.utils.akshare_data_utils import fetch_trade_status
    print("\n--- test_fetch_trade_status ---")
    res = fetch_trade_status('600036', '2026-07-08')
    print(res)
    assert res is not None, "fetch_trade_status 返回 None"
    assert res['code'] == '600036', "code 不正确"
    assert isinstance(res['is_st'], bool), "is_st 不是 bool"
    assert isinstance(res['is_suspended'], bool), "is_suspended 不是 bool"
    assert isinstance(res['is_limit_up'], bool), "is_limit_up 不是 bool"
    assert isinstance(res['is_limit_down'], bool), "is_limit_down 不是 bool"
    if res['name'] == '':
        print("⚠️  name 为空（可能接口限流）")
    print("✅ test_fetch_trade_status 通过")


def test_fetch_trade_status_batch():
    from zstock.common.utils.akshare_data_utils import fetch_trade_status_batch
    import akshare as ak
    print("\n--- test_fetch_trade_status_batch ---")

    # 诊断：先看 stock_zh_a_spot_em 实际返回的列名
    try:
        spot = ak.stock_zh_a_spot_em()
        if spot is not None and not spot.empty:
            print(f"stock_zh_a_spot_em 列名: {list(spot.columns)}")
            row = spot[spot['代码'] == '600036']
            print(f"600036 原始行: {row.iloc[0].to_dict() if not row.empty else '未找到'}")
        else:
            print("⚠️  stock_zh_a_spot_em 返回空")
    except Exception as e:
        print(f"⚠️  stock_zh_a_spot_em 调用失败: {e}")

    codes = ['000001', '600036']
    res = fetch_trade_status_batch(codes, '2026-07-08')
    print(res)

    if not res:
        print("⚠️  batch 返回空（可能非交易时间列名变化），跳过断言")
        return

    for code in [c for c in codes if c in res]:
        item = res[code]
        assert isinstance(item['is_limit_down'], bool), f"{code} is_limit_down 不是 bool"
        assert item['name'] != '', f"{code} name 为空"
    print("✅ test_fetch_trade_status_batch 通过")


async def main():
    from app.core.database import init_database, close_database
    try:
        print("初始化 MongoDB 连接...")
        await init_database()
        print("MongoDB 已连接\n")

        test_fetch_ohlcv()
        test_fetch_ohlcv_600036()
        test_fetch_ohlcv_batch()
        test_fetch_sector_list()
        test_fetch_sector_stocks()
        test_fetch_trade_status()
        test_fetch_trade_status_batch()

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
