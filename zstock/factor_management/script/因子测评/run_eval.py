"""
因子测评 CLI

用法：
  # P0 默认：条件宇宙（Top3板块∩主板）+ 负IC因子取反 + 全层测评
  python -m zstock.factor_management.script.因子测评.run_eval \\
      --start 2026-01-05 --end 2026-07-27 --period 5 --plot

  # 关闭条件宇宙 / 关闭极性取反（对照全市场原始 IC）
  python -m zstock.factor_management.script.因子测评.run_eval \\
      --start 2026-01-05 --end 2026-07-27 --no-conditional --no-invert

  # 只测某一层
  python -m zstock.factor_management.script.因子测评.run_eval \\
      --start 2026-01-05 --end 2026-07-27 --layer dragon

输出：
  因子测评/output/<timestamp>/summary.csv
  因子测评/output/<timestamp>/<factor>_analysis.png（可选 --plot）
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib

# 无界面后端，避免 Windows 下 tkinter "main thread is not in main loop"
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 本目录包路径（目录名含中文，用文件相对导入）
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from data_loader import (  # noqa: E402
    DEFAULT_SECTOR_FACTORS,
    DEFAULT_STOCK_FACTORS,
    FactorEvalDataLoader,
)
from factor_evaluator import FactorEvaluator  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def plot_factor_analysis(
        ic_series: pd.DataFrame,
        quantile_returns: pd.DataFrame,
        title: str = "",
        save_path: Optional[str] = None,
) -> None:
    """IC 序列 / 分布 / 分层累计 / 分层均值。"""
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    fig.suptitle(title or "因子有效性分析", fontsize=14)

    ax1 = axes[0, 0]
    if not ic_series.empty and "Rank_IC" in ic_series.columns:
        ic_series["Rank_IC"].plot(ax=ax1, alpha=0.7, label="Rank IC")
        if len(ic_series) >= 5:
            ic_series["Rank_IC"].rolling(12, min_periods=3).mean().plot(
                ax=ax1, color="red", linewidth=2, label="12期均值"
            )
        ax1.axhline(y=0, color="black", linestyle="--")
        ax1.set_title("Rank IC 时间序列")
        ax1.legend()

    ax2 = axes[0, 1]
    if not ic_series.empty and "Rank_IC" in ic_series.columns:
        ic_series["Rank_IC"].hist(bins=30, ax=ax2, edgecolor="black")
        ax2.axvline(x=0, color="red", linestyle="--")
        ax2.set_title(f'IC分布 (均值={ic_series["Rank_IC"].mean():.4f})')

    ax3 = axes[1, 0]
    if not quantile_returns.empty:
        for col in quantile_returns.columns:
            quantile_returns[col].cumsum().plot(ax=ax3, label=col)
        ax3.set_title("各分层累计收益")
        ax3.legend()

    ax4 = axes[1, 1]
    if not quantile_returns.empty:
        quantile_returns.mean().plot(kind="bar", ax=ax4, color="steelblue")
        ax4.set_title("各分层平均收益")
        ax4.axhline(y=0, color="red", linestyle="--")

    plt.tight_layout()
    try:
        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(save_path, dpi=150)
    finally:
        plt.close(fig)
        plt.close("all")


def _jobs_from_args(args: argparse.Namespace) -> List[Tuple[str, str]]:
    """返回 [(layer, field), ...]。layer in sector/dragon/force。"""
    if args.field:
        layer = args.layer or "dragon"
        return [(layer, args.field)]

    jobs: List[Tuple[str, str]] = []
    layers = [args.layer] if args.layer else ["sector", "dragon", "force"]
    for layer in layers:
        if layer == "sector":
            jobs.extend(("sector", f) for f in DEFAULT_SECTOR_FACTORS)
        elif layer in DEFAULT_STOCK_FACTORS:
            jobs.extend((layer, f) for f in DEFAULT_STOCK_FACTORS[layer])
        else:
            raise ValueError(f"未知 layer={layer}，可选 sector/dragon/force")
    return jobs


async def _eval_one(
    loader: FactorEvalDataLoader,
    layer: str,
    field: str,
    start: str,
    end: str,
    period: int,
    n_quantiles: int,
    do_plot: bool,
    out_dir: Path,
    decay_max: int = 0,
    conditional: bool = True,
    top_sectors: int = 3,
    invert_negative: bool = True,
) -> Dict:
    tag = []
    if layer != "sector" and conditional:
        tag.append(f"condTop{top_sectors}")
    if invert_negative and field in (
        "f32_amount",
        "fcoop4_turnover_quality",
    ):
        tag.append("inv")
    tag_s = f" [{' '.join(tag)}]" if tag else ""
    logger.info(f"\n===== 测评 {layer}.{field}{tag_s} =====")
    if layer == "sector":
        factor, price = await loader.load_eval_bundle_sector(field, start, end)
    else:
        factor, price = await loader.load_eval_bundle_stock(
            layer,
            field,
            start,
            end,
            conditional=conditional,
            top_sectors=top_sectors,
            invert_negative=invert_negative,
        )

    if factor.empty or price.empty:
        logger.warning(f"跳过 {layer}.{field}: 无因子或价格数据")
        return {
            "layer": layer,
            "field": field,
            "conditional": bool(conditional and layer != "sector"),
            "polarity": -1 if (invert_negative and field in (
                "f32_amount", "fcoop4_turnover_quality"
            )) else 1,
            "Grade": "N/A",
            "Total_Score": 0,
            "error": "no_data",
        }

    # 因子日期落在价格日期内
    common_dates = factor.index.intersection(price.index)
    factor = factor.loc[common_dates]
    # 价格保留更多尾部供 shift(-period)
    price = price.loc[price.index >= common_dates.min()]

    ev = FactorEvaluator(factor, price)
    result = ev.evaluate(period=period, n_quantiles=n_quantiles)
    score = result["score"]
    summary = result["ic_summary"]
    ls = result["long_short"]
    autocorr = result["autocorr"]

    polarity = (
        -1
        if (
            invert_negative
            and field in ("f32_amount", "fcoop4_turnover_quality")
        )
        else 1
    )
    row = {
        "layer": layer,
        "field": field,
        "period": period,
        "conditional": bool(conditional and layer != "sector"),
        "top_sectors": int(top_sectors) if (conditional and layer != "sector") else 0,
        "polarity": polarity,
        "N_Periods": score.get("N_Periods", 0),
        "IC_Mean": score.get("IC_Mean"),
        "Rank_IC_Mean": score.get("Rank_IC_Mean"),
        "Rank_ICIR": score.get("Rank_ICIR"),
        "IC_Positive_Ratio": (
            float(summary.get("IC_Positive_Ratio")) if not summary.empty else None
        ),
        "IC_p_value": float(summary.get("IC_p_value")) if not summary.empty else None,
        "LS_Mean_Return": ls.get("LS_Mean_Return"),
        "LS_Sharpe": ls.get("LS_Sharpe"),
        "LS_Win_Rate": ls.get("LS_Win_Rate"),
        "mean_autocorr": autocorr.get("mean_autocorr"),
        "IC_Score": score.get("IC_Score"),
        "ICIR_Score": score.get("ICIR_Score"),
        "WinRate_Score": score.get("WinRate_Score"),
        "Significance_Score": score.get("Significance_Score"),
        "Total_Score": score.get("Total_Score"),
        "Grade": score.get("Grade"),
    }

    logger.info(
        f"  RankIC={row['Rank_IC_Mean']:.4f}  RankICIR={row['Rank_ICIR']:.3f}  "
        f"Score={row['Total_Score']}  {row['Grade']}"
        if row["Rank_IC_Mean"] == row["Rank_IC_Mean"]
        else f"  Score={row['Total_Score']}  {row['Grade']}"
    )

    if do_plot:
        png = out_dir / f"{layer}_{field}_p{period}.png"
        plot_factor_analysis(
            result["ic_series"],
            result["quantile_returns"],
            title=f"{layer}.{field} (period={period})",
            save_path=str(png),
        )
        logger.info(f"  图已保存: {png.name}")

    if decay_max > 0:
        decay = ev.calc_factor_decay(max_period=decay_max)
        decay.to_csv(out_dir / f"{layer}_{field}_decay.csv")

    return row


async def async_main(args: argparse.Namespace) -> int:
    try:
        from app.core import database as db_module

        await db_module.db_manager.init_mongodb()
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        return 1

    out_dir = Path(args.output) if args.output else (
        _THIS_DIR / "output" / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = _jobs_from_args(args)
    loader = FactorEvalDataLoader()
    logger.info(
        f"测评配置: conditional={args.conditional} top_sectors={args.top_sectors} "
        f"invert_negative={args.invert}"
    )
    rows = []
    for layer, field in jobs:
        try:
            row = await _eval_one(
                loader,
                layer,
                field,
                args.start,
                args.end,
                args.period,
                args.quantiles,
                args.plot,
                out_dir,
                decay_max=int(args.decay_max or 0),
                conditional=bool(args.conditional),
                top_sectors=int(args.top_sectors),
                invert_negative=bool(args.invert),
            )
            rows.append(row)
        except Exception as e:
            logger.error(f"{layer}.{field} 测评失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            rows.append(
                {
                    "layer": layer,
                    "field": field,
                    "Grade": "ERROR",
                    "Total_Score": 0,
                    "error": str(e),
                }
            )

    summary = pd.DataFrame(rows)
    csv_path = out_dir / "summary.csv"
    summary.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info(f"\n汇总已写入: {csv_path}")
    if not summary.empty and "Total_Score" in summary.columns:
        show_cols = [
            c
            for c in [
                "layer",
                "field",
                "Rank_IC_Mean",
                "Rank_ICIR",
                "Total_Score",
                "Grade",
            ]
            if c in summary.columns
        ]
        logger.info("\n" + summary[show_cols].to_string(index=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="zstock 因子有效性测评")
    p.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    p.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    p.add_argument(
        "--layer",
        choices=["sector", "dragon", "force"],
        default=None,
        help="只测某一层；默认三层全测",
    )
    p.add_argument("--field", default=None, help="只测单个因子字段")
    p.add_argument("--period", type=int, default=5, help="预测周期（交易日），默认 5")
    p.add_argument("--quantiles", type=int, default=5, help="分层数，默认 5")
    p.add_argument(
        "--decay-max",
        type=int,
        default=0,
        help="若 >0，额外输出 1..N 的 IC 衰减 csv",
    )
    p.add_argument("--plot", action="store_true", help="保存分析图")
    p.add_argument("--output", default=None, help="输出目录")
    p.add_argument(
        "--conditional",
        dest="conditional",
        action="store_true",
        default=True,
        help="个股层用条件宇宙（Top板块∩主板非ST），默认开启",
    )
    p.add_argument(
        "--no-conditional",
        dest="conditional",
        action="store_false",
        help="关闭条件宇宙，全市场测评",
    )
    p.add_argument(
        "--top-sectors",
        type=int,
        default=3,
        help="条件宇宙 Top-N 板块，默认 3",
    )
    p.add_argument(
        "--invert",
        dest="invert",
        action="store_true",
        default=True,
        help="对 f32/fcoop4 取反后再测（与打分侧一致），默认开启",
    )
    p.add_argument(
        "--no-invert",
        dest="invert",
        action="store_false",
        help="关闭负IC因子取反",
    )
    return p


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
