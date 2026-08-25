"""
市场风格检测 CLI

遍历指定日期范围，逐日检测沪深300指数的市场风格（动量/反转/中性），
输出 CSV 报告和可选可视化。

用法：
  # 检测 2025-2026 年市场风格
  python -m zstock.factor_management.script.run_style_detector \
      --start 2025-01-01 --end 2026-07-01

  # 包含可视化
  python -m zstock.factor_management.script.run_style_detector \
      --start 2025-01-01 --end 2026-07-01 --plot

输出：
  因子测评/output/<timestamp>/style_detection.csv
  因子测评/output/<timestamp>/style_detection.png（可选 --plot）
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 默认指数代码
_DEFAULT_INDEX = "399300"  # 沪深300

# 风格颜色映射
_REGIME_COLORS = {
    "momentum": "#e74c3c",   # 红色：动量
    "reversal": "#2ecc71",   # 绿色：反转
    "neutral": "#95a5a6",    # 灰色：中性
}


def _get_trading_dates(start: str, end: str, df: pd.DataFrame) -> List[str]:
    """从指数数据中提取 start-end 范围内的交易日"""
    if "trade_date" not in df.columns:
        return []
    dates = sorted(df["trade_date"].astype(str).unique())
    return [d for d in dates if start <= d <= end]


async def _run_detection(
    start_date: str,
    end_date: str,
    index_code: str = _DEFAULT_INDEX,
) -> Tuple[pd.DataFrame, List[dict]]:
    """逐日运行风格检测"""
    from zstock.data_management.query_service import get_data_query_service
    from zstock.factor_management.style_detector import StyleDetector

    qs = get_data_query_service()

    # 加载全部指数数据（一次性加载，性能更好）
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    load_start = (start_dt - timedelta(days=120)).strftime("%Y-%m-%d")

    logger.info(f"加载指数 {index_code} 数据: {load_start} ~ {end_date}")
    df, source = await qs.get_ohlcv(index_code, load_start, end_date, period="daily")
    logger.info(f"  数据源: {source}, 共 {len(df)} 条")

    trading_dates = _get_trading_dates(start_date, end_date, df)
    logger.info(f"交易日范围: {start_date} ~ {end_date}, 共 {len(trading_dates)} 个交易日")

    detector = StyleDetector()
    results = []

    for i, td in enumerate(trading_dates):
        result = detector.detect(df, td)
        result["trade_date"] = td
        results.append(result)

        if (i + 1) % 50 == 0 or i == 0 or i == len(trading_dates) - 1:
            logger.info(
                f"  [{i+1}/{len(trading_dates)}] {td}: "
                f"regime={result['regime']}, "
                f"autocorr={result['autocorr']:.4f}, "
                f"strength={result['strength']:.2f}"
            )

    return df, results


def _save_results(results: List[dict], output_dir: Path) -> Path:
    """保存检测结果为 CSV"""
    rows = []
    for r in results:
        rows.append({
            "trade_date": r["trade_date"],
            "regime": r["regime"],
            "autocorr": round(r["autocorr"], 6) if np.isfinite(r["autocorr"]) else "",
            "strength": round(r["strength"], 4),
            "momentum_weight": round(r["momentum_weight"], 4),
            "reversal_weight": round(r["reversal_weight"], 4),
        })

    df = pd.DataFrame(rows)
    csv_path = output_dir / "style_detection.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info(f"✅ 风格检测结果已保存: {csv_path}")

    # 统计摘要
    regime_counts = df["regime"].value_counts()
    total = len(df)
    logger.info("=" * 60)
    logger.info("风格统计摘要:")
    for regime in ["momentum", "reversal", "neutral"]:
        count = regime_counts.get(regime, 0)
        pct = count / total * 100 if total > 0 else 0
        logger.info(f"  {regime}: {count} 天 ({pct:.1f}%)")
    logger.info("=" * 60)

    return csv_path


def _plot_results(results: List[dict], output_dir: Path) -> None:
    """绘制风格检测可视化"""
    dates = [r["trade_date"] for r in results]
    autocorrs = [r["autocorr"] for r in results]
    regimes = [r["regime"] for r in results]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), sharex=True)

    # 上图：自相关系数走势
    colors = [_REGIME_COLORS.get(r, "#95a5a6") for r in regimes]
    ax1.bar(range(len(dates)), autocorrs, color=colors, width=1.0, alpha=0.8)
    ax1.axhline(y=0.05, color="#e74c3c", linestyle="--", alpha=0.5, label="momentum (+0.05)")
    ax1.axhline(y=-0.05, color="#2ecc71", linestyle="--", alpha=0.5, label="reversal (-0.05)")
    ax1.axhline(y=0, color="black", linestyle="-", alpha=0.3)
    ax1.set_ylabel("Rank Autocorrelation")
    ax1.set_title("Market Style Detection: Rank Autocorrelation (20-day rolling)")
    ax1.legend(loc="upper right")
    ax1.grid(alpha=0.3)

    # 下图：风格分类堆叠
    regime_map = {"momentum": 1, "neutral": 0, "reversal": -1}
    regime_vals = [regime_map.get(r, 0) for r in regimes]
    regime_colors = [_REGIME_COLORS.get(r, "#95a5a6") for r in regimes]
    ax2.fill_between(range(len(dates)), 0, regime_vals, color="#e74c3c", alpha=0.3)
    for i, (rv, c) in enumerate(zip(regime_vals, regime_colors)):
        ax2.bar(i, rv, color=c, width=1.0, alpha=0.8)
    ax2.set_ylabel("Regime (1=momentum, -1=reversal)")
    ax2.set_xlabel("Trading Days")
    ax2.set_yticks([-1, 0, 1])
    ax2.set_yticklabels(["Reversal", "Neutral", "Momentum"])
    ax2.grid(alpha=0.3)

    # x轴标签（每 30 个交易日标一个）
    tick_step = max(1, len(dates) // 15)
    tick_positions = list(range(0, len(dates), tick_step))
    tick_labels = [dates[i] for i in tick_positions]
    ax2.set_xticks(tick_positions)
    ax2.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)

    plt.tight_layout()
    plot_path = output_dir / "style_detection.png"
    fig.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"✅ 风格检测图表已保存: {plot_path}")


async def main():
    parser = argparse.ArgumentParser(description="市场风格检测 CLI")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--index", default=_DEFAULT_INDEX, help=f"指数代码（默认 {_DEFAULT_INDEX}）")
    parser.add_argument("--plot", action="store_true", help="生成可视化图表")
    args = parser.parse_args()

    # 初始化 MongoDB
    try:
        from app.core import database as db_module
        await db_module.db_manager.init_mongodb()
    except Exception as e:
        logging.error(f"数据库初始化失败: {e}")
        return 1

    # 输出目录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).resolve().parent / "output" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"市场风格检测: {args.start} ~ {args.end}")
    logger.info(f"指数: {args.index}")
    logger.info(f"输出目录: {output_dir}")

    df, results = await _run_detection(args.start, args.end, args.index)

    if not results:
        logger.error("无检测结果，退出")
        return

    _save_results(results, output_dir)

    if args.plot:
        _plot_results(results, output_dir)

    logger.info("✅ 风格检测完成")


if __name__ == "__main__":
    asyncio.run(main())