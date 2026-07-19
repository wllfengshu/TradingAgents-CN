"""
诊断脚本：检查 zstock_ohlcv 表的数据情况
- 全表日期范围
- 指数（399300/899050/000300 等）日期范围与条数
- 个股样本日期范围
- 与生产管线 trade_date 的对齐情况

用法：
    python check_ohlcv_coverage.py
    python check_ohlcv_coverage.py --trade-date 2026-05-01     # 检查指定交易日的数据覆盖
"""
import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 指数 code（沪深300 在库里存的是 399300，不是 000300；上证指数 000001 与平安银行撞 code）
INDEX_CODES = [
    ('399300', '沪深300'),
    ('000300', '沪深300(上交所)'),
    ('899050', '北证50'),
    ('000905', '中证500/ETF?'),
    ('000688', '科创?'),
    ('399001', '深证成指'),
    ('399006', '创业板指'),
    ('000016', '上证50'),
]


async def inspect(trade_date: str = None) -> None:
    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()
    db = db_module.db_manager.mongo_db
    col = db['zstock_ohlcv']

    # 1. 全表概况
    total = await col.count_documents({'period': 'D'})
    distinct_codes = await col.distinct('code', {'period': 'D'})
    date_range = await col.aggregate([
        {'$match': {'period': 'D'}},
        {'$group': {'_id': None,
                    'min_date': {'$min': '$trade_date'},
                    'max_date': {'$max': '$trade_date'}}},
    ]).to_list(1)
    print('=' * 70)
    print('zstock_ohlcv 表概况')
    print('=' * 70)
    print(f"  D 线总条数      : {total}")
    print(f"  distinct codes  : {len(distinct_codes)}")
    if date_range:
        print(f"  最早 trade_date : {date_range[0]['min_date']}")
        print(f"  最晚 trade_date : {date_range[0]['max_date']}")

    # 2. 指数数据检查
    print('\n' + '=' * 70)
    print('指数数据覆盖')
    print('=' * 70)
    print(f"  {'code':<10} {'名称':<14} {'条数':>6}  {'最早':>12}  {'最晚':>12}  {'备注'}")
    print('  ' + '-' * 68)
    for code, name in INDEX_CODES:
        cnt = await col.count_documents({'code': code, 'period': 'D'})
        if cnt == 0:
            print(f"  {code:<10} {name:<14} {'0':>6}  {'-':>12}  {'-':>12}  ❌ 无数据")
            continue
        dr = await col.aggregate([
            {'$match': {'code': code, 'period': 'D'}},
            {'$group': {'_id': None,
                        'min_date': {'$min': '$trade_date'},
                        'max_date': {'$max': '$trade_date'}}},
        ]).to_list(1)
        mn = dr[0]['min_date'] if dr else '-'
        mx = dr[0]['max_date'] if dr else '-'
        # 判断是否可能为真实指数：close > 100 大概率为指数（个股价格一般 < 500 但多数 < 200）
        sample = await col.find_one({'code': code, 'period': 'D'}, sort=[('trade_date', -1)])
        close_val = sample.get('close') if sample else None
        vol_val = sample.get('volume') if sample else None
        name_val = sample.get('name', '') if sample else ''
        note = ''
        if code == '000001':
            # 000001 在库里可能是平安银行而非上证指数
            stock_info = await db['zstock_stock_info'].find_one({'code': '000001'})
            if stock_info and '平安' in stock_info.get('name', ''):
                note = f"⚠️ 实际为 {stock_info.get('name')}（与上证指数撞 code）"
            else:
                note = "可能是上证指数（但 close 通常 >2000）"
        if close_val and close_val > 500:
            note = note or "✓ 指数（close>500）"
        elif close_val and close_val < 100:
            note = note or "⚠️ 更像个股/ETF（close<100）"
        print(f"  {code:<10} {name:<14} {cnt:>6}  {mn:>12}  {mx:>12}  {note}")

    # 3. 个股样本
    print('\n' + '=' * 70)
    print('个股样本（随机 3 只）')
    print('=' * 70)
    sample_codes = list(distinct_codes[:3])
    for code in sample_codes:
        cnt = await col.count_documents({'code': code, 'period': 'D'})
        dr = await col.aggregate([
            {'$match': {'code': code, 'period': 'D'}},
            {'$group': {'_id': None,
                        'min_date': {'$min': '$trade_date'},
                        'max_date': {'$max': '$trade_date'}}},
        ]).to_list(1)
        mn = dr[0]['min_date'] if dr else '-'
        mx = dr[0]['max_date'] if dr else '-'
        name_doc = await db['zstock_stock_info'].find_one({'code': code})
        name = name_doc.get('name', '-') if name_doc else '-'
        print(f"  {code} ({name}): {cnt} 条  [{mn} ~ {mx}]")

    # 4. 针对特定 trade_date 检查覆盖
    if trade_date:
        print(f'\n{"=" * 70}')
        print(f'针对 trade_date = {trade_date} 的覆盖')
        print(f'{"=" * 70}')
        # 4.1 指数覆盖
        for code, name in INDEX_CODES:
            cnt = await col.count_documents({
                'code': code, 'period': 'D',
                'trade_date': {'$lte': trade_date},
            })
            if cnt > 0:
                latest = await col.find_one(
                    {'code': code, 'period': 'D', 'trade_date': {'$lte': trade_date}},
                    sort=[('trade_date', -1)],
                )
                print(f"  {name}({code}): {cnt} 条 ≤ {trade_date}  最新={latest.get('trade_date')}")
            else:
                # 查全局最早日期
                earliest = await col.find_one({'code': code, 'period': 'D'}, sort=[('trade_date', 1)])
                if earliest:
                    print(f"  {name}({code}): ❌ 无 ≤ {trade_date} 的数据  (全局最早={earliest.get('trade_date')})")
                else:
                    print(f"  {name}({code}): ❌ 该指数无任何 OHLCV 数据")
        # 4.2 个股覆盖
        stocks_total = await col.count_documents({
            'period': 'D', 'trade_date': {'$lte': trade_date},
        }, hint=None)  # 粗数
        stocks_lt_21 = 0
        # 抽样：取前 50 只 distinct code，查其 ≤ trade_date 的条数
        sample_codes_50 = distinct_codes[:50]
        for c in sample_codes_50:
            n = await col.count_documents({
                'code': c, 'period': 'D', 'trade_date': {'$lte': trade_date},
            })
            if n < 21:
                stocks_lt_21 += 1
        print(f"  个股 ≤ {trade_date}: 抽样 {len(sample_codes_50)} 只中 {stocks_lt_21} 只 < 21 根")

    await db_module.db_manager.close_connections()


def main():
    ap = argparse.ArgumentParser(description='检查 zstock_ohlcv 数据覆盖')
    ap.add_argument('--trade-date', default=None, help='特定交易日（YYYY-MM-DD）检查覆盖')
    args = ap.parse_args()
    asyncio.run(inspect(args.trade_date))


if __name__ == '__main__':
    main()
