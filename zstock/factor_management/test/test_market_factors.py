"""
连接 mongodb，测试 market_factors 类。打印详细日志，用于验证代码是否 ok

注意：本系统指数 OHLCV 与个股 OHLCV 同存于 zstock_ohlcv 集合，按 6 位 code 区分。
沪深300 在库里存的 code 是 399300（深交所发布代码），不是 000300（000300 库里 0 条）；
000001 被平安银行占用（与上证指数 code 撞了，上证指数未落库）。
故 MarketFactors 主锚用 399300。
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.factor_management import MarketFactors

# 待测指数：(code, name, start_date, end_date)
# 沪深300=399300（主锚，库里 2026-06-01~2026-06-30）；北证50=899050（验证通用性）
TEST_INDICES = [
    ('399300', '沪深300', '20260601', '20260630'),
    ('899050', '北证50', '20260601', '20260630'),
]


async def main() -> bool:
    print("\n" + "=" * 70)
    print("✅ MarketFactors 集成测试（MongoDB）")
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

    for idx, (index_code, index_name, start_date, end_date) in enumerate(TEST_INDICES, 1):
        print(f"\n{'─' * 70}")
        print(f"📌 指数 {idx}: {index_name} (code={index_code})  {start_date} ~ {end_date}")
        print(f"{'─' * 70}")

        # 3. 查询指数 OHLCV
        print(f"📌 步骤 3: 查询指数 OHLCV...")
        try:
            df, source = await query_service.get_ohlcv(
                index_code, start_date, end_date, period='daily'
            )
        except ValueError as e:
            print(f"⚠️ 指数 {index_code} 无数据：{e}")
            continue
        print(f"✅ 返回 {len(df)} 根日线（来源: {source}）")
        print(f"ohlcv数据：{df}")
        if len(df) == 0:
            print("⚠️ 空数据，跳过")
            continue
        print(f"   最新一行: trade_date={df.iloc[-1].get('trade_date')} "
              f"close={df.iloc[-1].get('close')} volume={df.iloc[-1].get('volume')}")
        print(f"   字段: {list(df.columns)}")

        # 4. 测试 MarketFactors.calculate_market_sentiment
        print(f"\n📌 步骤 4: 测试 MarketFactors.calculate_market_sentiment...")
        sentiment = MarketFactors.calculate_market_sentiment(df, index_name=index_name)

        print(f"   市场得分  : {sentiment['market_score']:.2f}")
        print(f"   市场等级  : {sentiment['market_grade']}")
        print(f"   仓位缩放  : {sentiment['position_scale']}")
        print(f"   允许开新仓: {sentiment['allow_new_open']}")
        print(f"   子因子拆分:")
        print(f"     MF1 趋势强度  = {sentiment['mf1_trend']:.1f}")
        print(f"     MF2 布林带位置 = {sentiment['mf2_boll']:.1f}")
        print(f"     MF3 量能状态  = {sentiment['mf3_volume']:.1f}")
        print(f"     MF4 近期动量  = {sentiment['mf4_momentum']:.1f}")
        print(f"     MF5 波动率压制= {sentiment['mf5_volatility']:.1f}")
        detail = sentiment.get('detail', {})
        print(f"   原始值: {detail}")

        # 5. 范围校验
        assert 0 <= sentiment['market_score'] <= 100, "market_score 应在 0-100"
        assert sentiment['market_grade'] in ('green', 'yellow', 'red'), "grade 非法"
        assert sentiment['position_scale'] in (1.0, 0.5, 0.0), "position_scale 非法"
        for k in ('mf1_trend', 'mf2_boll', 'mf3_volume', 'mf4_momentum', 'mf5_volatility'):
            v = sentiment[k]
            assert 0 <= v <= 100, f"{k} 应在 0-100，实际 {v}"
        print(f"   ✅ 得分/子因子范围正确 [0, 100]")

        # 6. 业务正确性校验：grade / scale / score / allow_new_open 一致性
        grade = sentiment['market_grade']
        score = sentiment['market_score']
        scale = sentiment['position_scale']
        allow = sentiment['allow_new_open']
        if grade == 'green':
            assert score >= 70 and scale == 1.0 and allow is True, "green 校验失败"
            print(f"   ✅ green: score>=70, scale=1.0, 允许开仓")
        elif grade == 'yellow':
            assert 40 <= score < 70 and scale == 0.5 and allow is True, "yellow 校验失败"
            print(f"   ✅ yellow: 40<=score<70, scale=0.5, 允许开仓(减半)")
        else:  # red
            assert score < 40 and scale == 0.0 and allow is False, "red 校验失败"
            print(f"   ✅ red: score<40, scale=0.0, 禁止开新仓")

        # 7. 业务正确性校验：score 与加权合成一致（容差 0.5）
        import math
        expected = (
            0.30 * sentiment['mf1_trend']
            + 0.25 * sentiment['mf2_boll']
            + 0.20 * sentiment['mf3_volume']
            + 0.15 * sentiment['mf4_momentum']
            + 0.10 * sentiment['mf5_volatility']
        )
        expected = max(0.0, min(100.0, expected))
        assert abs(expected - score) < 0.5, f"加权合成不一致: 期望 {expected:.2f} 实际 {score:.2f}"
        print(f"   ✅ 加权合成一致: 预期≈{expected:.2f} 实际={score:.2f}")

        # 8. 子因子主导性观察（仅打印，不强制断言）
        factors = {
            'MF1趋势': sentiment['mf1_trend'],
            'MF2布林': sentiment['mf2_boll'],
            'MF3量能': sentiment['mf3_volume'],
            'MF4动量': sentiment['mf4_momentum'],
            'MF5波动': sentiment['mf5_volatility'],
        }
        top_factor = max(factors, key=factors.get)
        low_factor = min(factors, key=factors.get)
        print(f"   ℹ️ 最强子因子: {top_factor}={factors[top_factor]:.1f}  "
              f"最弱子因子: {low_factor}={factors[low_factor]:.1f}")

    # 9. 边界测试：数据不足（< 21 根）应返回 yellow 中性结果
    print(f"\n{'─' * 70}")
    print(f"📌 步骤 9: 边界测试 — 数据不足应返回中性(yellow)")
    print(f"{'─' * 70}")
    index_code, index_name, _, _ = TEST_INDICES[0]
    df_full, _ = await query_service.get_ohlcv(
        index_code, TEST_INDICES[0][2], TEST_INDICES[0][3], period='daily'
    )
    short_df = df_full.head(10)  # 仅 10 根 < _MIN_BARS(21)
    print(f"   截取 {len(short_df)} 根日线（< 21 阈值）")
    neutral = MarketFactors.calculate_market_sentiment(short_df, index_name=index_name)
    print(f"   得分={neutral['market_score']} 等级={neutral['market_grade']} "
          f"scale={neutral['position_scale']} allow_new_open={neutral['allow_new_open']}")
    assert neutral['market_grade'] == 'yellow', "数据不足应返回 yellow"
    assert neutral['market_score'] == 50.0, "数据不足应返回中性分 50"
    assert neutral['position_scale'] == 0.5, "中性结果仓位缩放应为 0.5"
    assert neutral['allow_new_open'] is True, "中性结果应允许开仓"
    assert neutral.get('detail', {}).get('reason'), "中性结果应带 reason"
    print(f"   ✅ 数据不足正确返回中性评估 (reason={neutral['detail'].get('reason')})")

    # 10. 边界测试：缺少列应返回中性
    print(f"\n📌 步骤 10: 边界测试 — 缺少列应返回中性")
    bad_df = df_full.drop(columns=['volume']).head(25)
    neutral2 = MarketFactors.calculate_market_sentiment(bad_df, index_name=index_name)
    assert neutral2['market_grade'] == 'yellow' and neutral2['market_score'] == 50.0, "缺列应返回中性"
    print(f"   ✅ 缺少 volume 列正确返回中性 (reason={neutral2['detail'].get('reason')})")

    print("\n✅ MarketFactors 测试完成\n")

    # 清理
    print("📌 清理 ...")
    await db_module.db_manager.close_connections()
    print("✅ 已断开\n")

    return True


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
