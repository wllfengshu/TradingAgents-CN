"""
日频截面策略回测器

定位：
- 配合 StrategyPipeline 把"每个交易日的目标持仓"按日次再平衡跑出净值曲线；
- 不依赖 Qlib，纯 pandas/numpy 实现；
- 输入既支持真实 OHLCV（从 query_service 拉），也支持离线注入。

回测假设：
- T 日生成信号，按 T+1 开盘价建仓 / 调仓；当日收益用 T+1 的开盘到收盘；
- 后续 持仓日 的收益用每日 close→close；
- 单次换手成本 = turnover * fee_rate，单边费率配在 TurnoverController 上。

返回：
- BacktestResult.equity_curve  累计净值
- BacktestResult.daily_returns 日收益率
- BacktestResult.holdings_log  每个再平衡日的持仓快照
- BacktestResult.metrics       综合指标
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from zstock.strategy_management.pipeline import StrategyPipeline

logger = logging.getLogger(__name__)


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
        fee_rate: float = 0.0015,
        initial_capital: float = 1e7,
    ):
        self.strategy = strategy_pipeline or StrategyPipeline()
        self.fee_rate = fee_rate
        self.initial_capital = initial_capital
        # 同步 fee_rate 到 turnover_controller，保持成本估算一致
        self.strategy.turnover_controller.fee_rate = fee_rate
        logger.info(f"✅ Backtester 初始化完成: fee={fee_rate} init_capital={initial_capital}")

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

        Returns:
            BacktestResult
        """
        trade_dates = self._gen_trade_dates(start_date, end_date)
        if not trade_dates:
            raise ValueError(f"start_date={start_date} ~ end_date={end_date} 无可用交易日")

        # 打印开场 banner
        cfg_print = strategy_config or {}
        opt_cfg = (cfg_print.get('portfolio_optimization') or {})
        tov_cfg = (cfg_print.get('turnover_control') or {})
        if verbose:
            logger.info("=" * 70)
            logger.info("🚀 回测启动")
            logger.info("=" * 70)
            logger.info(f"  区间        : {start_date} ~ {end_date}   ({len(trade_dates)} 个交易日)")
            logger.info(f"  初始资金    : {self.initial_capital:,.2f}")
            logger.info(f"  再平衡频率  : 每 {rebalance_freq} 个交易日")
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

            # === 第二步：决定今天要不要再平衡 ===
            do_rebalance = (i % rebalance_freq == 0)
            day_cost = 0.0
            day_turnover = 0.0
            rebalance_status = '-'
            n_holdings_today = len(last_holdings) if last_holdings is not None else 0

            if do_rebalance:
                prebuilt = signal_provider(td) if signal_provider else None
                try:
                    summary = await self.strategy.execute_full_pipeline(
                        trade_date=td,
                        current_positions=last_holdings,
                        total_capital=equity,
                        config=strategy_config,
                        sectors=sectors,
                        max_stocks=max_stocks,
                        prebuilt_data=prebuilt,
                    )
                except Exception as e:
                    logger.error(f"{td} strategy pipeline 失败: {e}")
                    summary = {'status': 'failed', 'error': str(e)}

                rebalance_status = summary.get('status', 'failed')

                if rebalance_status == 'success':
                    new_holdings = summary['results']['final_holdings'].copy() if 'final_holdings' in summary['results'] else pd.DataFrame()
                    costs = summary['results'].get('trading_costs', {})
                    day_turnover = float(costs.get('turnover', 0.0))
                    day_cost = float(costs.get('cost_pct', 0.0))
                    equity *= (1.0 - day_cost)

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
                    })
                    holdings_log.append({
                        'trade_date': td,
                        'n_holdings': len(new_holdings),
                        'turnover': day_turnover,
                        'cost_pct': day_cost,
                        'holdings': new_holdings.to_dict(orient='records'),
                    })
                    n_holdings_today = len(new_holdings)

                    new_codes = set(new_holdings['code']) if not new_holdings.empty else set()
                    last_prices = {c: p for c, p in last_prices.items() if c in new_codes}
                    for _, row in new_holdings.iterrows():
                        code = row['code']
                        if code in last_prices:
                            continue
                        df = day_ohlcv.get(code)
                        if df is None or df.empty:
                            continue
                        rec = self._row_for_date(df, td)
                        if rec is None:
                            continue
                        close_today = float(rec.get('close', np.nan))
                        if not np.isnan(close_today) and close_today > 0:
                            last_prices[code] = close_today
                    last_holdings = new_holdings if not new_holdings.empty else last_holdings
                elif verbose:
                    logger.info(f"  ⚠️ {td} 再平衡未产出信号 (status={rebalance_status})")

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
