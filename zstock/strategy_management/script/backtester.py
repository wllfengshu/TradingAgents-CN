"""
日频截面策略回测器

定位：
- 配合 StrategyPipeline 把"每个交易日的目标持仓"按日次再平衡跑出净值曲线；
- 不依赖 Qlib，纯 pandas/numpy 实现；
- 输入既支持真实 OHLCV（从 query_service 拉），也支持离线注入。

回测假设：
- T 日生成信号，按 T+1 开盘价建仓 / 调仓；当日收益用 T+1 的开盘到收盘；
- 后续持仓日的收益用每日 close→close；
- 单次换手成本 = turnover * fee_rate，单边费率配在 TurnoverController 上。

入口：
- Backtester.run() — 注入 ohlcv_provider / signal_provider 的编程接口
- Backtester.run_real_data() — 从 MongoDB 拉 OHLCV + 可选预计算因子
- Backtester.run_cli() — CLI（python -m zstock.strategy_management.script.backtester）

返回：
- BacktestResult.equity_curve  累计净值
- BacktestResult.daily_returns 日收益率
- BacktestResult.holdings_log  每个再平衡日的持仓快照
- BacktestResult.metrics       综合指标
"""

from __future__ import annotations

import asyncio
import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

import logging

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.strategy_management.adaptive_rebalance import resolve_rebalance_freq
from zstock.common.config.strategy_config import (
    build_runtime_config,
    load_strategy_params,
)


# ─────────────────────────────────────────────────────────────────
# 配置加载（从 StrategyPipeline 中提出的脚本实用函数）
# ─────────────────────────────────────────────────────────────────

_CONFIG_CACHE: Optional[Dict[str, Dict]] = None


def _get_default_config() -> Dict[str, Dict]:
    """从 strategy_params.json 加载策略参数，转换为 pipeline 各阶段所需的配置结构。

    派生逻辑统一委托给 zstock.common.config.strategy_config.build_runtime_config()。
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    cfg = build_runtime_config()
    _CONFIG_CACHE = cfg
    return cfg


def load_runtime_config(override: Optional[Dict] = None) -> Dict[str, Dict]:
    """加载策略配置（backtester 脚本专用）。"""
    import copy
    base = copy.deepcopy(_get_default_config())
    if not override:
        return base
    for k, v in override.items():
        if k in base and isinstance(v, dict) and isinstance(base[k], dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


@dataclass
class BacktestResult:
    equity_curve: pd.Series  # index: date(str), value: 净值
    daily_returns: pd.Series  # 日收益率
    drawdown_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    turnover_series: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    cost_series: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    holdings_log: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    config_snapshot: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        m = self.metrics
        cfg = self.config_snapshot
        lines = [
            "",
            "╔══════════════════════════════════════════════════════════════════╗",
            "║                          回测结果摘要                            ║",
            "╚══════════════════════════════════════════════════════════════════╝",
            "── 回测配置 ──",
            f"  时间区间        : {self.equity_curve.index.min()} ~ {self.equity_curve.index.max()}",
            f"  交易日数        : {len(self.equity_curve)}",
            f"  初始资金        : {cfg.get('initial_capital', 0):,.2f}",
            f"  最终净值        : {self.equity_curve.iloc[-1]:.4f}",
            f"  最终资金        : {cfg.get('initial_capital', 0) * float(self.equity_curve.iloc[-1]):,.2f}",
            f"  再平衡频率      : 每 {cfg.get('rebalance_freq', 1)} 个交易日",
            f"  单边费率        : {cfg.get('fee_rate', 0.0):.4%}",
            f"  最少 / 最大持仓 : {cfg.get('min_holdings', '?')} / {cfg.get('max_holdings', '?')}",
            f"  单股权重上限    : {cfg.get('max_weight_per_stock', '?')}",
            f"  权重分配方式    : {cfg.get('weighting', '?')}",
            f"  Buffer 阈值     : {cfg.get('buffer_threshold', '?')}",
            "── 收益指标 ──",
            f"  累计收益        : {m.get('total_return', 0.0):>10.4%}",
            f"  年化收益        : {m.get('annualized_return', 0.0):>10.4%}",
            f"  年化波动率      : {m.get('annualized_vol', 0.0):>10.4%}",
            f"  夏普 (Rf=0)     : {m.get('sharpe', 0.0):>10.4f}",
            f"  索提诺 (Rf=0)   : {m.get('sortino', 0.0):>10.4f}",
            f"  卡玛 (年化/MDD) : {m.get('calmar', 0.0):>10.4f}",
            "── 风险指标 ──",
            f"  最大回撤        : {m.get('max_drawdown', 0.0):>10.4%}",
            f"  最长回撤天数    : {m.get('max_drawdown_duration', 0):>10d} 天",
            f"  下行波动率      : {m.get('downside_vol', 0.0):>10.4%}",
            f"  胜率            : {m.get('win_rate', 0.0):>10.4%}",
            f"  盈亏比 (avg)    : {m.get('profit_loss_ratio', 0.0):>10.4f}",
            f"  最大单日盈利    : {m.get('best_day', 0.0):>10.4%}",
            f"  最大单日亏损    : {m.get('worst_day', 0.0):>10.4%}",
            "── 换手与成本 ──",
            f"  调仓次数        : {m.get('rebalance_count', 0):>10d}",
            f"  平均换手率      : {m.get('avg_turnover', 0.0):>10.4%}",
            f"  最大换手率      : {m.get('max_turnover', 0.0):>10.4%}",
            f"  累计成本        : {m.get('total_cost', 0.0):>10.4%}",
            f"  扣费前累计收益  : {m.get('total_return_gross', 0.0):>10.4%}",
            "",
        ]
        return "\n".join(lines)

    def plot(
        self,
        output_path: Optional[str] = None,
        title: str = "策略回测净值曲线",
        show: bool = False,
    ) -> Optional[str]:
        """绘制收益曲线 + 回撤 + 换手率 + 日收益分布。

        Args:
            output_path: 保存路径；None 则自动生成 `backtest_<timestamp>.png`。
            title: 图表主标题。
            show: True 时调用 plt.show()（本地交互场景）。

        Returns:
            实际保存的文件路径。
        """
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
        except ImportError:
            logger.error("matplotlib 未安装，无法绘图。pip install matplotlib")
            return None

        # 中文字体
        for font in ('Microsoft YaHei', 'SimHei', 'PingFang SC', 'DejaVu Sans'):
            try:
                plt.rcParams['font.sans-serif'] = [font]
                plt.rcParams['axes.unicode_minus'] = False
                break
            except Exception:
                continue

        try:
            dates = pd.to_datetime(self.equity_curve.index)
        except Exception:
            dates = self.equity_curve.index

        fig, axes = plt.subplots(4, 1, figsize=(14, 14), sharex=True,
                                 gridspec_kw={'height_ratios': [3, 1.5, 1, 1.2]})

        # 1) 净值曲线
        ax = axes[0]
        ax.plot(dates, self.equity_curve.values, color='#2e7d32', linewidth=1.8, label='策略净值')
        ax.axhline(1.0, color='gray', linestyle='--', linewidth=0.8, label='初始净值')
        ax.set_ylabel('净值')
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.grid(True, alpha=0.3)
        ax.legend(loc='upper right')
        m = self.metrics
        annotation = (
            f"累计 {m.get('total_return', 0):.2%}   "
            f"年化 {m.get('annualized_return', 0):.2%}   "
            f"夏普 {m.get('sharpe', 0):.2f}   "
            f"MDD {m.get('max_drawdown', 0):.2%}   "
            f"胜率 {m.get('win_rate', 0):.2%}"
        )
        ax.text(0.5, -0.18, annotation, transform=ax.transAxes,
                fontsize=10, horizontalalignment='center', verticalalignment='top',
                bbox=dict(boxstyle='round,pad=0.4', facecolor='#fffde7', alpha=0.9, edgecolor='gray'))

        # 2) 回撤
        ax = axes[1]
        if len(self.drawdown_curve) > 0:
            ax.fill_between(dates, self.drawdown_curve.values, 0, color='#c62828', alpha=0.35)
            ax.plot(dates, self.drawdown_curve.values, color='#c62828', linewidth=1.0)
        ax.set_ylabel('回撤')
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0%}"))
        ax.grid(True, alpha=0.3)

        # 3) 换手率（bar）
        ax = axes[2]
        if len(self.turnover_series) > 0:
            ax.bar(dates, self.turnover_series.values, width=1.0, color='#1565c0', alpha=0.6, label='换手率')
        ax.set_ylabel('换手率')
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0%}"))
        ax.grid(True, alpha=0.3)
        ax.legend(loc='upper right')

        # 4) 日收益分布（柱状）
        ax = axes[3]
        colors = ['#43a047' if r >= 0 else '#e53935' for r in self.daily_returns.values]
        ax.bar(dates, self.daily_returns.values, width=1.0, color=colors, alpha=0.7)
        ax.axhline(0, color='gray', linewidth=0.5)
        ax.set_ylabel('日收益')
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.1%}"))
        ax.grid(True, alpha=0.3)
        ax.set_xlabel('日期')

        # x 轴时间格式
        try:
            for a in axes:
                a.xaxis.set_major_locator(mdates.AutoDateLocator())
                a.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            fig.autofmt_xdate(rotation=30)
        except Exception:
            pass

        plt.tight_layout()

        if output_path is None:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = f"backtest_{ts}.png"
        fig.savefig(output_path, dpi=120, bbox_inches='tight')
        if show:
            plt.show()
        plt.close(fig)
        logger.info(f"📊 回测图表已保存: {output_path}")
        return output_path

    def export_csv(self, output_dir: str = '.') -> Dict[str, str]:
        """把净值、日收益、换手、成本、持仓快照、交易记录写到 CSV 目录里。"""
        from pathlib import Path
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        paths = {}
        df_main = pd.DataFrame({
            'date': self.equity_curve.index,
            'equity': self.equity_curve.values,
            'daily_return': self.daily_returns.values,
            'drawdown': self.drawdown_curve.values if len(self.drawdown_curve) else np.nan,
            'turnover': self.turnover_series.values if len(self.turnover_series) else 0.0,
            'cost': self.cost_series.values if len(self.cost_series) else 0.0,
        })
        p = out / f'backtest_curve_{ts}.csv'
        df_main.to_csv(p, index=False, encoding='utf-8-sig')
        paths['curve'] = str(p)
        if self.trades:
            p = out / f'backtest_trades_{ts}.csv'
            pd.DataFrame(self.trades).to_csv(p, index=False, encoding='utf-8-sig')
            paths['trades'] = str(p)
        if self.holdings_log:
            flat = []
            for snap in self.holdings_log:
                for h in snap.get('holdings', []):
                    row = {'trade_date': snap.get('trade_date')}
                    row.update(h)
                    flat.append(row)
            if flat:
                p = out / f'backtest_holdings_{ts}.csv'
                pd.DataFrame(flat).to_csv(p, index=False, encoding='utf-8-sig')
                paths['holdings'] = str(p)
        return paths


class Backtester:
    """日频再平衡回测器。"""

    def __init__(
        self,
        strategy_pipeline: Optional[StrategyPipeline] = None,
        fee_rate: Optional[float] = None,
        initial_capital: Optional[float] = None,
        factor_pipeline: Optional["CrossSectionStrategyPipeline"] = None,
    ):
        self.strategy = strategy_pipeline or StrategyPipeline()
        if fee_rate is None or initial_capital is None:
            bt_cfg = (load_strategy_params() or {}).get("backtest") or {}
            fee_rate = fee_rate if fee_rate is not None else float(bt_cfg.get("fee_rate", 0.0015))
            initial_capital = (
                initial_capital
                if initial_capital is not None
                else float(bt_cfg.get("initial_capital", 1e7))
            )
        self.fee_rate = fee_rate
        self.initial_capital = initial_capital
        self.factor_pipeline = factor_pipeline
        # 同步 fee_rate 到 turnover_controller，保持成本估算一致
        self.strategy.turnover_controller.fee_rate = fee_rate
        logger.info(f"✅ Backtester 初始化完成: fee={fee_rate} init_capital={initial_capital}")

    # ============================== 真实数据 / CLI ==============================

    @staticmethod
    def _default_rebalance_freq() -> int:
        """CLI > strategy_params.json backtest.rebalance_freq > 5。"""
        try:
            params = load_strategy_params()
            return int(params.get("backtest", {}).get("rebalance_freq", 5))
        except Exception:
            return 5

    @staticmethod
    def _configure_cli_logging() -> None:
        for h in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(h)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        logging.getLogger("zstock.data_management").setLevel(logging.INFO)
        logging.getLogger("zstock.factor_management").setLevel(logging.INFO)
        logging.getLogger("zstock.strategy_management").setLevel(logging.INFO)
        logging.getLogger("app").setLevel(logging.WARNING)

    @classmethod
    def build_arg_parser(cls) -> argparse.ArgumentParser:
        bt_cfg = (load_strategy_params() or {}).get("backtest") or {}
        parser = argparse.ArgumentParser(description="真实数据回测")
        parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
        parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
        parser.add_argument("--lookback", type=int, default=60, help="OHLCV 回看天数（普通模式）")
        parser.add_argument(
            "--capital",
            type=float,
            default=float(bt_cfg.get("initial_capital", 1e7)),
            help="初始资金（默认读 strategy_params.json backtest.initial_capital）",
        )
        parser.add_argument(
            "--fee",
            type=float,
            default=float(bt_cfg.get("fee_rate", 0.0015)),
            help="单边费率（默认读 strategy_params.json backtest.fee_rate）",
        )
        parser.add_argument(
            "--rebalance",
            type=int,
            default=None,
            help="再平衡频率（天）；默认读 strategy_params.json backtest.rebalance_freq",
        )
        parser.add_argument("--precomputed", action="store_true", help="使用预计算因子（极速回测）")
        parser.add_argument("--output", default="output", help="输出目录（相对 script/）")
        return parser

    async def run_real_data(
        self,
        start_date: str,
        end_date: str,
        *,
        lookback_days: int = 60,
        use_precomputed_factors: bool = False,
        rebalance_freq: Optional[int] = None,
        output_dir: Optional[str] = None,
        verbose: bool = True,
        strategy_config: Optional[Dict] = None,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
        save_outputs: bool = True,
    ) -> BacktestResult:
        """
        真实数据回测：从 MongoDB 加载 OHLCV，可选预计算因子，运行回测并保存图表/CSV。

        调用方需已 init_zstock_database()；run_cli() 会自动管理数据库生命周期。
        """
        from zstock.common.utils.common_utils import normalize_date
        from zstock.data_management.query_service import get_data_query_service

        rebalance_freq = rebalance_freq if rebalance_freq is not None else self._default_rebalance_freq()
        mode_label = "预计算极速" if use_precomputed_factors else "普通（实时因子）"

        if verbose:
            logger.info("=" * 70)
            logger.info(f"🚀 真实数据回测 [{mode_label}]")
            logger.info(f"   区间: {start_date} → {end_date}")
            logger.info(
                f"   资金: ¥{self.initial_capital:,.0f}  "
                f"费率: {self.fee_rate:.4%}  再平衡: 每{rebalance_freq}天"
            )
            logger.info("=" * 70)

        if use_precomputed_factors and self.factor_pipeline is None:
            from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

            self.factor_pipeline = CrossSectionStrategyPipeline()

        qs = get_data_query_service()

        if verbose:
            logger.info("📦 加载 OHLCV 数据...")

        all_stocks_docs, _ = await qs.get_all_stocks()
        mainboard_codes = [
            d["code"] for d in all_stocks_docs
            if d.get("is_mainboard") and not d.get("is_st")
        ]
        if verbose:
            logger.info(f"   主板非ST: {len(mainboard_codes)} 只")

        ohlcv_data: Dict[str, pd.DataFrame] = {}
        chunk_size = 500
        ohlcv_batch_size = 80
        ohlcv_query_concurrency = 6
        total_chunks = (len(mainboard_codes) + chunk_size - 1) // chunk_size
        failed_chunks = 0

        for ci, i in enumerate(range(0, len(mainboard_codes), chunk_size), 1):
            chunk = mainboard_codes[i : i + chunk_size]
            if verbose:
                logger.info(f"   OHLCV [{ci}/{total_chunks}] {len(chunk)} 只...")
            try:
                batch = await qs.get_ohlcv_batch(
                    chunk,
                    start_date,
                    end_date,
                    batch_size=ohlcv_batch_size,
                    query_concurrency=ohlcv_query_concurrency,
                )
                if batch:
                    ohlcv_data.update(batch)
            except Exception as e:
                logger.warning(f"   ⚠️ 批次 {ci} 加载失败（跳过）: {e}")
                failed_chunks += 1

        for code, df in ohlcv_data.items():
            if "trade_date" in df.columns:
                df["trade_date"] = df["trade_date"].apply(normalize_date)

        if verbose:
            logger.info(
                f"✅ OHLCV 加载完成: {len(ohlcv_data)} 只 "
                f"(失败批次: {failed_chunks}/{total_chunks})"
            )

        if not ohlcv_data:
            raise RuntimeError("无 OHLCV 数据，无法回测")

        ohlcv_provider = make_ohlcv_provider_from_dict(ohlcv_data)

        if verbose:
            logger.info("🚀 回测开始...")

        result = await self.run(
            start_date=start_date,
            end_date=end_date,
            ohlcv_provider=ohlcv_provider,
            rebalance_freq=rebalance_freq,
            strategy_config=strategy_config,
            sectors=sectors,
            max_stocks=max_stocks,
            verbose=verbose,
            use_precomputed_factors=use_precomputed_factors,
        )

        if save_outputs and output_dir is not None:
            out = Path(__file__).resolve().parent / output_dir
            out.mkdir(parents=True, exist_ok=True)
            mode_tag = "precomputed" if use_precomputed_factors else "realtime"
            chart_path = result.plot(
                output_path=str(out / f"backtest_{mode_tag}.png"),
                title=f"真实数据回测 [{mode_label}] {start_date}~{end_date}",
            )
            csv_paths = result.export_csv(str(out))
            if verbose:
                logger.info(f"📊 图表已保存: {chart_path}")
                logger.info(f"📁 CSV 已导出: {csv_paths}")

        return result

    @classmethod
    async def run_cli(cls, argv: Optional[List[str]] = None) -> int:
        """CLI 入口：解析参数、初始化数据库、运行真实数据回测。"""
        cls._configure_cli_logging()
        args = cls.build_arg_parser().parse_args(argv)

        rebalance_freq = args.rebalance if args.rebalance is not None else cls._default_rebalance_freq()

        try:
            from zstock.common.utils.db_utils import init_zstock_database, close_zstock_database

            await init_zstock_database()
        except Exception as e:
            logger.error(f"❌ 数据库初始化失败: {e}")
            return 1

        try:
            bt = cls(
                fee_rate=args.fee,
                initial_capital=args.capital,
            )
            result = await bt.run_real_data(
                start_date=args.start,
                end_date=args.end,
                lookback_days=args.lookback,
                use_precomputed_factors=args.precomputed,
                rebalance_freq=rebalance_freq,
                output_dir=args.output,
                verbose=True,
            )
            print(result.summary())
            return 0
        except Exception as e:
            logger.error(f"❌ 回测失败: {e}")
            import traceback

            traceback.print_exc()
            return 1
        finally:
            try:
                from zstock.common.utils.db_utils import close_zstock_database

                await close_zstock_database()
            except Exception:
                pass

    async def run(
        self,
        start_date: str,
        end_date: str,
        ohlcv_provider: Callable[[str], Dict[str, pd.DataFrame]],
        signal_provider: Optional[Callable[[str], Dict[str, Any]]] = None,
        rebalance_freq: int = 1,
        strategy_config: Optional[Dict] = None,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
        verbose: bool = True,
        use_precomputed_factors: bool = False,
    ) -> BacktestResult:
        """
        Args:
            start_date / end_date: 'YYYY-MM-DD' 闭区间。
            ohlcv_provider: 同步函数，输入交易日 'YYYY-MM-DD'，返回
                {code: DataFrame(trade_date, open, close, ...)}。
                只需要覆盖 [start_date - lookback, end_date+1] 区间。
            signal_provider: 可选；同步函数，给定交易日返回 prebuilt_data
                直接喂进 factor pipeline（离线/确定性回测用）。None 时走
                StrategyPipeline 默认的真实数据拉取。
            rebalance_freq: 每隔几个交易日再平衡一次（1=日频）。
            strategy_config: 透传给 StrategyPipeline 的 config。
            sectors / max_stocks: 透传。
            verbose: True 时每个交易日都打印日志。
            use_precomputed_factors: True 时用 factor_pipeline.score_signals 读预计算因子。

        Returns:
            BacktestResult
        """
        trade_dates = self._gen_trade_dates(start_date, end_date)
        if not trade_dates:
            raise ValueError(f"start_date={start_date} ~ end_date={end_date} 无可用交易日")

        # 打印开场 banner（无 strategy_config 时用 StrategyPipeline 默认配置）
        cfg_print = load_runtime_config(strategy_config)
        opt_cfg = (cfg_print.get('portfolio_optimization') or {})
        tov_cfg = (cfg_print.get('turnover_control') or {})
        _adaptive_banner = bool((cfg_print.get("adaptive_rebalance") or {}).get("enabled"))
        if verbose:
            logger.info("=" * 70)
            logger.info("🚀 回测启动")
            logger.info("=" * 70)
            logger.info(f"  区间        : {start_date} ~ {end_date}   ({len(trade_dates)} 个交易日)")
            logger.info(f"  初始资金    : {self.initial_capital:,.2f}")
            logger.info(f"  再平衡频率  : 每 {rebalance_freq} 个交易日"
                        + (" (自适应)" if _adaptive_banner else ""))
            logger.info(f"  单边费率    : {self.fee_rate:.4%}")
            logger.info(f"  持仓区间    : [{opt_cfg.get('min_holdings', '?')}, {opt_cfg.get('max_holdings', '?')}]")
            logger.info(f"  单股权重上限: {opt_cfg.get('max_weight_per_stock', '?')}")
            logger.info(f"  权重分配    : {opt_cfg.get('weighting', 'score')}")
            logger.info(f"  Buffer 阈值 : {tov_cfg.get('buffer_threshold', 0.15)}")
            logger.info(f"  板块限定    : {sectors or '全市场'}")
            logger.info(f"  最大股票数  : {max_stocks or '不限'}")
            logger.info("=" * 70)

        if use_precomputed_factors and self.factor_pipeline:
            if verbose:
                logger.info("📦 预加载区间预计算因子（减少 Mongo 逐日查询）...")
            await self.factor_pipeline.preload_precomputed_factors(
                start_date, end_date
            )

        equity = self.initial_capital
        last_holdings: Optional[pd.DataFrame] = None
        last_prices: Dict[str, float] = {}

        equity_records: List[float] = []
        return_records: List[float] = []
        gross_return_records: List[float] = []
        holdings_log: List[Dict[str, Any]] = []
        trades: List[Dict[str, Any]] = []
        turnover_records: List[float] = []
        cost_records: List[float] = []

        cfg_runtime = (
            load_runtime_config(strategy_config)
            if strategy_config is not None
            else load_runtime_config()
        )
        hard_stop_pct = float(
            (cfg_runtime.get("risk_management") or {}).get("hard_stop_loss_pct", -0.08)
        )
        exit_cfg = cfg_runtime.get("exit_rules") or {}
        flat_after_bad_days = int(exit_cfg.get("flat_after_bad_days", 5))
        no_signal_action = str(exit_cfg.get("no_signal_action", "hold"))
        no_signal_reduce_scale = float(exit_cfg.get("no_signal_reduce_scale", 0.5))
        out_of_cand_days = int(
            exit_cfg.get(
                "consecutive_days_out_of_candidates",
                exit_cfg.get("consecutive_days_out_of_top3", 3),
            )
        )
        max_holdings = int(
            (cfg_runtime.get("portfolio_optimization") or {}).get("max_holdings", 5)
        )

        bad_streak = 0  # 连续红灯 / 无信号天数
        out_of_list_days: Dict[str, int] = {}  # 持仓跌出买入名单的连续天数

        adaptive_cfg = cfg_runtime.get("adaptive_rebalance") or {}
        use_adaptive = bool(adaptive_cfg.get("enabled"))
        target_freq = rebalance_freq
        days_since_rebalance = rebalance_freq  # 首个交易日触发再平衡
        last_regime = "neutral"
        last_market_grade = "green"

        for i, td in enumerate(trade_dates):
            day_ohlcv = ohlcv_provider(td) or {}

            # === 第一步：用昨日的持仓 + 今日收盘价计算今日收益 ===
            day_return = 0.0
            stocks_priced = 0
            if last_holdings is not None and not last_holdings.empty:
                for _, row in last_holdings.iterrows():
                    code = row['code']
                    weight = float(row['weight'])
                    today_df = day_ohlcv.get(code)
                    if today_df is None or today_df.empty:
                        continue
                    today_row = self._row_for_date(today_df, td)
                    if today_row is None:
                        continue
                    close_today = float(today_row.get('close', np.nan))
                    if np.isnan(close_today) or close_today <= 0:
                        continue
                    prev_close = last_prices.get(code)
                    if prev_close is None or prev_close <= 0:
                        open_today = float(today_row.get('open', close_today))
                        if open_today <= 0:
                            continue
                        ret = close_today / open_today - 1.0
                    else:
                        ret = close_today / prev_close - 1.0
                    day_return += weight * ret
                    last_prices[code] = close_today
                    stocks_priced += 1

            equity_before_cost = equity * (1.0 + day_return)
            equity = equity_before_cost

            # === 1.5 硬止损：相对入场价跌破阈值则当日卖出，留现金 ===
            day_cost = 0.0
            day_turnover = 0.0
            if last_holdings is not None and not last_holdings.empty:
                last_holdings, stop_turnover, stop_cost = self._apply_hard_stops(
                    last_holdings, last_prices, td, hard_stop_pct
                )
                if stop_cost > 0:
                    equity *= (1.0 - stop_cost)
                    day_cost += stop_cost
                    day_turnover += stop_turnover

            # === 第二步：决定今天要不要再平衡 ===
            if use_adaptive:
                do_rebalance = days_since_rebalance >= target_freq
            else:
                do_rebalance = (i % rebalance_freq == 0)
            rebalance_status = '-'
            n_holdings_today = len(last_holdings) if last_holdings is not None else 0

            if do_rebalance:
                precomputed_signals = None
                if use_precomputed_factors and self.factor_pipeline:
                    # ── 快速路径：从 MongoDB 读预计算信号 ──
                    # 缺日（节假日/未预计算）跳过再平衡，继续持有，不中断整段回测
                    try:
                        precomputed_signals = await self.factor_pipeline.score_signals(td)
                    except ValueError as e:
                        logger.warning(f"⚠️ {td} 无预计算因子，跳过再平衡: {e}")
                        summary = {
                            "status": "skip_no_factors",
                            "error": str(e),
                        }
                    else:
                        summary = await self.strategy.execute_full_pipeline(
                            trade_date=td,
                            current_positions=last_holdings,
                            total_capital=equity,
                            config=cfg_runtime,
                            precomputed_signals=precomputed_signals,
                        )
                else:
                    # ── 原有路径：实时计算 ──
                    prebuilt = signal_provider(td) if signal_provider else None
                    try:
                        summary = await self.strategy.execute_full_pipeline(
                            trade_date=td,
                            current_positions=last_holdings,
                            total_capital=equity,
                            config=cfg_runtime,
                            sectors=sectors,
                            max_stocks=max_stocks,
                            prebuilt_data=prebuilt,
                        )
                    except Exception as e:
                        logger.error(f"{td} strategy pipeline 失败: {e}")
                        summary = {'status': 'failed', 'error': str(e)}

                rebalance_status = summary.get('status', 'failed')
                market_grade = summary.get('market_grade') or (
                    StrategyPipeline._extract_market_grade(precomputed_signals)
                    if precomputed_signals is not None
                    else "unknown"
                )
                if precomputed_signals is not None and hasattr(precomputed_signals, "attrs"):
                    last_regime = str(precomputed_signals.attrs.get("regime", last_regime))
                elif summary.get("regime"):
                    last_regime = str(summary.get("regime"))
                last_market_grade = market_grade if market_grade != "unknown" else last_market_grade
                if use_adaptive:
                    target_freq = resolve_rebalance_freq(
                        adaptive_cfg,
                        regime=last_regime,
                        market_grade=last_market_grade,
                        default_freq=rebalance_freq,
                    )
                days_since_rebalance = 0

                # ── 更新「跌出买入名单」连续天数 ──
                # 仅在有真实买入名单的成功日累计；无信号/缺因子日不计入
                # （无信号清仓由 bad_streak + flat_after_bad_days 负责）
                force_out: set = set()
                if rebalance_status == "success":
                    signals_for_exit = None
                    if summary.get("results"):
                        signals_for_exit = summary["results"].get("signals")
                    if signals_for_exit is None:
                        signals_for_exit = precomputed_signals
                    buy_codes = self._buy_code_set(signals_for_exit, max_holdings)
                    out_of_list_days = self._update_out_of_list_days(
                        out_of_list_days, last_holdings, buy_codes
                    )
                    force_out = {
                        c for c, d in out_of_list_days.items() if d >= out_of_cand_days
                    }

                # ── 无信号 / 坏环境：减仓或清仓（不再傻持）──
                if rebalance_status == "no_signals":
                    bad_streak += 1
                    new_holdings, exit_reason = self._handle_no_signal_exit(
                        last_holdings,
                        bad_streak=bad_streak,
                        flat_after_bad_days=flat_after_bad_days,
                        action=no_signal_action,
                        reduce_scale=no_signal_reduce_scale,
                        force_exit_codes=None,
                    )
                    costs = self.strategy.turnover_controller.estimate_trading_costs(
                        current_holdings=last_holdings,
                        new_holdings=new_holdings,
                        total_capital=equity,
                    )
                    reb_turnover = float(costs.get("turnover", 0.0))
                    reb_cost = float(costs.get("cost_pct", 0.0))
                    day_turnover += reb_turnover
                    day_cost += reb_cost
                    equity *= (1.0 - reb_cost)
                    new_holdings = self._attach_entry_meta(
                        new_holdings, last_holdings, last_prices, day_ohlcv, td
                    )
                    last_holdings, last_prices = self._commit_holdings(
                        new_holdings, last_prices, day_ohlcv, td
                    )
                    n_holdings_today = (
                        len(last_holdings) if last_holdings is not None else 0
                    )
                    trades.append({
                        "trade_date": td,
                        "turnover": day_turnover,
                        "cost_pct": day_cost,
                        "cost_amount": costs.get("cost_amount", 0.0),
                        "n_holdings": n_holdings_today,
                        "top_holding": None,
                        "top_weight": 0.0,
                        "risk_status": exit_reason,
                        "risk_issues": f"bad_streak={bad_streak};grade={market_grade}",
                        "position_scale": 0.0,
                    })
                    holdings_log.append({
                        "trade_date": td,
                        "n_holdings": n_holdings_today,
                        "turnover": day_turnover,
                        "cost_pct": day_cost,
                        "holdings": (
                            last_holdings.to_dict(orient="records")
                            if last_holdings is not None and not last_holdings.empty
                            else []
                        ),
                    })
                    if verbose:
                        logger.info(
                            f"  🚪 {td} 无信号退出: {exit_reason} "
                            f"streak={bad_streak} grade={market_grade} "
                            f"持仓={n_holdings_today}"
                        )
                    rebalance_status = exit_reason

                elif rebalance_status == 'success':
                    bad_streak = 0 if market_grade != "red" else bad_streak + 1
                    new_holdings = summary['results']['final_holdings'].copy() if 'final_holdings' in summary['results'] else pd.DataFrame()
                    # 连续跌出买入名单 → 从目标持仓剔除
                    if force_out and new_holdings is not None and not new_holdings.empty:
                        before = len(new_holdings)
                        new_holdings = new_holdings[~new_holdings["code"].isin(force_out)].copy()
                        if len(new_holdings) < before:
                            logger.info(
                                f"🚪 {td} 连续{out_of_cand_days}日跌出候选，剔除 "
                                f"{sorted(force_out)}"
                            )
                            if not new_holdings.empty:
                                new_holdings = self.strategy.turnover_controller._normalize(
                                    new_holdings
                                )
                    # 若仍处于坏环境连击且达到清仓阈值（防御：成功但红灯）
                    if bad_streak >= flat_after_bad_days and market_grade == "red":
                        new_holdings = pd.DataFrame(columns=["code", "weight", "score"])
                        logger.info(
                            f"🛑 {td} 连续{bad_streak}日坏环境，强制清仓"
                        )

                    from zstock.strategy_management.weak_regime_protection import (
                        compute_drawdown_scale,
                    )

                    dd_scale = compute_drawdown_scale(
                        equity_records + [equity],
                        cfg_runtime.get("weak_regime_protection"),
                    )
                    if (
                        dd_scale < 1.0 - 1e-9
                        and new_holdings is not None
                        and not new_holdings.empty
                    ):
                        new_holdings = new_holdings.copy()
                        new_holdings["weight"] = (
                            new_holdings["weight"].astype(float) * dd_scale
                        )
                        logger.info(
                            f"📉 {td} 回撤节流 scale={dd_scale:.2f} "
                            f"敞口={new_holdings['weight'].sum():.2%}"
                        )

                    costs = summary['results'].get('trading_costs', {})
                    # 若因 force_out / 清仓改变了目标，重算成本
                    costs = self.strategy.turnover_controller.estimate_trading_costs(
                        current_holdings=last_holdings,
                        new_holdings=new_holdings,
                        total_capital=equity,
                    )
                    reb_turnover = float(costs.get('turnover', 0.0))
                    reb_cost = float(costs.get('cost_pct', 0.0))
                    day_turnover += reb_turnover
                    day_cost += reb_cost
                    equity *= (1.0 - reb_cost)

                    new_holdings = self._attach_entry_meta(
                        new_holdings, last_holdings, last_prices, day_ohlcv, td
                    )

                    risk_info = summary['results'].get('risk_check', {}) or {}
                    trades.append({
                        'trade_date': td,
                        'turnover': day_turnover,
                        'cost_pct': day_cost,
                        'cost_amount': costs.get('cost_amount', 0.0),
                        'n_holdings': len(new_holdings),
                        'top_holding': new_holdings.iloc[0]['code'] if not new_holdings.empty else None,
                        'top_weight': float(new_holdings.iloc[0]['weight']) if not new_holdings.empty else 0.0,
                        'risk_status': risk_info.get('status', '-'),
                        'risk_issues': '; '.join(risk_info.get('issues', [])) if risk_info.get('issues') else '',
                        'position_scale': summary.get('statistics', {}).get('position_scale', 1.0),
                    })
                    holdings_log.append({
                        'trade_date': td,
                        'n_holdings': len(new_holdings),
                        'turnover': day_turnover,
                        'cost_pct': day_cost,
                        'holdings': new_holdings.to_dict(orient='records') if not new_holdings.empty else [],
                    })
                    # 成功路径：空仓也提交（主动清仓），不再保留旧仓
                    last_holdings, last_prices = self._commit_holdings(
                        new_holdings, last_prices, day_ohlcv, td
                    )
                    n_holdings_today = (
                        len(last_holdings) if last_holdings is not None else 0
                    )
                elif rebalance_status == "skip_no_factors":
                    # 数据缺失不计入坏环境连击
                    if verbose:
                        logger.info(f"  ⚠️ {td} 跳过再平衡 (status={rebalance_status})")
                elif verbose:
                    logger.info(f"  ⚠️ {td} 再平衡未产出信号 (status={rebalance_status})")
            elif use_adaptive:
                days_since_rebalance += 1

            equity_records.append(equity)
            net_return = (1.0 + day_return) * (1.0 - day_cost) - 1.0
            return_records.append(net_return)
            gross_return_records.append(day_return)
            turnover_records.append(day_turnover)
            cost_records.append(day_cost)

            if verbose:
                tag = "🔁" if do_rebalance else "  "
                msg = (
                    f"{tag} {td} | 净值 {equity / self.initial_capital:8.4f} | "
                    f"日收益 {net_return:+7.3%} | 毛收益 {day_return:+7.3%} | "
                    f"持仓 {n_holdings_today:3d} | 换手 {day_turnover:6.2%} | "
                    f"成本 {day_cost:6.3%}"
                )
                if do_rebalance and rebalance_status == 'success':
                    msg += f" | 再平衡✓"
                elif do_rebalance:
                    msg += f" | 再平衡✗({rebalance_status})"
                logger.info(msg)

        equity_curve = pd.Series(equity_records, index=trade_dates, name='equity') / self.initial_capital
        daily_returns = pd.Series(return_records, index=trade_dates, name='daily_return')
        gross_returns = pd.Series(gross_return_records, index=trade_dates, name='gross_return')
        turnover_series = pd.Series(turnover_records, index=trade_dates, name='turnover')
        cost_series = pd.Series(cost_records, index=trade_dates, name='cost')
        drawdown_curve = equity_curve / equity_curve.cummax() - 1.0

        metrics = self._compute_metrics(
            equity_curve, daily_returns, gross_returns,
            turnover_records, cost_records, len(trades), drawdown_curve,
        )
        config_snapshot = {
            'initial_capital': self.initial_capital,
            'fee_rate': self.fee_rate,
            'rebalance_freq': rebalance_freq,
            'adaptive_rebalance': use_adaptive,
            'min_holdings': opt_cfg.get('min_holdings'),
            'max_holdings': opt_cfg.get('max_holdings'),
            'max_weight_per_stock': opt_cfg.get('max_weight_per_stock'),
            'weighting': opt_cfg.get('weighting', 'score'),
            'buffer_threshold': tov_cfg.get('buffer_threshold', 0.15),
            'sectors': sectors,
            'max_stocks': max_stocks,
            'start_date': start_date,
            'end_date': end_date,
        }
        result = BacktestResult(
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            drawdown_curve=drawdown_curve,
            turnover_series=turnover_series,
            cost_series=cost_series,
            holdings_log=holdings_log,
            trades=trades,
            metrics=metrics,
            config_snapshot=config_snapshot,
        )
        if verbose:
            logger.info(result.summary())
        return result

    # ============================== 工具 ==============================

    @staticmethod
    def _buy_code_set(
        signals_df: Optional[pd.DataFrame], max_holdings: int
    ) -> set:
        if signals_df is None or signals_df.empty or "code" not in signals_df.columns:
            return set()
        df = signals_df
        if "signal_type" in df.columns:
            buy = df[df["signal_type"] == "buy"]
            if not buy.empty:
                return set(buy["code"].astype(str))
        if "rank" in df.columns:
            df = df.sort_values("rank", ascending=True)
        return set(df.head(max_holdings)["code"].astype(str))

    @staticmethod
    def _update_out_of_list_days(
        state: Dict[str, int],
        holdings: Optional[pd.DataFrame],
        buy_codes: set,
    ) -> Dict[str, int]:
        if holdings is None or holdings.empty:
            return {}
        held = set(holdings["code"].astype(str))
        new_state: Dict[str, int] = {}
        for code in held:
            if code in buy_codes:
                new_state[code] = 0
            else:
                new_state[code] = int(state.get(code, 0)) + 1
        return new_state

    def _handle_no_signal_exit(
        self,
        last_holdings: Optional[pd.DataFrame],
        bad_streak: int,
        flat_after_bad_days: int,
        action: str,
        reduce_scale: float,
        force_exit_codes: Optional[set] = None,
    ) -> tuple:
        """
        无信号日的仓位处理。
        Returns: (new_holdings, reason)
        """
        force_exit_codes = set(force_exit_codes or set())
        empty = pd.DataFrame(columns=["code", "weight", "score"])
        if last_holdings is None or last_holdings.empty:
            return empty, "flat_no_position"

        df = last_holdings.copy()
        if force_exit_codes:
            df = df[~df["code"].isin(force_exit_codes)].copy()

        if bad_streak >= max(int(flat_after_bad_days), 1):
            return empty, "flat_after_bad_days"

        if action == "flat":
            return empty, "flat_no_signals"

        if action == "hold":
            if df.empty:
                return empty, "flat_force_exit"
            return self.strategy.turnover_controller._normalize(df), "hold_no_signals"

        # reduce_then_flat：未达清仓阈值前按比例减仓
        scale = float(np.clip(reduce_scale, 0.0, 1.0))
        if df.empty:
            return empty, "flat_force_exit"
        df = df.copy()
        df["weight"] = df["weight"].astype(float) * scale
        # 不归一化回 1，保留现金
        return df.reset_index(drop=True), "reduce_no_signals"

    def _commit_holdings(
        self,
        new_holdings: pd.DataFrame,
        last_prices: Dict[str, float],
        day_ohlcv: Dict[str, pd.DataFrame],
        trade_date: str,
    ) -> tuple:
        """提交目标持仓（允许空仓）；同步 last_prices。

        修复 look-ahead bias：新买入的票不设置 close 作为基准价，
        让次日的「第一步」自动使用 open 作为买入价。
        只有延续持仓的票继续使用 close→close 收益。
        """
        if new_holdings is None or new_holdings.empty:
            return (
                pd.DataFrame(columns=["code", "weight", "score"]),
                {},
            )
        new_codes = set(new_holdings["code"].astype(str))
        # 只延续已有持仓的价格；新买入的票不加入 last_prices，
        # 次日「第一步」中 last_prices.get(code) 返回 None → 使用 open
        prices = {c: p for c, p in last_prices.items() if c in new_codes}
        return new_holdings, prices

    def _apply_hard_stops(
        self,
        holdings: pd.DataFrame,
        last_prices: Dict[str, float],
        trade_date: str,
        hard_stop_pct: float,
    ) -> tuple:
        """相对入场价止损；返回 (新持仓, 止损换手, 止损成本占比)。留现金不归一化。"""
        if holdings is None or holdings.empty or hard_stop_pct is None:
            return holdings, 0.0, 0.0
        if "entry_price" not in holdings.columns:
            return holdings, 0.0, 0.0

        keep_rows = []
        sold_w = 0.0
        for _, row in holdings.iterrows():
            code = row["code"]
            ep = row.get("entry_price")
            try:
                ep_f = float(ep)
            except (TypeError, ValueError):
                ep_f = float("nan")
            px = last_prices.get(code)
            if ep_f == ep_f and ep_f > 0 and px is not None and px > 0:
                ret = px / ep_f - 1.0
                if ret <= hard_stop_pct:
                    sold_w += float(row["weight"])
                    logger.info(
                        f"🛑 {trade_date} 硬止损 {code}: 入场后收益 {ret:.2%} "
                        f"≤ {hard_stop_pct:.2%}"
                    )
                    continue
            keep_rows.append(row)

        if sold_w <= 0:
            return holdings, 0.0, 0.0

        stop_turnover = 0.5 * sold_w  # 与 estimate_trading_costs 口径一致（单边卖出）
        # 卖出成本按卖出权重 * 单边费率
        stop_cost = sold_w * self.fee_rate
        if not keep_rows:
            return (
                pd.DataFrame(columns=list(holdings.columns)),
                stop_turnover,
                stop_cost,
            )
        out = pd.DataFrame(keep_rows).reset_index(drop=True)
        # 不把剩余权重归一化回 1，现金降低风险敞口
        return out, stop_turnover, stop_cost

    @staticmethod
    def _attach_entry_meta(
        new_holdings: pd.DataFrame,
        old_holdings: Optional[pd.DataFrame],
        last_prices: Dict[str, float],
        day_ohlcv: Dict[str, pd.DataFrame],
        trade_date: str,
    ) -> pd.DataFrame:
        """为持仓写入/继承 entry_date、entry_price。"""
        if new_holdings is None or new_holdings.empty:
            return new_holdings

        old_map: Dict[str, Dict[str, Any]] = {}
        if old_holdings is not None and not old_holdings.empty:
            for _, r in old_holdings.iterrows():
                old_map[str(r["code"])] = {
                    "entry_date": r.get("entry_date"),
                    "entry_price": r.get("entry_price"),
                }

        df = new_holdings.copy()
        entry_dates = []
        entry_prices = []
        for _, r in df.iterrows():
            code = str(r["code"])
            prev = old_map.get(code)
            if prev and prev.get("entry_date") and prev.get("entry_price") == prev.get("entry_price"):
                entry_dates.append(prev["entry_date"])
                entry_prices.append(prev["entry_price"])
                continue
            px = last_prices.get(code)
            if px is None or px <= 0:
                odf = day_ohlcv.get(code)
                if odf is not None and not odf.empty:
                    rec = Backtester._row_for_date(odf, trade_date)
                    if rec is not None:
                        px = float(rec.get("close", np.nan))
            entry_dates.append(trade_date)
            entry_prices.append(px if px is not None else np.nan)
        df["entry_date"] = entry_dates
        df["entry_price"] = entry_prices
        return df

    @staticmethod
    def _gen_trade_dates(start_date: str, end_date: str) -> List[str]:
        """生成日期列表，简单按工作日过滤（周一~周五）。"""
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        out: List[str] = []
        d = start
        while d <= end:
            if d.weekday() < 5:
                out.append(d.strftime('%Y-%m-%d'))
            d += timedelta(days=1)
        return out

    @staticmethod
    def _row_for_date(df: pd.DataFrame, td: str) -> Optional[Dict[str, Any]]:
        if 'trade_date' not in df.columns:
            return None
        match = df[df['trade_date'] == td]
        if match.empty:
            return None
        return match.iloc[-1].to_dict()

    @staticmethod
    def _compute_metrics(
        equity_curve: pd.Series,
        daily_returns: pd.Series,
        gross_returns: pd.Series,
        turnover: List[float],
        costs: List[float],
        rebalance_count: int,
        drawdown_curve: pd.Series,
    ) -> Dict[str, float]:
        n = len(daily_returns)
        if n == 0:
            return {}
        total_return = float(equity_curve.iloc[-1] - 1.0)
        years = max(n / 252.0, 1e-6)
        annualized_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1 else -1.0
        std = float(daily_returns.std()) if daily_returns.std() > 0 else 0.0
        vol = std * float(np.sqrt(252))
        sharpe = float(daily_returns.mean() / std * np.sqrt(252)) if std > 0 else 0.0
        downside = daily_returns[daily_returns < 0]
        downside_std = float(downside.std()) if len(downside) > 1 and downside.std() > 0 else 0.0
        downside_vol = downside_std * float(np.sqrt(252))
        sortino = float(daily_returns.mean() / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0
        max_dd = float(drawdown_curve.min()) if len(drawdown_curve) else 0.0
        calmar = float(annualized_return / abs(max_dd)) if max_dd < 0 else 0.0

        # 最长回撤天数
        underwater = drawdown_curve < 0
        max_dd_duration = 0
        cur = 0
        for v in underwater.values:
            if v:
                cur += 1
                max_dd_duration = max(max_dd_duration, cur)
            else:
                cur = 0

        win_rate = float((daily_returns > 0).mean()) if n else 0.0
        wins = daily_returns[daily_returns > 0]
        losses = daily_returns[daily_returns < 0]
        avg_win = float(wins.mean()) if len(wins) else 0.0
        avg_loss = float(losses.mean()) if len(losses) else 0.0
        profit_loss_ratio = float(avg_win / abs(avg_loss)) if avg_loss < 0 else 0.0

        return {
            'rebalance_count': rebalance_count,
            'total_return': total_return,
            'total_return_gross': float((1.0 + gross_returns).prod() - 1.0),
            'annualized_return': annualized_return,
            'annualized_vol': vol,
            'sharpe': sharpe,
            'sortino': sortino,
            'calmar': calmar,
            'max_drawdown': max_dd,
            'max_drawdown_duration': int(max_dd_duration),
            'downside_vol': downside_vol,
            'win_rate': win_rate,
            'profit_loss_ratio': profit_loss_ratio,
            'best_day': float(daily_returns.max()) if n else 0.0,
            'worst_day': float(daily_returns.min()) if n else 0.0,
            'avg_turnover': float(np.mean(turnover)) if turnover else 0.0,
            'max_turnover': float(np.max(turnover)) if turnover else 0.0,
            'total_cost': float(np.sum(costs)),
        }


# ============================== 便捷数据 provider ==============================

def make_ohlcv_provider_from_dict(
    ohlcv_by_code: Dict[str, pd.DataFrame],
) -> Callable[[str], Dict[str, pd.DataFrame]]:
    """
    把"全市场静态 OHLCV"封装成一个 provider 函数。
    回测器每个交易日会调一次该函数；这里直接返回完整 dict（回测器内部自己按 trade_date 取行）。
    首次调用时按 trade_date 预建索引，后续调用 O(1) 查找。
    """
    _indexed_cache: Dict[str, Dict[str, pd.DataFrame]] = {}

    def _provider(td: str) -> Dict[str, pd.DataFrame]:
        if td not in _indexed_cache:
            result = {}
            for code, df in ohlcv_by_code.items():
                if 'trade_date' in df.columns:
                    row = df[df['trade_date'] == td]
                    if not row.empty:
                        result[code] = row
            _indexed_cache[td] = result
        return _indexed_cache[td]
    return _provider


def make_signal_provider_from_pipeline_data(
    data_by_date: Dict[str, Dict[str, Any]],
) -> Callable[[str], Optional[Dict[str, Any]]]:
    """每个交易日返回一个预构造好的 run_pipeline 入参字典。"""
    def _provider(td: str) -> Optional[Dict[str, Any]]:
        return data_by_date.get(td)
    return _provider


if __name__ == "__main__":
    sys.exit(asyncio.run(Backtester.run_cli()))
