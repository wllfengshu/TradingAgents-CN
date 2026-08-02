"""
真实数据回测入口脚本

两种模式:
  1. 普通回测: 实时计算因子（慢，每日全量拉数据 + 重算因子）
  2. 预计算回测: 从 MongoDB 读预计算因子原始值 → 打分 → 信号（极速）

用法:
    # 普通回测
    python -m zstock.strategy_management.script.run_backtest --start 2026-05-01 --end 2026-06-30

    # 预计算极速回测（需先运行 precompute_factors.py）
    python -m zstock.strategy_management.script.run_backtest --start 2026-05-01 --end 2026-06-30 --precomputed

    # 指定更多参数
    python -m zstock.strategy_management.script.run_backtest \
        --start 2026-01-01 --end 2026-06-30 \
        --capital 2000000 --fee 0.001 --rebalance 5 --precomputed
预计算极速回测的完整流程：
1. 先预计算因子：python -m zstock.factor_management.script.precompute_factors --start 2026-05-01 --end 2026-06-30
2. 再极速回测：python -m zstock.strategy_management.script.run_backtest --start 2026-05-01 --end 2026-06-30 --precomputed

"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 强制根 logger 到 INFO
for h in list(logging.getLogger().handlers):
    logging.getLogger().removeHandler(h)
logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("zstock.data_management").setLevel(logging.INFO)
logging.getLogger("zstock.factor_management").setLevel(logging.INFO)
logging.getLogger("zstock.strategy_management").setLevel(logging.INFO)
logging.getLogger("app").setLevel(logging.WARNING)


async def main() -> int:
    parser = argparse.ArgumentParser(description="真实数据回测")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--lookback", type=int, default=60, help="OHLCV 回看天数（普通模式）")
    parser.add_argument("--capital", type=float, default=1e6, help="初始资金")
    parser.add_argument("--fee", type=float, default=0.0015, help="单边费率")
    parser.add_argument("--rebalance", type=int, default=1, help="再平衡频率（天）")
    parser.add_argument("--precomputed", action="store_true", help="使用预计算因子（极速回测）")
    parser.add_argument("--output", default="output", help="输出目录")
    args = parser.parse_args()

    mode_label = "预计算极速" if args.precomputed else "普通（实时因子）"
    logging.info("=" * 70)
    logging.info(f"🚀 真实数据回测 [{mode_label}]")
    logging.info(f"   区间: {args.start} → {args.end}")
    logging.info(f"   资金: ¥{args.capital:,.0f}  费率: {args.fee:.4%}  再平衡: 每{args.rebalance}天")
    logging.info("=" * 70)

    # ── 1. 初始化数据库 ──
    try:
        from app.core.database import init_database, close_database
        await init_database()
    except Exception as e:
        logging.error(f"❌ 数据库初始化失败: {e}")
        return 1

    try:
        from zstock.data_management.query_service import get_data_query_service
        from zstock.common.utils.common_utils import normalize_date

        qs = get_data_query_service()

        # ── 2. 加载 OHLCV 数据（用于每日收益计算）──
        logging.info("📦 加载 OHLCV 数据...")

        # 获取主板非ST股票列表
        all_stocks_docs, _ = await qs.get_all_stocks()
        mainboard_codes = [
            d["code"] for d in all_stocks_docs
            if d.get("is_mainboard") and not d.get("is_st")
        ]
        logging.info(f"   主板非ST: {len(mainboard_codes)} 只")

        # 分批加载 OHLCV（避免单次 $in 过大）
        ohlcv_data: dict = {}
        chunk_size = 500
        total_chunks = (len(mainboard_codes) + chunk_size - 1) // chunk_size
        failed_chunks = 0

        for ci, i in enumerate(range(0, len(mainboard_codes), chunk_size), 1):
            chunk = mainboard_codes[i : i + chunk_size]
            logging.info(f"   OHLCV [{ci}/{total_chunks}] {len(chunk)} 只...")
            try:
                batch = await qs.get_ohlcv_batch(chunk, args.start, args.end)
                if batch:
                    ohlcv_data.update(batch)
            except Exception as e:
                logging.warning(f"   ⚠️ 批次 {ci} 加载失败（跳过）: {e}")
                failed_chunks += 1
                continue

        # 确保 trade_date 格式统一为 YYYY-MM-DD（与 Backtester 一致）
        for code, df in ohlcv_data.items():
            if "trade_date" in df.columns:
                df["trade_date"] = df["trade_date"].apply(normalize_date)

        logging.info(
            f"✅ OHLCV 加载完成: {len(ohlcv_data)} 只 "
            f"(失败批次: {failed_chunks}/{total_chunks})"
        )

        if not ohlcv_data:
            logging.error("❌ 无 OHLCV 数据，无法回测")
            return 1

        # ── 3. 创建 ohlcv_provider ──
        from zstock.strategy_management.script.backtester import (
            Backtester,
            make_ohlcv_provider_from_dict,
        )

        ohlcv_provider = make_ohlcv_provider_from_dict(ohlcv_data)

        # ── 4. 创建 Backtester ──
        if args.precomputed:
            from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
            factor_pipeline = CrossSectionStrategyPipeline()
            bt = Backtester(
                fee_rate=args.fee,
                initial_capital=args.capital,
                factor_pipeline=factor_pipeline,
            )
        else:
            bt = Backtester(
                fee_rate=args.fee,
                initial_capital=args.capital,
            )

        # ── 5. 运行回测 ──
        logging.info("🚀 回测开始...")
        result = await bt.run(
            start_date=args.start,
            end_date=args.end,
            ohlcv_provider=ohlcv_provider,
            rebalance_freq=args.rebalance,
            use_precomputed_factors=args.precomputed,
            verbose=True,
        )

        # ── 6. 输出结果 ──
        output_dir = Path(__file__).resolve().parent / args.output
        output_dir.mkdir(parents=True, exist_ok=True)

        # 打印摘要
        print(result.summary())

        # 保存图表
        mode_tag = "precomputed" if args.precomputed else "realtime"
        chart_path = result.plot(
            output_path=str(output_dir / f"backtest_{mode_tag}.png"),
            title=f"真实数据回测 [{mode_label}] {args.start}~{args.end}",
        )

        # 导出 CSV
        csv_paths = result.export_csv(str(output_dir))

        logging.info(f"📊 图表已保存: {chart_path}")
        logging.info(f"📁 CSV 已导出: {csv_paths}")

        return 0

    except Exception as e:
        logging.error(f"❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        try:
            await close_database()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
