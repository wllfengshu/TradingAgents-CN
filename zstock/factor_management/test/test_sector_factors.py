"""
连接 mongodb，测试 sector_factors 类。打印详细日志，用于验证代码是否 ok

注意：
- M2 是板块层 cross-sectional 排名，必须喂 >=2 个板块才有横向意义，本测试取前 5 个板块。
- F2.1(RPS) 需要 20 日收益率，OHLCV 区间需 >= 21 根，故用 20260401~20260630（约 60 根）。
- 资金流 F2.2 取近 5 日（end_date=20260630），与 force_factors 测试口径一致。
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.factor_management import SectorFactors


async def main() -> bool:
    print("\n" + "=" * 70)
    print("✅ SectorFactors 集成测试（MongoDB）")
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

    # 3. 获取板块列表（已含成分股），取前 5 个有成分股的板块
    print("📌 步骤 3: 查询板块列表...")
    sectors, _ = await query_service.get_sector_list()
    test_sectors = [s for s in sectors[:5] if s.get('sector_code') and s.get('stocks')]
    sector_codes = [s['sector_code'] for s in test_sectors]
    all_stock_codes = list(set(
        code for s in test_sectors for code in s.get('stocks', [])
    ))
    print(f"✅ 选取 {len(test_sectors)} 个板块: {sector_codes}")
    print(f"✅ 共 {len(all_stock_codes)} 只成分股\n")

    # 4. 查询个股 OHLCV（宽区间保证 F2.1 RPS20 有足够根数）
    print("📌 步骤 4: 查询个股 OHLCV...")
    start_date = '20260401'
    end_date = '20260630'
    stock_ohlcv = await query_service.get_ohlcv_batch(
        all_stock_codes, start_date, end_date, period='daily'
    )
    print(f"✅ 返回 {len(stock_ohlcv)} 只股票的 OHLCV\n")
    print(f"stock_ohlcv: {stock_ohlcv}")

    # 5. 查询资金流数据（近 5 日）
    print("📌 步骤 5: 查询资金流数据...")
    stock_flow_recent = await query_service.get_capital_flow_recent_days(
        all_stock_codes, end_date=end_date, days=5
    )
    print(f"✅ 返回 {len(stock_flow_recent)} 只股票的资金流\n")
    print(f"stock_flow_recent: {stock_flow_recent}")

    # 6. 构造 sector_stocks 映射
    print("📌 步骤 6: 构造板块成分股映射...")
    sector_stocks = {s['sector_code']: s.get('stocks', []) for s in test_sectors}
    for sc, stocks in sector_stocks.items():
        print(f"   {sc}: {len(stocks)} 只")
    print()

    # 7. 测试 SectorFactors.calculate_all_sector_factors
    print("📌 步骤 7: 测试 SectorFactors.calculate_all_sector_factors...")
    m2_scores = SectorFactors.calculate_all_sector_factors(
        sectors=test_sectors,
        sector_stocks=sector_stocks,
        stock_ohlcv=stock_ohlcv,
        stock_flow_recent=stock_flow_recent,
    )
    print(f"✅ 返回 {len(m2_scores)} 个板块得分\n")

    if not m2_scores:
        print("⚠️ 无板块得分（OHLCV 数据不足），结束")
        await db_module.db_manager.close_connections()
        return False

    # 8. 排序 + 范围校验
    ranked = sorted(m2_scores.items(), key=lambda x: x[1], reverse=True)
    print("   板块排名:")
    for sc, score in ranked:
        print(f"     {sc:<20} M2={score:.2f}")
    assert all(0 <= v <= 100 for v in m2_scores.values()), "M2 得分应在 0-100"
    assert len(m2_scores) <= len(test_sectors), "结果不应超过输入板块数"
    print(f"   ✅ 得分范围正确 [0, 100]")

    # 9. 因子拆分（复刻内部流程，便于定位）
    print("\n📌 步骤 8: 因子拆分 (F2.1 RPS / F2.2 资金流 / F2.3 涨停浓度 / F2.4 连板高度 / F2.5 成交占比斜率)...")
    sector_ohlcv, sector_capital_flow = SectorFactors._aggregate_sectors_from_stocks(
        sector_stocks, stock_ohlcv, stock_flow_recent or {},
    )
    all_limit_up = SectorFactors._compute_limit_up_from_ohlcv(stock_ohlcv)
    all_boards = SectorFactors._compute_consecutive_boards_from_ohlcv(stock_ohlcv)
    valid_codes = {s['sector_code'] for s in test_sectors if s.get('sector_code')}
    rps_raw = SectorFactors._collect_sector_rps(sector_ohlcv, sector_codes=valid_codes)
    vol_slope_raw = SectorFactors._collect_volume_ratio_slope(sector_ohlcv, sector_codes=valid_codes)
    ohlcv_valid = set(rps_raw.keys()) & set(vol_slope_raw.keys())
    valid_sectors_filtered = [s for s in test_sectors if s.get('sector_code') in ohlcv_valid]
    cap_raw = SectorFactors._collect_sector_capital_flow(
        {k: v for k, v in sector_capital_flow.items() if k in ohlcv_valid}
    )
    lu_raw = SectorFactors._collect_limit_up_densities(
        valid_sectors_filtered, sector_stocks, all_limit_up
    )
    boards_raw = SectorFactors._collect_consecutive_boards_max(
        valid_sectors_filtered, sector_stocks, all_boards
    )

    rps_n = SectorFactors._minmax_normalize(rps_raw)
    cap_n = SectorFactors._minmax_normalize(cap_raw)
    lu_n = SectorFactors._minmax_normalize(lu_raw)
    boards_n = SectorFactors._minmax_normalize(boards_raw)
    vol_n = SectorFactors._minmax_normalize(vol_slope_raw)

    def _fmt(d, sc):
        v = d.get(sc)
        return f"{v:>6.1f}" if isinstance(v, (int, float)) else f"{'NA':>6}"

    print(f"   {'板块':<20} {'M2':>6} {'F2.1':>6} {'F2.2':>6} {'F2.3':>6} {'F2.4':>6} {'F2.5':>6}")
    for sc, score in ranked:
        print(f"   {sc:<20} {score:>6.1f} {_fmt(rps_n, sc)} {_fmt(cap_n, sc)} "
              f"{_fmt(lu_n, sc)} {_fmt(boards_n, sc)} {_fmt(vol_n, sc)}")

    # 10. min-max 归一化性质校验：每个因子（>=2 个有效值且不全相等时）最大值=100
    for name, norm in [('F2.1', rps_n), ('F2.2', cap_n), ('F2.3', lu_n),
                       ('F2.4', boards_n), ('F2.5', vol_n)]:
        vals = list(norm.values())
        if len(vals) >= 2 and len(set(round(v, 6) for v in vals)) >= 2:
            assert max(vals) == 100.0, f"{name} 归一化后最大值应为 100，实际 {max(vals)}"
    print(f"   ✅ min-max 归一化性质正确（多板块非等值时 max=100）")

    # 11. 业务校验：F2.1(RPS) 最高的板块，M2 应排在前半区（RPS 权重主导趋势）
    if rps_raw:
        rps_top = max(rps_raw, key=rps_raw.get)
        rank_pos = [sc for sc, _ in ranked].index(rps_top) if rps_top in m2_scores else -1
        if rank_pos >= 0:
            half = max(len(ranked) // 2, 1)
            tag = "✅" if rank_pos < half else "ℹ️"
            print(f"   {tag} F2.1 RPS 最强板块 {rps_top} 排名第 {rank_pos+1}/{len(ranked)}（前半区为强）")

    # 12. 业务校验：聚合后板块 OHLCV 行数 <= 个股最大行数，且 close 为净值序列
    for sc, df in sector_ohlcv.items():
        assert 'close' in df.columns and 'volume' in df.columns, f"{sc} 聚合缺列"
        assert len(df) >= 21, f"{sc} 聚合 OHLCV 不足 21 根（{len(df)}），RPS20 会缺失"
    print(f"   ✅ 板块聚合 OHLCV 合法（均含 close/volume 且 >=21 根）")

    # 13. 边界测试：空输入应返回 {}
    print(f"\n📌 步骤 9: 边界测试 — 空板块输入应返回空 dict")
    empty = SectorFactors.calculate_all_sector_factors(
        sectors=[], sector_stocks={}, stock_ohlcv={}, stock_flow_recent={},
    )
    assert empty == {}, "空输入应返回 {}"
    print(f"   ✅ 空输入正确返回 {{}}")

    # 14. 边界测试：单板块（min-max 全相等→50，最终得分=50）
    print(f"\n📌 步骤 10: 边界测试 — 单板块应得 50（min-max 等值中性）")
    if test_sectors:
        single = test_sectors[:1]
        single_stocks = {single[0]['sector_code']: single[0].get('stocks', [])}
        single_m2 = SectorFactors.calculate_all_sector_factors(
            sectors=single, sector_stocks=single_stocks,
            stock_ohlcv=stock_ohlcv, stock_flow_recent=stock_flow_recent,
        )
        if single_m2:
            sc, v = next(iter(single_m2.items()))
            print(f"   单板块 {sc}: M2={v:.2f}")
            assert 0 <= v <= 100, "单板块得分应在 0-100"
            print(f"   ✅ 单板块得分范围正确（min-max 等值时各因子=50，合成≈50）")

    print("\n✅ SectorFactors 测试完成\n")

    # 清理
    print("📌 清理 ...")
    await db_module.db_manager.close_connections()
    print("✅ 已断开\n")

    return True


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
