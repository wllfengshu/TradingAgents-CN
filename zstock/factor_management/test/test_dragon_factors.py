"""
连接mongodb，测试dragon_factors类。打印详细日志，用于验证代码是否ok
"""


import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.factor_management import DragonFactors


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

    # todo test DragonFactors
    print("📌 步骤 3: 查询板块列表（已含成分股）...")
    sectors, _ = await query_service.get_sector_list()
    # 说明：i == 4这是取下标为4的，只取一个方便测试。sectors[:5]是取前5个
    test_sectors = [s for i, s in enumerate(sectors) if i == 4 and s.get('sector_code') and s.get('stocks')]
    # test_sectors = [s for s in sectors[:5] if s.get('sector_code') and s.get('stocks')]
    sector_codes = [s['sector_code'] for s in test_sectors]
    all_stock_codes = list(set(
        code for s in test_sectors for code in s.get('stocks', [])
    ))
    print(f"✅ 选取 {len(test_sectors)} 个板块: {sector_codes}")
    print(f"✅ 共 {len(all_stock_codes)} 只成分股\n")

    print("📌 步骤 4: 查询个股 OHLCV...")
    # 固定回测日期：2026-06-01 至 2026-06-30（注意 start <= end）
    start_date = '20260601'
    end_date = '20260630'
    stock_ohlcv = await query_service.get_ohlcv_batch(
        all_stock_codes, start_date, end_date, period='daily'
    )
    print(f"✅ 返回 {len(stock_ohlcv)} 只股票的 OHLCV\n")
    print(f"   OHLCV 数据: {stock_ohlcv}")

    print("📌 步骤 5: 测试 DragonFactors.calculate_all_dragon_factors_in_sector...")
    for sector in test_sectors:
        sector_code = sector['sector_code']
        sector_stocks = sector.get('stocks', [])
        if not sector_stocks:
            continue

        print(f"\n  ── 板块 {sector_code} ({sector.get('sector_name', '')}) ──")
        print(f"     成分股: {len(sector_stocks)} 只")
        print(f"     成分股列表: {sector_stocks}")

        # DragonFactors 内部已自动过滤无 OHLCV 数据的股票，无需测试层干预
        scores = DragonFactors.calculate_all_dragon_factors_in_sector(
            sector_stocks, stock_ohlcv
        )

        print(f"     计算结果: {len(scores)} 只")
        if not scores:
            print(f"     ⚠️ 无得分（OHLCV 数据不足）")
            continue

        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        print(f"     TOP3: {sorted_scores[:3]}")
        print(f"     平均分: {sum(scores.values()) / len(scores):.2f}")
        assert all(0 <= v <= 100 for v in scores.values()), "得分应在 0-100 之间"
        assert len(scores) <= len(sector_stocks), "结果不应超过输入股票数量"

        # ── 业务正确性校验：因子拆分可见 ──
        stock_5d_returns = DragonFactors._compute_5d_returns(stock_ohlcv, sector_stocks)
        stock_daily_volumes = DragonFactors._compute_daily_volumes(stock_ohlcv, sector_stocks)
        stock_consecutive_boards = DragonFactors._compute_consecutive_boards(stock_ohlcv, sector_stocks)
        sector_returns = {s: stock_5d_returns[s] for s in sector_stocks if s in stock_5d_returns}
        sector_volumes = {s: stock_daily_volumes[s] for s in sector_stocks if s in stock_daily_volumes}
        sector_boards = {s: stock_consecutive_boards[s] for s in sector_stocks if s in stock_consecutive_boards}
        f31_raw = DragonFactors._calculate_leading_performance_raw(sector_returns)
        f32_raw = DragonFactors._calculate_popularity_raw(sector_volumes)
        f33_raw = DragonFactors._calculate_height_raw(sector_boards)
        f34_raw = DragonFactors._calculate_volume_price_resonance_raw(sector_stocks, stock_ohlcv)
        f31_norm = DragonFactors._minmax_normalize(f31_raw)
        f32_norm = DragonFactors._minmax_normalize(f32_raw)
        f33_norm = DragonFactors._minmax_normalize(f33_raw)
        f34_norm = DragonFactors._minmax_normalize(f34_raw)

        # 打印 TOP5 因子拆分，验证 F3.3 权重主导
        print(f"     TOP5 因子拆分 (F3.1=超额 F3.2=人气 F3.3=高度 F3.4=共振):")
        for code, score in sorted_scores[:5]:
            f1 = f31_norm.get(code)
            f2 = f32_norm.get(code)
            f3 = f33_norm.get(code)
            f4 = f34_norm.get(code)
            f1_s = f"{f1:.0f}" if f1 is not None else "N/A"
            f2_s = f"{f2:.0f}" if f2 is not None else "N/A"
            f3_s = f"{f3:.0f}" if f3 is not None else "N/A"
            f4_s = f"{f4:.0f}" if f4 is not None else "N/A"
            print(f"       {code}: total={score:.1f}  F3.1={f1_s}  "
                  f"F3.2={f2_s}  F3.3={f3_s}  F3.4={f4_s}")

        # 业务校验 1：真正连板天数 > 0 的股票才计入连板票
        boards_codes = {s for s, days in sector_boards.items() if days > 0}
        if boards_codes:
            top3_codes = {c for c, _ in sorted_scores[:3]}
            boards_in_top3 = boards_codes & top3_codes
            print(f"     连板票: {len(boards_codes)} 只，TOP3 中: {len(boards_in_top3)} 只")
            if boards_in_top3:
                print(f"     ✅ 连板票出现在 TOP3，F3.3 权重逻辑正确")
            else:
                print(f"     ⚠️ 连板票未出现在 TOP3（需检查数据或权重）")
        else:
            print(f"     ℹ️ 该板块无连板票")

        # 业务校验 2：TOP1 的 F3.3 应在所有因子中最高或次高
        top1_code = sorted_scores[0][0]
        top1_factors = {
            'F3.1': f31_norm.get(top1_code, 0),
            'F3.2': f32_norm.get(top1_code, 0),
            'F3.3': f33_norm.get(top1_code, 0),
            'F3.4': f34_norm.get(top1_code, 0),
        }
        top1_max_factor = max(top1_factors, key=top1_factors.get)
        print(f"     TOP1 {top1_code} 最强因子: {top1_max_factor}={top1_factors[top1_max_factor]:.0f}")
        if top1_max_factor == 'F3.3':
            print(f"     ✅ TOP1 由 F3.3（连板高度）主导，符合权重设计")
        else:
            print(f"     ℹ️ TOP1 由 {top1_max_factor} 主导（非连板驱动行情）")

        print(f"     ✅ 得分范围正确 [0, 100]")

    print("\n✅ DragonFactors 测试完成\n")

    # 清理
    print("\n📌 清理 ...")
    await db_module.db_manager.close_connections()
    print("✅ 已断开\n")

    return True


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
