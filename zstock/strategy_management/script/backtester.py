"""
日频截面策略回测器

定位：
- 配合 StrategyPipeline 把"每个交易日的目标持仓"按日次再平衡跑出净值曲线；
- 不依赖 Qlib，纯 pandas/numpy 实现；
- 输入既支持真实 OHLCV（从 query_service 拉），也支持离线注入。

回测假设：
- T 日生成信号，按 T+1 开盘价建仓 / 调仓；当日收益用 T+1 的开盘到收盘；
- 后续持仓日的收益用每日 close→close；
- 成本：买入腿 fee_rate，卖出腿 fee_rate + stamp_tax（印花税）；
  债基/黄金等 fee_free_codes 买卖成本为 0；
- 硬止损按当日 low 触发，成交价取 open（跳空）或止损价；收盘封跌停或停牌卖不出；
- 开盘封涨停买不进。当日买入的份额当天不能卖。加仓只吃开盘到收盘，不吃隔夜缺口；
- 仍未建模：整手、冲击成本。

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
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import (
    is_equity_etf,
    is_limit_down,
    is_limit_up,
    normalize_code,
    normalize_date,
)
from zstock.common.book_codes import BOOK_OHLCV_CODES, INDEX_CODE, is_t0
from zstock.strategy_management.market_book import (
    holdings_frame,
    needs_book_trade,
    park_treasury,
    weight_map,
)

import logging

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.strategy_section import exit as exit_policy
from zstock.strategy_management.strategy_section.execute import extract_market_grade
from zstock.strategy_management.pipeline import StrategyPipeline, _state_for_grade
from zstock.common.config.strategy_config import (
    load_strategy_params,
    resolve_runtime_config,
)


# ─────────────────────────────────────────────────────────────────
# 配置加载：唯一入口 resolve_runtime_config（strategy_config SSOT）
# ─────────────────────────────────────────────────────────────────


def load_runtime_config(
    override: Optional[Dict] = None,
    *,
    base_params: Optional[Dict] = None,
) -> Dict[str, Dict]:
    """回测脚本用的运行态配置，委托 strategy_config.resolve_runtime_config。"""
    return resolve_runtime_config(override, base_params=base_params)


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
        signal_pipeline=None,
    ):
        if strategy_pipeline is not None:
            self.strategy = strategy_pipeline
            # 允许打分用 duck replay、执行为完整 StrategyPipeline
            self.signal_pipeline = (
                signal_pipeline if signal_pipeline is not None else strategy_pipeline
            )
        elif signal_pipeline is not None and hasattr(signal_pipeline, "execute_full_pipeline"):
            self.strategy = signal_pipeline
            self.signal_pipeline = signal_pipeline
        elif signal_pipeline is not None:
            # test duck: only scoring
            self.signal_pipeline = signal_pipeline
            self.strategy = StrategyPipeline(signals=signal_pipeline)
        else:
            p = StrategyPipeline()
            self.strategy = p
            self.signal_pipeline = p
        bt_cfg = (load_strategy_params() or {}).get("backtest") or {}
        if fee_rate is None or initial_capital is None:
            fee_rate = fee_rate if fee_rate is not None else float(bt_cfg.get("fee_rate", 0.0015))
            initial_capital = (
                initial_capital
                if initial_capital is not None
                else float(bt_cfg.get("initial_capital", 1e7))
            )
        self.fee_rate = fee_rate
        self.stamp_tax = float(bt_cfg.get("stamp_tax", 0.0005))
        self.initial_capital = initial_capital
        self.strategy._turnover_controller.fee_rate = fee_rate
        self.strategy._turnover_controller.stamp_tax = self.stamp_tax
        logger.info(
            f"✅ Backtester 初始化完成: fee={fee_rate} stamp={self.stamp_tax} "
            f"init_capital={initial_capital}"
        )

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
    def _resolve_rebalance_freq(
        cfg: Optional[Dict[str, Any]],
        *,
        regime: str = "neutral",
        market_grade: str = "green",
        default_freq: int = 5,
    ) -> int:
        """根据 regime 与灯号决定下一次再平衡间隔（交易日）。"""
        if not cfg or not cfg.get("enabled"):
            return max(1, int(default_freq))

        base = int(cfg.get("default_freq", default_freq))
        by_regime = cfg.get("by_regime") or {}
        by_grade = cfg.get("by_market_grade") or {}

        r_freq = int(by_regime.get(regime, by_regime.get("neutral", base)))
        g_freq = int(by_grade.get(market_grade, by_grade.get("green", base)))

        if cfg.get("use_min_freq", True):
            freq = min(r_freq, g_freq)
        else:
            freq = max(r_freq, g_freq)

        lo = int(cfg.get("min_freq", 3))
        hi = int(cfg.get("max_freq", 8))
        return max(lo, min(hi, freq))

    @staticmethod
    def _compute_drawdown_scale(
        equity_series: List[float],
        cfg: Optional[Dict[str, Any]],
    ) -> float:
        """根据窗口内峰值回撤计算仓位乘数。

        单档：drawdown_threshold + scale_factor。
        多档：tiers=[{drawdown_threshold, scale_factor}, ...]，命中最深档（scale 取最小）。
        lookback_days<=0 表示用全部净值序列当峰值窗口。
        """
        root = cfg or {}
        if not root.get("enabled", False):
            return 1.0
        dt = root.get("drawdown_throttle") or {}
        if not dt.get("enabled", False):
            return 1.0
        if not equity_series:
            return 1.0

        lookback = int(dt.get("lookback_days", 20))
        window = equity_series[-lookback:] if lookback > 0 else equity_series
        peak = max(window)
        current = equity_series[-1]
        if peak <= 0:
            return 1.0
        dd = current / peak - 1.0

        tiers = dt.get("tiers") or []
        if tiers:
            scale = 1.0
            hit = False
            for tier in tiers:
                threshold = abs(float(tier.get("drawdown_threshold", 0.0)))
                if threshold <= 0:
                    continue
                if dd <= -threshold:
                    hit = True
                    scale = min(scale, float(tier.get("scale_factor", 1.0)))
            if hit:
                return max(0.0, min(1.0, scale))
            return 1.0

        threshold = abs(float(dt.get("drawdown_threshold", 0.10)))
        scale = float(dt.get("scale_factor", 0.7))
        if dd <= -threshold:
            return max(0.0, min(1.0, scale))
        return 1.0

    @staticmethod
    def _compute_vol_scale(
        equity_series: List[float],
        cfg: Optional[Dict[str, Any]],
    ) -> float:
        """按实现波动缩放仓位：scale=clip(target/realized, floor, cap)。

        地板默认 0.25，不会因波动目标空仓。样本不足或波动近 0 时返回 1.0。
        """
        vt = (cfg or {}).get("vol_target") or {}
        if not vt.get("enabled", False):
            return 1.0
        if len(equity_series) < 3:
            return 1.0
        lookback = int(vt.get("lookback_days", 40))
        target = float(vt.get("target_vol", 0.12))
        floor = float(vt.get("scale_floor", 0.25))
        ceiling = min(1.0, float(vt.get("scale_cap", 1.0)))
        min_obs = int(vt.get("min_obs", 20))
        rets: List[float] = []
        for i in range(1, len(equity_series)):
            prev = float(equity_series[i - 1])
            if prev > 0:
                rets.append(float(equity_series[i]) / prev - 1.0)
        window = rets[-lookback:] if lookback > 0 else rets
        if len(window) < min_obs:
            return 1.0
        std = float(np.std(window, ddof=1)) if len(window) > 1 else 0.0
        realized = std * float(np.sqrt(252))
        if realized <= 1e-8 or target <= 0:
            return 1.0
        return max(floor, min(ceiling, target / realized))

    @staticmethod
    def _configure_cli_logging() -> None:
        from zstock.common.logging_setup import setup_logging

        setup_logging("backtester", console_fmt="%(message)s")
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
        ohlcv_query_concurrency: Optional[int] = None,
        ohlcv_batch_size: Optional[int] = None,
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

        if use_precomputed_factors and self.signal_pipeline is None:
            self.signal_pipeline = StrategyPipeline()

        qs = get_data_query_service()

        if verbose:
            logger.info("📦 加载 OHLCV 数据...")

        all_stocks_docs, _ = await qs.get_all_stocks()
        info_by_code = {
            normalize_code(d["code"]): d for d in all_stocks_docs if d.get("code")
        }
        # 区间内真实有行情的代码 ∩ 主板非 ST，减轻「用今日股票池回测历史」的幸存者偏差
        traded_in_range: List[str] = []
        getter = getattr(qs, "get_traded_codes_between", None)
        if callable(getter):
            traded_in_range = await getter(start_date, end_date)
        start_norm = normalize_date(start_date)
        end_norm = normalize_date(end_date)

        def _alive_in_window(info: Dict[str, Any]) -> bool:
            if not info.get("is_mainboard"):
                return False
            listed = info.get("list_date")
            delisted = info.get("delist_date")
            try:
                if listed and normalize_date(str(listed)) > end_norm:
                    return False
            except (TypeError, ValueError):
                pass
            try:
                if delisted and normalize_date(str(delisted)) < start_norm:
                    return False
            except (TypeError, ValueError):
                pass
            return True

        # 不用今天的 ST 标记删历史行情。后来被 ST 的票若从样本里消失，会抬高回测。
        if traded_in_range:
            mainboard_codes = [
                c for c in traded_in_range if _alive_in_window(info_by_code.get(c, {}))
            ]
        else:
            mainboard_codes = [
                normalize_code(str(d["code"]))
                for d in all_stocks_docs
                if d.get("code") and _alive_in_window(d)
            ]
        if verbose:
            logger.info(f"   主板(区间有行情，含后来 ST): {len(mainboard_codes)} 只")

        # universe_mode 只影响信号漏斗；OHLCV 始终全主板（测评宇宙收窄在 test 侧）。
        # Book 账户还需要指数 / 行业 ETF / 债基日线，一并加载。
        book_codes = [str(c) for c in BOOK_OHLCV_CODES]
        codes_to_load = list(dict.fromkeys([*mainboard_codes, *book_codes]))

        ohlcv_data: Dict[str, pd.DataFrame] = {}
        chunk_size = 500
        import os

        cpu = os.cpu_count() or 4
        if ohlcv_batch_size is None:
            ohlcv_batch_size = int(os.environ.get("ZSTOCK_OHLCV_BATCH", "80"))
        if ohlcv_query_concurrency is None:
            ohlcv_query_concurrency = int(
                os.environ.get("ZSTOCK_OHLCV_CONCURRENCY", str(min(12, max(6, cpu))))
            )
        ohlcv_batch_size = max(10, int(ohlcv_batch_size))
        ohlcv_query_concurrency = max(1, int(ohlcv_query_concurrency))
        total_chunks = max(1, (len(codes_to_load) + chunk_size - 1) // chunk_size)
        failed_chunks = 0

        for ci, i in enumerate(range(0, len(codes_to_load), chunk_size), 1):
            chunk = codes_to_load[i : i + chunk_size]
            if verbose:
                logger.info(f"   OHLCV [{ci}/{total_chunks}] {len(chunk)} 只...")
            try:
                warm_days = max(int(lookback_days or 0), 250)
                load_start = (
                    datetime.strptime(start_norm, "%Y-%m-%d") - timedelta(days=warm_days)
                ).strftime("%Y-%m-%d")
                batch = await qs.get_ohlcv_batch(
                    chunk,
                    load_start,
                    end_date,
                    batch_size=ohlcv_batch_size,
                    query_concurrency=ohlcv_query_concurrency,
                )
                if batch:
                    for code, df in batch.items():
                        code_s = normalize_code(str(code))
                        if "trade_date" in df.columns:
                            df = df.copy()
                            df["trade_date"] = df["trade_date"].map(normalize_date)
                        ohlcv_data[code_s] = _slim_ohlcv_frame(df)
            except Exception as e:
                logger.warning(f"   ⚠️ 批次 {ci} 加载失败（跳过）: {e}")
                failed_chunks += 1

        if verbose:
            logger.info(
                f"✅ OHLCV 加载完成: {len(ohlcv_data)} 只 "
                f"(失败批次: {failed_chunks}/{total_chunks})"
            )

        if not ohlcv_data:
            raise RuntimeError("无 OHLCV 数据，无法回测")

        self._ohlcv_full = ohlcv_data
        ohlcv_provider = make_ohlcv_provider_from_dict(ohlcv_data)

        if verbose:
            logger.info("🚀 回测开始...")

        try:
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
        finally:
            try:
                import gc

                gc.collect()
            except Exception:
                pass

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
                直接喂进信号计算（离线/确定性回测用）。None 时走
                StrategyPipeline 默认的真实数据拉取。
            rebalance_freq: 每隔几个交易日再平衡一次（1=日频）。
            strategy_config: 透传给 StrategyPipeline 的 config。
            sectors / max_stocks: 透传。
            verbose: True 时每个交易日都打印日志。
            use_precomputed_factors: True 时用 signal_pipeline.score_signals 读预计算因子。

        Returns:
            BacktestResult
        """
        trade_dates = self._resolve_trade_dates(start_date, end_date)
        if not trade_dates:
            raise ValueError(f"start_date={start_date} ~ end_date={end_date} 无可用交易日")

        tc = getattr(self.strategy, "_turnover_controller", None)
        if tc is not None and hasattr(tc, "set_trading_calendar"):
            tc.set_trading_calendar(trade_dates)
        if tc is not None:
            tc.fee_rate = self.fee_rate
            tc.stamp_tax = float(getattr(self, "stamp_tax", getattr(tc, "stamp_tax", 0.0005)))

        # 显式 strategy_config 时同步进 pipeline，避免信号读 A、执行读 B
        if strategy_config:
            seen: set[int] = set()
            for pipe in (self.signal_pipeline, self.strategy):
                if pipe is None or id(pipe) in seen:
                    continue
                seen.add(id(pipe))
                apply = getattr(pipe, "_apply_config_override", None)
                if callable(apply):
                    apply(strategy_config)
        # 与 strategy 实例同源：未显式传入时用 pipeline.config 派生
        pipe_params = getattr(self.strategy, "config", None) or None
        cfg_runtime = load_runtime_config(strategy_config, base_params=pipe_params)
        opt_cfg = (cfg_runtime.get('portfolio_optimization') or {})
        tov_cfg = (cfg_runtime.get('turnover_control') or {})
        _adaptive_banner = bool((cfg_runtime.get("adaptive_rebalance") or {}).get("enabled"))
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
        exit_cfg = cfg_runtime.get("exit_rules") or {}
        hard_stop_pct = float(
            exit_cfg.get(
                "hard_stop_loss_pct",
                (cfg_runtime.get("risk_management") or {}).get("hard_stop_loss_pct", -0.08),
            )
        )
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
        refresh_satellites = True
        last_industry: Optional[Dict[str, float]] = None
        last_low_vol: Optional[Dict[str, float]] = None
        last_regime = "neutral"
        last_market_grade = "green"
        book_cfg = cfg_runtime.get("book_regime") or {}
        book_on = bool(book_cfg.get("enabled", False))
        book_band = float(book_cfg.get("rebalance_band", 0.03))
        book_min_util = float(book_cfg.get("min_utilization", 0.90))
        book_bond = str(book_cfg.get("bond_code") or "511260")
        industry_stop_pct = float(book_cfg.get("industry_stop_loss_pct", 0.0) or 0.0)
        industry_codes = {
            normalize_code(str(c))
            for c in (book_cfg.get("industry_etfs") or [])
        }
        index_rows: List[dict] = []
        asset_hist: Dict[str, List[float]] = {}
        prev_book_state: Optional[str] = None

        self._warmup_book_history(index_rows, asset_hist, trade_dates[0])

        for i, td in enumerate(trade_dates):
            day_ohlcv = ohlcv_provider(td) or {}
            nav_for_book = equity

            # === 第一步：用昨日持仓定价；新仓按开盘成交并写入 entry_price ===
            day_return = 0.0
            stocks_priced = 0
            day_cost = 0.0
            day_turnover = 0.0
            name_rets: Dict[str, float] = {}
            equity_at_open = equity
            if last_holdings is not None and not last_holdings.empty:
                last_holdings, name_rets, bases, last_prices, stocks_priced = (
                    self._mark_to_market_and_fill_entries(
                        last_holdings, last_prices, day_ohlcv, td
                    )
                )
                (
                    last_holdings,
                    day_return,
                    stop_turnover,
                    stop_cost,
                    last_prices,
                ) = self._apply_hard_stops(
                    last_holdings,
                    last_prices,
                    name_rets,
                    bases,
                    day_ohlcv,
                    td,
                    hard_stop_pct,
                    industry_stop_pct=industry_stop_pct,
                    industry_codes=industry_codes,
                    enabled=not book_on,
                )
                if stop_cost > 0:
                    day_cost += stop_cost
                    day_turnover += stop_turnover

            equity = equity * (1.0 + day_return)
            if day_cost > 0:
                equity *= 1.0 - day_cost

            prev_td = trade_dates[i - 1] if i > 0 else None
            if (
                not book_on
                and last_holdings is not None
                and not last_holdings.empty
            ):
                last_holdings, tr_t, tr_c = self._apply_trend_exits(
                    last_holdings,
                    td,
                    prev_td,
                    exit_cfg,
                    regime=last_regime,
                )
                if tr_c > 0:
                    equity *= (1.0 - tr_c)
                    day_cost += tr_c
                    day_turnover += tr_t

            if last_holdings is not None and not last_holdings.empty:
                last_holdings = self._drift_weights(last_holdings, name_rets, day_return)

            # === 第二步：决定今天要不要再平衡 ===
            if use_adaptive:
                do_rebalance = days_since_rebalance >= target_freq
            else:
                do_rebalance = (i % rebalance_freq == 0)
            rebalance_status = '-'
            n_holdings_today = len(last_holdings) if last_holdings is not None else 0
            decision = None
            if book_on:
                index_closes = [float(row["close"]) for row in index_rows]
                today_bar = _day_bar_frame(day_ohlcv, INDEX_CODE)
                if today_bar is not None and not getattr(today_bar, "empty", True):
                    try:
                        today_px = float(today_bar.iloc[-1].get("close"))
                    except (TypeError, ValueError):
                        today_px = float("nan")
                    if today_px > 0:
                        index_closes.append(today_px)
                held_codes: List[str] = []
                if last_holdings is not None and not last_holdings.empty and "code" in last_holdings.columns:
                    held_codes = [str(code) for code in last_holdings["code"].tolist()]
                decision = await self.strategy.note_market_day(
                    td,
                    index_closes=index_closes,
                    asset_closes=asset_hist,
                    held=held_codes,
                    nav=nav_for_book,
                    confirm_green_days=int(book_cfg.get("confirm_green_days", 2)),
                )
                light_state = _state_for_grade(decision.grade)
                state_changed = light_state != prev_book_state
                prev_book_state = light_state
                # 截面、行业 ETF、低波都按当天信号重算。
                refresh_satellites = True
                do_rebalance = True

            if do_rebalance:
                precomputed_signals = None
                if use_precomputed_factors and self.signal_pipeline:
                    # ── 快速路径：从 MongoDB 读预计算信号 ──
                    # 缺日（节假日/未预计算）跳过再平衡，继续持有，不中断整段回测
                    try:
                        precomputed_signals = await self.signal_pipeline.score_signals(td)
                    except ValueError as e:
                        logger.warning(f"⚠️ {td} 无预计算因子，跳过再平衡: {e}")
                        summary = {
                            "status": "skip_no_factors",
                            "error": str(e),
                        }
                    else:
                        extra = self._pipeline_market_kwargs(td, prev_td, last_holdings)
                        if book_on:
                            extra = {
                                **extra,
                                "index_bars": list(index_rows),
                                "index_closes": [
                                    float(r["close"]) for r in index_rows
                                ],
                                "asset_closes": dict(asset_hist),
                            }
                        if book_on and decision is not None:
                            extra = {**extra, "book_decision": decision}
                        if book_on:
                            extra["respect_book_rebalance_band"] = True
                            if (
                                not refresh_satellites
                                and last_industry is not None
                                and last_low_vol is not None
                            ):
                                extra["industry_weights"] = last_industry
                                extra["low_vol_weights"] = last_low_vol
                        summary = await self.strategy.execute_full_pipeline(
                            trade_date=td,
                            current_positions=last_holdings,
                            total_capital=equity,
                            config=cfg_runtime,
                            precomputed_signals=precomputed_signals,
                            **extra,
                        )
                else:
                    # ── 原有路径：实时计算 ──
                    prebuilt = signal_provider(td) if signal_provider else None
                    try:
                        extra = self._pipeline_market_kwargs(td, prev_td, last_holdings)
                        if book_on:
                            extra = {
                                **extra,
                                "index_bars": list(index_rows),
                                "index_closes": [
                                    float(r["close"]) for r in index_rows
                                ],
                                "asset_closes": dict(asset_hist),
                            }
                            if decision is not None:
                                extra["book_decision"] = decision
                            extra["respect_book_rebalance_band"] = True
                            if (
                                not refresh_satellites
                                and last_industry is not None
                                and last_low_vol is not None
                            ):
                                extra["industry_weights"] = last_industry
                                extra["low_vol_weights"] = last_low_vol
                        summary = await self.strategy.execute_full_pipeline(
                            trade_date=td,
                            current_positions=last_holdings,
                            total_capital=equity,
                            config=cfg_runtime,
                            sectors=sectors,
                            max_stocks=max_stocks,
                            prebuilt_data=prebuilt,
                            **extra,
                        )
                    except Exception as e:
                        logger.error(f"{td} strategy pipeline 失败: {e}")
                        summary = {'status': 'failed', 'error': str(e)}

                rebalance_status = summary.get('status', 'failed')
                market_grade = summary.get('market_grade') or (
                    extract_market_grade(precomputed_signals)
                    if precomputed_signals is not None
                    else "unknown"
                )
                if precomputed_signals is not None and hasattr(precomputed_signals, "attrs"):
                    last_regime = str(precomputed_signals.attrs.get("regime", last_regime))
                elif summary.get("regime"):
                    last_regime = str(summary.get("regime"))
                last_market_grade = market_grade if market_grade != "unknown" else last_market_grade
                if use_adaptive:
                    target_freq = self._resolve_rebalance_freq(
                        adaptive_cfg,
                        regime=last_regime,
                        market_grade=last_market_grade,
                        default_freq=rebalance_freq,
                    )
                if refresh_satellites and rebalance_status == "success":
                    cached = (summary.get("results") or {}) if isinstance(summary, dict) else {}
                    if cached.get("industry_weights") is not None:
                        last_industry = cached.get("industry_weights")
                    if cached.get("low_vol_weights") is not None:
                        last_low_vol = cached.get("low_vol_weights")
                    days_since_rebalance = 0
                elif book_on:
                    days_since_rebalance += 1

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
                    force_out -= _unsellable_codes(last_holdings, td)

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
                    costs = self.strategy._turnover_controller.estimate_trading_costs(
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
                        "book_state": decision.state if decision is not None else "",
                        "market_grade": market_grade,
                        "regime": last_regime,
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
                    # 灯开着时目标仓已经是三策略合成结果，不再按股票候选名单改仓。
                    dropped = self._drop_out_of_candidates(
                        new_holdings, force_out, book_on=book_on
                    )
                    if dropped is not new_holdings and len(dropped) < len(new_holdings):
                        logger.info(
                            f"🚪 {td} 连续{out_of_cand_days}日跌出候选，剔除 "
                            f"{sorted(force_out)}"
                        )
                        new_holdings = dropped
                        if not new_holdings.empty:
                            new_holdings = self._clip_weights(new_holdings)
                    # 若仍处于坏环境连击且达到清仓阈值（防御：成功但红灯）
                    if (
                        not book_on
                        and bad_streak >= flat_after_bad_days
                        and market_grade == "red"
                    ):
                        new_holdings = pd.DataFrame(columns=["code", "weight", "score"])
                        logger.info(
                            f"🛑 {td} 连续{bad_streak}日坏环境，强制清仓"
                        )

                    if not book_on:
                        wrp = cfg_runtime.get("weak_regime_protection")
                        eq_path = equity_records + [equity]
                        dd_scale = self._compute_drawdown_scale(eq_path, wrp)
                        vol_scale = self._compute_vol_scale(eq_path, wrp)
                        risk_scale = dd_scale * vol_scale
                        if (
                            abs(risk_scale - 1.0) > 1e-9
                            and new_holdings is not None
                            and not new_holdings.empty
                        ):
                            new_holdings = new_holdings.copy()
                            new_holdings["weight"] = (
                                new_holdings["weight"].astype(float) * risk_scale
                            )
                            logger.info(
                                f"📉 {td} 风险缩放 dd={dd_scale:.2f} vol={vol_scale:.2f} "
                                f"敞口={new_holdings['weight'].sum():.2%}"
                            )

                    new_holdings = self._enforce_t1(last_holdings, new_holdings, td)
                    costs = summary['results'].get('trading_costs', {})
                    # 若因 force_out / 清仓 / T+1 锁仓改变了目标，重算成本
                    costs = self.strategy._turnover_controller.estimate_trading_costs(
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
                        'book_state': summary.get('book_state') or (decision.state if decision is not None else ''),
                        'market_grade': summary.get('market_grade') or '',
                        'regime': summary.get('regime') or last_regime,
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

            _append_book_bars(index_rows, asset_hist, day_ohlcv)
            # 空仓也 park：主动清仓后补国债，避免现金闲置
            if book_on and last_holdings is not None:
                parked = park_treasury(last_holdings, book_bond)
                if abs(float(parked["weight"].sum()) - float(last_holdings["weight"].sum())) > 1e-8:
                    fill_costs = self.strategy._turnover_controller.estimate_trading_costs(
                        current_holdings=last_holdings,
                        new_holdings=parked,
                        total_capital=equity,
                    )
                    fill_cost = float(fill_costs.get("cost_pct", 0.0))
                    equity *= 1.0 - fill_cost
                    day_cost += fill_cost
                    day_turnover += float(fill_costs.get("turnover", 0.0))
                    last_holdings = parked

            equity_records.append(equity)
            net_return = (
                equity / equity_at_open - 1.0 if equity_at_open > 0 else day_return
            )
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
    def _drop_out_of_candidates(
        holdings: Optional[pd.DataFrame],
        force_out: set,
        *,
        book_on: bool,
    ) -> pd.DataFrame:
        """截面回测才按股票候选名单剔除。三策略账户的目标仓不再改。"""
        if (
            book_on
            or not force_out
            or holdings is None
            or holdings.empty
            or "code" not in holdings.columns
        ):
            return holdings if holdings is not None else pd.DataFrame()
        return holdings[~holdings["code"].isin(force_out)].copy()

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
            return self._clip_weights(df), "hold_no_signals"

        # reduce_then_flat：未达清仓阈值前按比例减仓
        scale = float(np.clip(reduce_scale, 0.0, 1.0))
        if df.empty:
            return empty, "flat_force_exit"
        df = df.copy()
        df["weight"] = df["weight"].astype(float) * scale
        # 不归一化回 1，保留现金
        return df.reset_index(drop=True), "reduce_no_signals"

    @staticmethod
    def _clip_weights(df: pd.DataFrame) -> pd.DataFrame:
        """权重和>1 时向下归一，保留现金。"""
        if df is None or df.empty or "weight" not in df.columns:
            return df
        out = df.copy()
        total = float(out["weight"].sum())
        if total > 1.0 + 1e-9:
            out["weight"] = out["weight"] / total
        return out.reset_index(drop=True)

    def _enforce_t1(
        self,
        current: Optional[pd.DataFrame],
        target: pd.DataFrame,
        trade_date: str,
    ) -> pd.DataFrame:
        """股票和股票 ETF 当天买入的份额当天不能卖。国债和黄金不受这条限制。"""
        if current is None or current.empty:
            return target
        td = normalize_date(trade_date)
        floors: Dict[str, float] = {}
        meta: Dict[str, Dict[str, Any]] = {}
        for _, row in current.iterrows():
            code = normalize_code(str(row["code"]))
            weight = float(row.get("weight") or 0.0)
            meta[code] = row.to_dict()
            if is_t0(code) or weight <= 1e-8:
                continue
            entry = str(row.get("entry_date") or "")[:10]
            locked = float(row.get("locked_weight") or 0.0)
            floor = 0.0
            if entry == td:
                floor = weight
            elif locked > 1e-8:
                floor = min(locked, weight)
            if floor > 1e-8:
                floors[code] = floor
        if not floors:
            return target
        desired: Dict[str, float] = {}
        rows_by_code: Dict[str, Dict[str, Any]] = {}
        if target is not None and not target.empty:
            for _, row in target.iterrows():
                code = normalize_code(str(row["code"]))
                desired[code] = float(row.get("weight") or 0.0)
                rows_by_code[code] = row.to_dict()
        for code, floor in floors.items():
            desired[code] = max(desired.get(code, 0.0), floor)
            if code not in rows_by_code:
                rows_by_code[code] = meta[code]
        total = sum(desired.values())
        if total > 1.0 + 1e-8:
            flexible = {c: desired[c] - floors.get(c, 0.0) for c in desired}
            flex_sum = sum(max(0.0, v) for v in flexible.values())
            excess = total - 1.0
            if flex_sum > 1e-8:
                keep = max(0.0, flex_sum - excess) / flex_sum
                for code in desired:
                    desired[code] = floors.get(code, 0.0) + max(0.0, flexible[code]) * keep
        out_rows = []
        for code, weight in desired.items():
            if weight <= 1e-8:
                continue
            row = dict(rows_by_code.get(code) or {"code": code, "score": 0.0})
            row["code"] = code
            row["weight"] = weight
            out_rows.append(row)
        if not out_rows:
            return target
        return pd.DataFrame(out_rows).reset_index(drop=True)

    def _drift_weights(
        self,
        holdings: pd.DataFrame,
        name_rets: Dict[str, float],
        day_return: float,
    ) -> pd.DataFrame:
        """持仓权重随当日涨跌漂移。没有再平衡的日子不再按目标权重免费再平衡。"""
        if holdings is None or holdings.empty:
            return holdings
        denom = 1.0 + float(day_return)
        if denom <= 1e-8:
            return holdings
        out = holdings.copy()
        weights = []
        locked_out = []
        for _, row in out.iterrows():
            code = normalize_code(str(row["code"]))
            old_w = float(row["weight"])
            factor = (1.0 + float(name_rets.get(code, 0.0))) / denom
            weights.append(old_w * factor)
            locked = float(row.get("locked_weight") or 0.0)
            locked_out.append(locked * factor)
        out["weight"] = weights
        out["basis_weight"] = out["weight"]
        out["locked_weight"] = locked_out
        return out

    def _warmup_book_history(
        self,
        index_rows: List[dict],
        asset_hist: Dict[str, List[float]],
        first_trade_date: str,
    ) -> None:
        """回测起点之前的指数和 ETF 收盘先喂给账户，均线防守才有 120 日历史。"""
        full = getattr(self, "_ohlcv_full", None) or {}
        if not full:
            return
        first = normalize_date(first_trade_date)
        dates = set()
        frames: Dict[str, pd.DataFrame] = {}
        for code in BOOK_OHLCV_CODES:
            df = full.get(code)
            if df is None or getattr(df, "empty", True) or "trade_date" not in df.columns:
                continue
            frames[code] = df
            for raw in df["trade_date"].tolist():
                nd = normalize_date(str(raw))
                if nd < first:
                    dates.add(nd)
        for td in sorted(dates):
            day: Dict[str, pd.DataFrame] = {}
            for code, df in frames.items():
                mask = df["trade_date"].map(lambda x: normalize_date(str(x))) == td
                hit = df.loc[mask]
                if not hit.empty:
                    day[code] = hit
            if day:
                _append_book_bars(index_rows, asset_hist, day)

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
        holdings = new_holdings.copy()
        holdings["code"] = holdings["code"].map(lambda c: normalize_code(str(c)))
        new_codes = set(holdings["code"].astype(str))
        # 只延续已有持仓的价格；新买入的票不加入 last_prices，
        # 次日「第一步」中 last_prices.get(code) 返回 None → 使用 open
        prices = {
            normalize_code(str(c)): p
            for c, p in last_prices.items()
            if normalize_code(str(c)) in new_codes
        }
        return holdings, prices

    def _mark_to_market_and_fill_entries(
        self,
        holdings: pd.DataFrame,
        last_prices: Dict[str, float],
        day_ohlcv: Dict[str, pd.DataFrame],
        trade_date: str,
    ) -> tuple:
        """按日定价；新开仓用开盘价成交并回填 entry_price。

        返回 (holdings, name_rets, bases, last_prices, priced_count)。
        bases[code] = 当日收益分母（新仓=open，旧仓=昨收）。
        """
        if holdings is None or holdings.empty:
            return holdings, {}, {}, last_prices, 0
        df = holdings.copy()
        df["code"] = df["code"].map(lambda c: normalize_code(str(c)))
        if "entry_price" not in df.columns:
            df["entry_price"] = np.nan
        name_rets: Dict[str, float] = {}
        bases: Dict[str, float] = {}
        prices = dict(last_prices)
        priced = 0
        entry_prices = []
        weights_out: List[float] = []
        basis_out: List[float] = []
        locked_out: List[float] = []
        entry_dates_out: List[Any] = []

        def _keep_unfilled(row_w, basis_w, entry_d, entry_p) -> None:
            entry_prices.append(entry_p)
            weights_out.append(row_w)
            basis_out.append(basis_w)
            locked_out.append(0.0)
            entry_dates_out.append(entry_d)

        for _, row in df.iterrows():
            code = str(row["code"])
            today_df = _day_bar_frame(day_ohlcv, code)
            ep = row.get("entry_price")
            try:
                ep_f = float(ep)
            except (TypeError, ValueError):
                ep_f = float("nan")
            if today_df is None or today_df.empty:
                name_rets[code] = 0.0
                basis_w = _positive(row.get("basis_weight"))
                _keep_unfilled(
                    basis_w if basis_w is not None else float(row["weight"]),
                    basis_w if basis_w is not None else float(row["weight"]),
                    row.get("entry_date"),
                    ep_f,
                )
                continue
            today_row = self._row_for_date(today_df, trade_date)
            if today_row is None:
                name_rets[code] = 0.0
                basis_w = _positive(row.get("basis_weight"))
                _keep_unfilled(
                    basis_w if basis_w is not None else float(row["weight"]),
                    basis_w if basis_w is not None else float(row["weight"]),
                    row.get("entry_date"),
                    ep_f,
                )
                continue
            close_today = float(today_row.get("close", np.nan))
            open_today = float(today_row.get("open", close_today))
            if np.isnan(close_today) or close_today <= 0:
                name_rets[code] = 0.0
                basis_w = _positive(row.get("basis_weight"))
                _keep_unfilled(
                    basis_w if basis_w is not None else float(row["weight"]),
                    basis_w if basis_w is not None else float(row["weight"]),
                    row.get("entry_date"),
                    ep_f,
                )
                continue
            prev_close = prices.get(code)
            pre_close = _positive(today_row.get("pre_close")) or (
                float(prev_close) if prev_close and prev_close > 0 else None
            )
            held_basis = _positive(row.get("basis_weight"))
            if held_basis is None:
                held_basis = float(row["weight"]) if prev_close and prev_close > 0 else 0.0
            held_basis = min(held_basis, float(row["weight"]))
            add_w = float(row["weight"]) - held_basis
            buy_blocked = add_w > 1e-8 and _open_is_limit_up(
                code, pre_close or open_today, open_today, trade_date
            )
            if buy_blocked:
                add_w = 0.0
                if held_basis <= 1e-8:
                    name_rets[code] = 0.0
                    entry_prices.append(ep_f)
                    weights_out.append(0.0)
                    basis_out.append(0.0)
                    locked_out.append(0.0)
                    entry_dates_out.append(row.get("entry_date"))
                    continue
            weight_today = held_basis + add_w
            if prev_close is None or prev_close <= 0:
                if open_today <= 0 or weight_today <= 1e-8:
                    name_rets[code] = 0.0
                    entry_prices.append(ep_f)
                    weights_out.append(0.0)
                    basis_out.append(0.0)
                    locked_out.append(0.0)
                    entry_dates_out.append(row.get("entry_date"))
                    continue
                ep_f = open_today
                bases[code] = open_today
                name_rets[code] = close_today / open_today - 1.0
                entry_dates_out.append(trade_date)
                locked_out.append(weight_today)
            else:
                denom = pre_close if pre_close and pre_close > 0 else float(prev_close)
                bases[code] = denom
                ret_held = close_today / denom - 1.0
                ret_add = close_today / open_today - 1.0 if open_today > 0 else ret_held
                if weight_today <= 1e-8:
                    name_rets[code] = 0.0
                else:
                    name_rets[code] = (
                        held_basis * ret_held + add_w * ret_add
                    ) / weight_today
                entry_dates_out.append(row.get("entry_date"))
                locked_out.append(add_w if add_w > 1e-8 else 0.0)
            prices[code] = close_today
            weights_out.append(weight_today)
            basis_out.append(weight_today)
            entry_prices.append(ep_f)
            priced += 1
        df["entry_price"] = entry_prices
        df["weight"] = weights_out
        df["basis_weight"] = basis_out
        df["locked_weight"] = locked_out
        df["entry_date"] = entry_dates_out
        df = df[df["weight"].astype(float) > 1e-8].reset_index(drop=True)
        return df, name_rets, bases, prices, priced

    def _apply_hard_stops(
        self,
        holdings: pd.DataFrame,
        last_prices: Dict[str, float],
        name_rets: Dict[str, float],
        bases: Dict[str, float],
        day_ohlcv: Dict[str, pd.DataFrame],
        trade_date: str,
        hard_stop_pct: float,
        *,
        industry_stop_pct: Optional[float] = None,
        industry_codes: Optional[set] = None,
        enabled: bool = True,
    ) -> tuple:
        """相对入场价止损（按当日 low 触发）。

        返回 (新持仓, day_return, 止损换手, 止损成本占比, last_prices)。
        跳空低开则按开盘价成交，否则按止损价。
        三策略账户关闭这条止损，当日收益仍按收盘计价。
        """
        def _held_day_return(frame: Optional[pd.DataFrame]) -> float:
            if frame is None or frame.empty:
                return 0.0
            total = 0.0
            for _, r in frame.iterrows():
                code = normalize_code(str(r["code"]))
                total += float(r["weight"]) * float(name_rets.get(code, 0.0))
            return total

        if not enabled or holdings is None or holdings.empty or hard_stop_pct is None:
            return holdings, _held_day_return(holdings), 0.0, 0.0, last_prices
        if "entry_price" not in holdings.columns:
            return holdings, _held_day_return(holdings), 0.0, 0.0, last_prices

        ind_set = industry_codes or set()
        ind_stop = industry_stop_pct if industry_stop_pct is not None else 0.0
        stamp = float(getattr(self, "stamp_tax", 0.0005))

        keep_rows = []
        sold_w = 0.0
        stop_cost = 0.0
        day_return = 0.0
        prices = dict(last_prices)
        tc = getattr(self.strategy, "_turnover_controller", None)
        for _, row in holdings.iterrows():
            code = normalize_code(str(row["code"]))
            weight = float(row["weight"])
            ep = row.get("entry_price")
            try:
                ep_f = float(ep)
            except (TypeError, ValueError):
                ep_f = float("nan")
            close_ret = float(name_rets.get(code, 0.0))
            stop = hard_stop_pct
            if ind_stop < 0 and code in ind_set:
                stop = ind_stop

            triggered = False
            stop_ret = close_ret
            today_row = None
            if ep_f == ep_f and ep_f > 0 and stop is not None:
                stop_px = ep_f * (1.0 + float(stop))
                today_df = _day_bar_frame(day_ohlcv, code)
                today_row = (
                    self._row_for_date(today_df, trade_date)
                    if today_df is not None and not today_df.empty
                    else None
                )
                if today_row is not None:
                    try:
                        low_today = float(
                            today_row.get("low", today_row.get("close", np.nan))
                        )
                        open_today = float(
                            today_row.get("open", today_row.get("close", np.nan))
                        )
                    except (TypeError, ValueError):
                        low_today = open_today = float("nan")
                    base = float(bases.get(code) or 0.0)
                    if (
                        low_today == low_today
                        and low_today > 0
                        and low_today <= stop_px + 1e-12
                        and base > 0
                    ):
                        fill = (
                            open_today
                            if open_today == open_today
                            and open_today > 0
                            and open_today <= stop_px + 1e-12
                            else stop_px
                        )
                        if fill > 0:
                            stop_ret = fill / base - 1.0
                            triggered = True

            locked = _positive(row.get("locked_weight")) or 0.0
            same_day = str(row.get("entry_date") or "") == str(trade_date)
            sealed_down = False
            if today_row is not None:
                pre = _positive(today_row.get("pre_close")) or _positive(bases.get(code))
                close_px = _positive(today_row.get("close"))
                if pre and close_px and is_limit_down(code, pre, close_px, trade_date):
                    sealed_down = True
            if same_day or sealed_down or locked >= weight - 1e-8:
                triggered = False
            free = bool(tc is not None and tc.is_fee_free(code))
            sell_rate = 0.0 if free else (
                self.fee_rate if is_equity_etf(code) else self.fee_rate + stamp
            )

            if triggered and locked > 1e-8:
                sell_w = max(0.0, weight - locked)
                day_return += sell_w * stop_ret + locked * close_ret
                sold_w += sell_w
                stop_cost += sell_w * sell_rate
                kept = row.copy()
                kept["weight"] = locked
                kept["basis_weight"] = locked
                kept["locked_weight"] = locked
                keep_rows.append(kept)
                logger.info(
                    f"🛑 {trade_date} 硬止损 {code}: 当日买入 {locked:.2%} 不可卖，"
                    f"卖出 {sell_w:.2%}"
                )
                continue

            if triggered:
                sold_w += weight
                day_return += weight * stop_ret
                prices.pop(code, None)
                stop_cost += weight * sell_rate
                logger.info(
                    f"🛑 {trade_date} 硬止损 {code}: stop={stop:.2%} "
                    f"day_ret={stop_ret:.2%} (替代收盘 {close_ret:.2%})"
                )
                continue
            day_return += weight * close_ret
            keep_rows.append(row)

        if sold_w <= 0:
            return holdings, day_return, 0.0, 0.0, last_prices

        stop_turnover = 0.5 * sold_w
        if not keep_rows:
            return (
                pd.DataFrame(columns=list(holdings.columns)),
                day_return,
                stop_turnover,
                stop_cost,
                prices,
            )
        out = pd.DataFrame(keep_rows).reset_index(drop=True)
        return out, day_return, stop_turnover, stop_cost, prices

    def _apply_trend_exits(
        self,
        last_holdings: pd.DataFrame,
        trade_date: str,
        prev_trade_date: Optional[str],
        exit_cfg: Dict[str, Any],
        regime: Optional[str] = None,
    ) -> tuple:
        empty = last_holdings.iloc[0:0]
        if not exit_policy.trend_exit_enabled(exit_cfg, regime=regime) or last_holdings is None or last_holdings.empty:
            return last_holdings, 0.0, 0.0

        held = set(last_holdings["code"].astype(str))
        f35_map: Dict[str, float] = {}
        prev_f35: Dict[str, float] = {}
        fp = self.signal_pipeline
        field_fn = getattr(fp, "dragon_field_by_code", None) if fp else None
        if field_fn is not None:
            if trade_date:
                f35_map.update(field_fn(trade_date, "f35_bollinger_trend", held))
            if prev_trade_date:
                prev_f35.update(field_fn(prev_trade_date, "f35_bollinger_trend", held))

        full = getattr(self, "_ohlcv_full", None) or {}
        ohlcv_by = {c: full[c] for c in held if c in full}
        # 漏斗缓存缺失时用日线补 f35 / 昨 f35，避免非再平衡日趋势退出空转
        if ohlcv_by:
            missing = {
                c
                for c in held
                if not np.isfinite(float(f35_map.get(c, float("nan"))))
            }
            if missing and trade_date:
                f35_map.update(
                    exit_policy.f35_map_from_ohlcv(ohlcv_by, trade_date, missing)
                )
            if prev_trade_date:
                miss_prev = {
                    c
                    for c in held
                    if not np.isfinite(float(prev_f35.get(c, float("nan"))))
                }
                if miss_prev:
                    prev_f35.update(
                        exit_policy.f35_map_from_ohlcv(
                            ohlcv_by, prev_trade_date, miss_prev
                        )
                    )
        force = exit_policy.trend_force_exit_codes(
            held,
            trade_date,
            exit_cfg,
            ohlcv_by_code=ohlcv_by,
            f35_by_code=f35_map,
            prev_f35_by_code=prev_f35,
            regime=regime,
        )
        if not force:
            return last_holdings, 0.0, 0.0
        blocked = _unsellable_codes(last_holdings, trade_date)
        force = {c for c in force if normalize_code(str(c)) not in blocked}
        if not force:
            return last_holdings, 0.0, 0.0

        df = last_holdings[~last_holdings["code"].astype(str).isin(force)].copy()
        if not df.empty:
            df = self._clip_weights(df)
        costs = self.strategy._turnover_controller.estimate_trading_costs(
            current_holdings=last_holdings,
            new_holdings=df,
            total_capital=1.0,
        )
        logger.info(f"🚪 {trade_date} 趋势退出: {sorted(force)}")
        return df, float(costs.get("turnover", 0.0)), float(costs.get("cost_pct", 0.0))

    def _pipeline_market_kwargs(
        self,
        trade_date: str,
        prev_trade_date: Optional[str],
        last_holdings: Optional[pd.DataFrame],
    ) -> Dict[str, Any]:
        """把日线 / 昨日 f35 交给现场管道，避免 require_both 时趋势退出空转。"""
        held: set = set()
        if last_holdings is not None and not last_holdings.empty and "code" in last_holdings.columns:
            held = set(last_holdings["code"].astype(str))
        full = getattr(self, "_ohlcv_full", None) or {}
        ohlcv_by = {c: full[c] for c in held if c in full}
        prev_f35: Dict[str, float] = {}
        fp = self.signal_pipeline
        field_fn = getattr(fp, "dragon_field_by_code", None) if fp else None
        if field_fn is not None and prev_trade_date and held:
            prev_f35 = field_fn(prev_trade_date, "f35_bollinger_trend", held)
        return {
            "ohlcv_by_code": ohlcv_by,
            "prev_f35_by_code": prev_f35,
        }

    @staticmethod
    def _attach_entry_meta(
        new_holdings: pd.DataFrame,
        old_holdings: Optional[pd.DataFrame],
        last_prices: Dict[str, float],
        day_ohlcv: Dict[str, pd.DataFrame],
        trade_date: str,
    ) -> pd.DataFrame:
        """为持仓写入/继承 entry_date、entry_price。

        新开仓 entry_price 留 NaN，等次日开盘成交时由
        `_mark_to_market_and_fill_entries` 回填为实际 open。
        """
        if new_holdings is None or new_holdings.empty:
            return new_holdings

        old_map: Dict[str, Dict[str, Any]] = {}
        if old_holdings is not None and not old_holdings.empty:
            for _, r in old_holdings.iterrows():
                old_map[normalize_code(str(r["code"]))] = {
                    "entry_date": r.get("entry_date"),
                    "entry_price": r.get("entry_price"),
                    "weight": float(r.get("weight") or 0.0),
                }

        df = new_holdings.copy()
        entry_dates = []
        entry_prices = []
        basis_weights = []
        for _, r in df.iterrows():
            code = normalize_code(str(r["code"]))
            new_w = float(r.get("weight") or 0.0)
            prev = old_map.get(code)
            inherited = False
            if prev and prev.get("entry_date") is not None:
                try:
                    prev_ep = float(prev.get("entry_price"))
                except (TypeError, ValueError):
                    prev_ep = float("nan")
                if prev_ep == prev_ep and prev_ep > 0:
                    entry_dates.append(prev["entry_date"])
                    entry_prices.append(prev_ep)
                    prev_w = float(prev.get("weight") or 0.0)
                    # 加仓的新份额次日开盘才有，不能吃隔夜缺口
                    basis_weights.append(prev_w if new_w > prev_w + 1e-12 else new_w)
                    inherited = True
            if not inherited:
                entry_dates.append(None)
                entry_prices.append(np.nan)
                basis_weights.append(0.0)
        df["code"] = df["code"].map(lambda c: normalize_code(str(c)))
        df["entry_date"] = entry_dates
        df["entry_price"] = entry_prices
        df["basis_weight"] = basis_weights
        df["locked_weight"] = 0.0
        return df

    def _resolve_trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """优先用指数/已加载 OHLCV 的真实交易日，否则退回工作日并告警。"""
        start = normalize_date(start_date)
        end = normalize_date(end_date)
        full = getattr(self, "_ohlcv_full", None) or {}
        for key in (INDEX_CODE, normalize_code(INDEX_CODE), "000300"):
            df = full.get(key)
            if df is None or getattr(df, "empty", True) or "trade_date" not in getattr(df, "columns", []):
                continue
            dates = sorted(
                {
                    normalize_date(str(d))
                    for d in df["trade_date"].tolist()
                    if start <= normalize_date(str(d)) <= end
                }
            )
            if dates:
                return dates
        # 任意一只股票的交易日并集（测试注入用）
        union: set = set()
        for df in full.values():
            if df is None or getattr(df, "empty", True):
                continue
            if "trade_date" not in getattr(df, "columns", []):
                continue
            for d in df["trade_date"].tolist():
                td = normalize_date(str(d))
                if start <= td <= end:
                    union.add(td)
        if union:
            return sorted(union)
        logger.warning(
            "未找到指数/OHLCV 交易日历，回退周一~周五（含节假日偏差）"
        )
        return self._gen_trade_dates(start, end)

    @staticmethod
    def _gen_trade_dates(start_date: str, end_date: str) -> List[str]:
        """生成日期列表：工作日过滤（仅作无行情日历时的回退）。"""
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

def _positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 0 and number == number:
        return number
    return None


def _open_is_limit_up(code: str, pre_close: Optional[float], open_px: float, trade_date: str) -> bool:
    pre = _positive(pre_close)
    opened = _positive(open_px)
    if pre is None or opened is None:
        return False
    return is_limit_up(code, pre, opened, trade_date)


def _unsellable_codes(holdings: Optional[pd.DataFrame], trade_date: str) -> set:
    """当日才成交、或当日买入份额占满仓位的代码，当天不能卖。"""
    blocked: set = set()
    if holdings is None or getattr(holdings, "empty", True):
        return blocked
    for _, row in holdings.iterrows():
        code = normalize_code(str(row["code"]))
        if str(row.get("entry_date") or "") == str(trade_date):
            blocked.add(code)
            continue
        locked = _positive(row.get("locked_weight")) or 0.0
        weight = float(row.get("weight") or 0.0)
        if locked > 0 and locked >= weight - 1e-8:
            blocked.add(code)
    return blocked


def _day_bar_frame(
    day_ohlcv: Dict[str, pd.DataFrame],
    code: str,
) -> Optional[pd.DataFrame]:
    """按 6 位代码取当日 bar；兼容未补零的旧键。"""
    code_s = normalize_code(str(code))
    frame = (day_ohlcv or {}).get(code_s)
    if frame is not None:
        return frame
    raw = str(code)
    if raw != code_s:
        return (day_ohlcv or {}).get(raw)
    return None


def _append_book_bars(
    index_rows: List[dict],
    asset_hist: Dict[str, List[float]],
    day_ohlcv: Dict[str, pd.DataFrame],
) -> None:
    """把当日收盘记入账户状态所用的历史。判定用的是调用前已经积累的部分。"""
    for code, frame in (day_ohlcv or {}).items():
        if frame is None or getattr(frame, "empty", True):
            continue
        row = frame.iloc[-1]
        try:
            close = float(row.get("close", float("nan")))
        except (TypeError, ValueError):
            continue
        if not close > 0:
            continue
        code_s = normalize_code(str(code))
        if code_s not in BOOK_OHLCV_CODES:
            continue
        asset_hist.setdefault(code_s, []).append(close)
        if code_s != INDEX_CODE:
            continue
        def _num(key: str, default: float) -> float:
            try:
                value = float(row.get(key, default))
            except (TypeError, ValueError):
                return default
            if value != value:
                return default
            return value
        bar_date = row.get("trade_date", "")
        index_rows.append({
            "trade_date": normalize_date(str(bar_date)) if bar_date else "",
            "close": close,
            "high": _num("high", close),
            "low": _num("low", close),
            "volume": _num("volume", 0.0),
        })


def _slim_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    """回测只需价量列；去掉多余字段并压成 float32，降低驻留内存。"""
    keep = [
        c
        for c in (
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "pre_close",
            "pct_chg",
        )
        if c in df.columns
    ]
    out = df.loc[:, keep].copy()
    for c in keep:
        if c == "trade_date":
            out[c] = out[c].astype(str)
        else:
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("float32")
    return out.reset_index(drop=True)


def make_ohlcv_provider_from_dict(
    ohlcv_by_code: Dict[str, pd.DataFrame],
) -> Callable[[str], Dict[str, pd.DataFrame]]:
    """
    把静态 OHLCV 封装成按日 provider。

    预建 td→code→row_dict 索引；按日返回时再包成 1 行 DataFrame。
    不再把「每个交易日的全市场切片」永久缓存——长区间下那会复制出海量小对象直接 OOM。
    """
    by_date: Dict[str, Dict[str, Dict[str, Any]]] = {}
    value_cols = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "pre_close",
        "pct_chg",
    )
    for code, df in ohlcv_by_code.items():
        if df is None or getattr(df, "empty", True) or "trade_date" not in df.columns:
            continue
        cols = [c for c in value_cols if c in df.columns]
        dates = df["trade_date"].astype(str).to_numpy()
        if not cols:
            continue
        values = df[cols].to_numpy(copy=False)
        code_s = normalize_code(str(code))
        for i, td in enumerate(dates):
            rec = {cols[j]: values[i, j] for j in range(len(cols))}
            by_date.setdefault(str(td), {})[code_s] = rec

    def _provider(td: str) -> Dict[str, pd.DataFrame]:
        day = by_date.get(str(td)) or {}
        out: Dict[str, pd.DataFrame] = {}
        for code, rec in day.items():
            row = dict(rec)
            row["trade_date"] = str(td)
            out[code] = pd.DataFrame([row])
        return out

    return _provider


def make_signal_provider_from_pipeline_data(
    data_by_date: Dict[str, Dict[str, Any]],
) -> Callable[[str], Optional[Dict[str, Any]]]:
    """每个交易日返回一个预构造好的现场漏斗入参字典。"""
    def _provider(td: str) -> Optional[Dict[str, Any]]:
        return data_by_date.get(td)
    return _provider


if __name__ == "__main__":
    sys.exit(asyncio.run(Backtester.run_cli()))
