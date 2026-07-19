"""
连接mongodb，测试force_factors类。打印详细日志，用于验证代码是否ok
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.factor_management import ForceFactors, DragonFactors


async def main() -> bool:
    print("\n" + "=" * 70)
    print("✅ ForceFactors 集成测试（MongoDB）")
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

    # 3. 获取板块列表（已含成分股）
    print("📌 步骤 3: 查询板块列表...")
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
    print(f"   板块详情: {all_stock_codes}")

    # 4. 查询个股 OHLCV
    print("📌 步骤 4: 查询个股 OHLCV...")
    end_date = '20260630'
    start_date = '20260601'
    stock_ohlcv = await query_service.get_ohlcv_batch(
        all_stock_codes, start_date, end_date, period='daily'
    )
    print(f"✅ 返回 {len(stock_ohlcv)} 只股票的 OHLCV\n")
    print(f"   OHLCV 数据: {stock_ohlcv}")

    # 5. 计算 DragonFactors 龙头分
    print("📌 步骤 5: 计算 DragonFactors 龙头分...")
    sector_dragon_scores = {}
    for sector in test_sectors:
        sector_code = sector['sector_code']
        sector_stocks = sector.get('stocks', [])
        scores = DragonFactors.calculate_all_dragon_factors_in_sector(
            sector_stocks, stock_ohlcv
        )
        sector_dragon_scores[sector_code] = scores
        print(f"   板块 {sector_code}: {len(scores)} 只有龙头分")
    print()

    # 6. 构造 candidates（含 code, sector_code, dragon_score）
    print("📌 步骤 6: 构造候选集...")
    candidates = []
    for sector in test_sectors:
        sector_code = sector['sector_code']
        for stock_code in sector.get('stocks', []):
            dragon_score = sector_dragon_scores.get(sector_code, {}).get(stock_code, 0)
            candidates.append({
                'code': stock_code,
                'sector_code': sector_code,
                'dragon_score': dragon_score,
            })
    # 取前3个候选用于测试
    candidates = candidates[:3]
    print(f"✅ 候选集: {len(candidates)} 只\n")
    print(f"   候选详情: {candidates}")

    # 7. 查询资金流数据
    print("📌 步骤 7: 查询资金流数据...")
    stock_flow_recent = await query_service.get_capital_flow_recent_days(
        all_stock_codes, end_date=end_date, days=5
    )
    print(f"✅ 返回 {len(stock_flow_recent)} 只股票的资金流\n")
    print(f"   资金流数据: {stock_flow_recent}")

    # 8. 构造 top_sectors（板块排名）
    print("📌 步骤 8: 构造板块排名...")
    # 用板块内平均龙头分作为板块排名分
    top_sectors = []
    for sector in test_sectors:
        sector_code = sector['sector_code']
        scores = sector_dragon_scores.get(sector_code, {})
        avg_score = sum(scores.values()) / len(scores) if scores else 0
        top_sectors.append((sector_code, avg_score))
    # 按龙头分降序排列
    top_sectors.sort(key=lambda x: x[1], reverse=True)
    print(f"✅ 板块排名: {top_sectors}\n")

    # 9. 测试 ForceFactors.apply_cooperative_force_and_score
    print("📌 步骤 9: 测试 ForceFactors.apply_cooperative_force_and_score...")
    ranked = ForceFactors.apply_cooperative_force_and_score(
        candidates=candidates,
        top_sectors=top_sectors,
        stock_flow_recent=stock_flow_recent,
        stock_ohlcv=stock_ohlcv,
    )

    print(f"✅ M4+M5 完成: {len(ranked)} 只候选通过\n")

    if ranked:
        print("     TOP5 最终候选:")
        for c in ranked[:5]:
            print(f"       {c['code']} ({c['sector_code']}): "
                  f"final_score={c['final_score']:.2f}  "
                  f"dragon_score={c['dragon_score']:.2f}  "
                  f"coop_score={c.get('coop_score', 0):.2f}  "
                  f"main_net_ratio={c.get('main_net_ratio', 0):.4f}")

        # 校验：所有 final_score 在 0-100 之间
        assert all(0 <= c['final_score'] <= 100 for c in ranked), "final_score 应在 0-100 之间"
        print("     ✅ 最终得分范围正确 [0, 100]")

        # 校验：M4 过滤后保留的候选应有 dragon_score >= 0
        assert all(c['dragon_score'] >= 0 for c in ranked), "dragon_score 不应为负"
        print("     ✅ 龙头分非负")

        # 校验：coop_score 在 0-100 之间
        assert all(0 <= c.get('coop_score', 0) <= 100 for c in ranked), "coop_score 应在 0-100 之间"
        print("     ✅ 合力综合分范围正确 [0, 100]")
    else:
        print("     ⚠️ 无候选通过 M4 合力过滤（资金流/成交额不足）")

    print("\n✅ ForceFactors 测试完成\n")

    # 清理
    print("📌 清理 ...")
    await db_module.db_manager.close_connections()
    print("✅ 已断开\n")

    return True


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
