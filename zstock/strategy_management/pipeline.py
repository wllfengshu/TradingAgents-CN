"""
策略层完整管道（截面因子方案）

新版策略层不再依赖大模型/ML 预测，直接基于 zstock.factor_management的 CrossSectionStrategyPipeline 产出的 top K 信号做：

    信号生成 (factor pipeline) → 组合优化 → 风控检查 → 换手控制 → 最终持仓
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .signal_generator import SignalGenerator
from .portfolio_optimizer import PortfolioOptimizer
from .risk_manager import RiskManager
from .turnover_controller import TurnoverController
from zstock.common.config.strategy_config import build_runtime_config

logger = logging.getLogger(__name__)


class StrategyPipeline:
    """策略层全流程：信号 → 组合 → 风控 → 换手。"""

    _config_cache: Optional[Dict] = None

    def __init__(
        self,
        signal_generator: Optional[SignalGenerator] = None,
        portfolio_optimizer: Optional[PortfolioOptimizer] = None,
        risk_manager: Optional[RiskManager] = None,
        turnover_controller: Optional[TurnoverController] = None,
    ):
        self.signal_generator = signal_generator or SignalGenerator()
        self.portfolio_optimizer = portfolio_optimizer or PortfolioOptimizer()
        self.risk_manager = risk_manager or RiskManager()
        if turnover_controller is None:
            tov = self._default_config().get("turnover_control", {})
            turnover_controller = TurnoverController(
                buffer_threshold=float(tov.get("buffer_threshold", 0.25)),
                min_hold_days=int(tov.get("min_hold_days", 3)),
                fee_rate=float(tov.get("fee_rate", 0.0015)),
            )
        self.turnover_controller = turnover_controller
        self.pipeline_result: Dict[str, Any] = {}
        logger.info("✅ StrategyPipeline(factor) 初始化完成")

    @staticmethod
    def _resolve_effective_top_k(cfg: Dict[str, Any], regime: str = "neutral") -> int:
        """从 final_score.by_regime 解析当日 effective top_k。"""
        fs = cfg.get("final_score") or {}
        by_regime = fs.get("by_regime") or {}
        sub = by_regime.get(regime) or by_regime.get(str(regime))
        if isinstance(sub, dict) and sub.get("top_k") is not None:
            return int(sub["top_k"])
        return int(fs.get("top_k", 5))

    async def execute_full_pipeline(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 60,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
        current_positions: Optional[pd.DataFrame] = None,
        total_capital: float = 1e7,
        config: Optional[Dict] = None,
        prebuilt_data: Optional[Dict] = None,
        precomputed_signals: Optional[pd.DataFrame] = None,  # ← 新增
    ) -> Dict:
        """
        Args:
            trade_date: 交易日，'YYYY-MM-DD'，默认今天。
            lookback_days: OHLCV 回看天数。
            sectors: 关心的板块名列表，None 走默认。
            max_stocks: 限制处理股票数（调试用）。
            current_positions: 当前持仓（code, weight, [score]）。
            total_capital: 总资金（用于换手成本估算）。
            config: 各阶段配置覆盖，结构见 _default_config()。
            prebuilt_data: 离线测试时直接注入 factor pipeline 的入参，
                跳过 query_service 真实拉数。
        """
        cfg = self._merge_config(config)
        td = trade_date or datetime.now().strftime('%Y-%m-%d')

        logger.info("=" * 60)
        logger.info(f"🚀 策略层管道启动 trade_date={td}")
        logger.info("=" * 60)

        results: Dict[str, Any] = {}

        # 1. 信号
        if precomputed_signals is not None:
            signals_df = precomputed_signals
            logger.info(f"✅ 使用预计算信号: {len(signals_df)} 只候选")
        else:
            signals_df = await self.signal_generator.generate_signals(
                trade_date=td,
                lookback_days=lookback_days,
                sectors=sectors,
                max_stocks=max_stocks,
                prebuilt_data=prebuilt_data,
            )
        results['signals'] = signals_df
        results['market_grade'] = self._extract_market_grade(signals_df)
        if signals_df.empty:
            logger.warning("⚠️ 无信号，管道终止（由回测层决定减仓/清仓）")
            self.pipeline_result = {
                'status': 'no_signals',
                'trade_date': td,
                'results': results,
                'market_grade': results['market_grade'],
            }
            return self.pipeline_result

        # 2. 组合优化（regime 动态 top_k → max/min holdings）
        buy_df = signals_df
        if 'signal_type' in signals_df.columns:
            buy_only = signals_df[signals_df['signal_type'] == 'buy']
            if not buy_only.empty:
                buy_df = buy_only

        regime = "neutral"
        if hasattr(signals_df, "attrs"):
            regime = str(signals_df.attrs.get("regime", regime))
        effective_top_k = self._resolve_effective_top_k(cfg, regime)
        port_cfg = dict(cfg["portfolio_optimization"])
        port_cfg["max_holdings"] = effective_top_k
        port_cfg["min_holdings"] = max(1, effective_top_k - 2)
        logger.info(
            f"📊 regime={regime} effective_top_k={effective_top_k} "
            f"holdings=[{port_cfg['min_holdings']}, {port_cfg['max_holdings']}]"
        )

        try:
            opt_result = self.portfolio_optimizer.optimize_portfolio(
                signals_df=buy_df,
                **port_cfg,
            )
        except Exception as e:
            logger.error(f"❌ 组合优化异常: {e}")
            opt_result = {'status': 'failed', 'reason': str(e), 'holdings_df': pd.DataFrame()}
        results['optimization'] = opt_result
        if opt_result.get('status') != 'success':
            self.pipeline_result = {'status': 'optimization_failed', 'trade_date': td, 'results': results}
            return self.pipeline_result
        optimized_holdings = opt_result['holdings_df'].copy()
        # 回填 sector，便于风控按行业检查
        if 'sector_code' in signals_df.columns and 'sector_code' not in optimized_holdings.columns:
            mapping = signals_df.set_index('code')['sector_code'].to_dict()
            optimized_holdings['sector_code'] = optimized_holdings['code'].map(mapping).fillna('UNKNOWN')

        # 3. 风控检查 + 纠正（regime 动态 top_k）
        risk_limits_override = {
            "top_k": effective_top_k,
            "min_holdings": max(1, effective_top_k - 2),
            "max_holdings": effective_top_k * 2,
        }
        try:
            compliance, optimized_holdings = self.risk_manager.apply_corrections(
                optimized_holdings,
                signals_df,
                limits_override=risk_limits_override,
            )
        except Exception as e:
            logger.error(f"❌ 风控检查异常: {e}")
            compliance = {'status': 'error', 'issues': [str(e)], 'metrics': {}}
        results['risk_check'] = compliance
        logger.info(f"🔍 风控状态: {compliance['status']} issues={len(compliance['issues'])}")

        # 4. 排名退出：跌出候选宇宙后部 → 强制卖出（豁免最短持有）
        force_exit_codes = self._rank_force_exit_codes(
            current_positions, signals_df, cfg.get('exit_rules') or {}
        )
        if force_exit_codes:
            logger.info(f"🚪 排名退出强制卖出: {sorted(force_exit_codes)}")

        market_grade = self._extract_market_grade(signals_df)
        reduce_only = False
        from zstock.strategy_management.weak_regime_protection import (
            apply_reduce_only_filter,
            should_reduce_only,
        )

        if should_reduce_only(cfg.get("weak_regime_protection"), regime, market_grade):
            reduce_only = True
            optimized_holdings = apply_reduce_only_filter(
                optimized_holdings,
                current_positions,
                force_exit_codes=force_exit_codes,
            )
            logger.info(
                f"🛡️ reversal+yellow 只减不加: 目标 {len(optimized_holdings)} 只"
            )

        # 5. 换手控制（buffer + 最短持有；force_exit 不受 min_hold 保护）
        try:
            final_holdings = self.turnover_controller.apply_buffer_mechanism(
                new_holdings=optimized_holdings,
                current_holdings=current_positions,
                buffer_threshold=cfg['turnover_control']['buffer_threshold'],
                min_hold_days=int(cfg['turnover_control'].get('min_hold_days', 0)),
                trade_date=td,
                force_exit_codes=force_exit_codes,
            )
            trading_costs = self.turnover_controller.estimate_trading_costs(
                current_holdings=current_positions,
                new_holdings=final_holdings,
                total_capital=total_capital,
            )
        except Exception as e:
            logger.error(f"❌ 换手控制异常: {e}")
            final_holdings = optimized_holdings.copy()
            trading_costs = {'turnover': 0.0, 'cost_pct': 0.0, 'cost_amount': 0.0, 'fee_rate': 0.0}

        # 6. 市场仓位缩放（黄灯缩仓；红灯上游已空信号）
        # 归一化目标 = min(1, n * cap)：
        #   optimizer/risk 都遵循 "allow_cash" 语义——n*cap<1 时保留现金，不强行拉满仓。
        #   若这里无条件归一化到 1，会突破 optimizer 已经设置的 per-stock cap
        #   （例如 n=3, cap=0.12 → wsum=0.36，若归一化到 1，每只 0.333，破 cap 3 倍）。
        #   因此把上限收敛到 n*cap 之内，仅在权重被换手/风控意外压低到"应当满仓
        #   却只有 wsum<n*cap"的场景才补回来。
        if (
            not final_holdings.empty
            and 'weight' in final_holdings.columns
        ):
            n_eff = int(len(final_holdings))
            wsum = float(final_holdings['weight'].astype(float).sum())
            cap = float(
                (cfg.get('portfolio_optimization') or {}).get('max_weight_per_stock', 1.0)
            )
            target_sum = min(1.0, n_eff * cap) if cap > 0 else 1.0
            if wsum > 1e-9 and wsum < target_sum - 1e-9:
                final_holdings = final_holdings.copy()
                final_holdings['weight'] = (
                    final_holdings['weight'].astype(float) * (target_sum / wsum)
                )
                logger.info(
                    f"📈 权重归一化: {wsum:.2%} → {target_sum:.2%}"
                    f"（n={n_eff}, cap={cap:.2%}, 保留现金以尊重 per-stock cap）"
                )

        position_scale = 1.0
        scale_col = None
        for col in ('position_scale', 'position_scale_factor'):
            if col in signals_df.columns and not signals_df.empty:
                scale_col = col
                break
        if scale_col:
            try:
                position_scale = float(signals_df[scale_col].iloc[0])
            except (TypeError, ValueError):
                position_scale = 1.0
        elif signals_df.attrs.get('position_scale') is not None:
            position_scale = float(signals_df.attrs.get('position_scale', 1.0))
        elif signals_df.attrs.get('position_scale_factor') is not None:
            position_scale = float(signals_df.attrs.get('position_scale_factor', 1.0))
        position_scale = float(np.clip(position_scale, 0.0, 1.0))
        if (
            not final_holdings.empty
            and position_scale < 1.0 - 1e-9
            and 'weight' in final_holdings.columns
        ):
            final_holdings = final_holdings.copy()
            final_holdings['weight'] = final_holdings['weight'].astype(float) * position_scale
            logger.info(
                f"📉 市场仓位缩放 position_scale={position_scale:.2f}，"
                f"权益仓位={final_holdings['weight'].sum():.2%}"
            )

        results['final_holdings'] = final_holdings
        results['trading_costs'] = trading_costs
        results['position_scale'] = position_scale
        results['force_exit_codes'] = sorted(force_exit_codes)

        summary = {
            'status': 'success',
            'trade_date': td,
            'execution_time': datetime.now().astimezone().isoformat(),
            'market_grade': results['market_grade'],
            'regime': regime,
            'effective_top_k': effective_top_k,
            'reduce_only': reduce_only,
            'statistics': {
                'signals_count': int(
                    (buy_df['signal_type'] == 'buy').sum()
                    if 'signal_type' in buy_df.columns
                    else len(buy_df)
                ),
                'universe_count': len(signals_df),
                'optimized_holdings': len(optimized_holdings),
                'final_holdings': len(final_holdings),
                'risk_status': compliance['status'],
                'turnover': trading_costs['turnover'],
                'cost_pct': trading_costs['cost_pct'],
                'total_capital': total_capital,
                'position_scale': position_scale,
            },
            'results': results,
        }
        self.pipeline_result = summary
        logger.info(
            f"✅ 完成: signals={len(signals_df)} opt={len(optimized_holdings)} "
            f"final={len(final_holdings)} turnover={trading_costs['turnover']:.2%}"
        )
        return summary

    @staticmethod
    def _extract_market_grade(signals_df: Optional[pd.DataFrame]) -> str:
        if signals_df is None:
            return "unknown"
        for attr in ("market_grade", "market_risk_level"):
            grade = signals_df.attrs.get(attr)
            if grade:
                return str(grade)
        if not signals_df.empty:
            for col in ("market_grade", "market_risk_level"):
                if col in signals_df.columns:
                    val = signals_df[col].iloc[0]
                    if val is not None and str(val) != "nan":
                        return str(val)
        return "unknown"

    @staticmethod
    def _rank_force_exit_codes(
        current_positions: Optional[pd.DataFrame],
        signals_df: pd.DataFrame,
        exit_rules: Dict[str, Any],
    ) -> set:
        """
        排名退出：持仓若仍在今日候选宇宙中，但截面排名分位劣于阈值，则强制卖出。
        （连续跌出候选宇宙的计数在 Backtester 中处理）
        """
        if current_positions is None or current_positions.empty or signals_df is None or signals_df.empty:
            return set()
        if "rank" not in signals_df.columns or "code" not in signals_df.columns:
            return set()
        thr = float(exit_rules.get("rank_percentile_threshold", 0.85))
        n = len(signals_df)
        if n <= 1:
            return set()
        rank_map = (
            signals_df.drop_duplicates(subset="code", keep="first")
            .set_index("code")["rank"]
            .to_dict()
        )
        force = set()
        for code in current_positions["code"].astype(str):
            r = rank_map.get(code)
            if r is None:
                continue
            # rank 从 1 开始；分位 = rank/n，越大越差
            pct = float(r) / float(n)
            if pct > thr + 1e-12:
                force.add(code)
        return force

    @staticmethod
    def _default_config() -> Dict[str, Dict]:
        """
        从 strategy_params.json 加载策略参数，转换为 pipeline 各阶段所需的配置结构。
        配置文件是策略参数的唯一数据源（Single Source of Truth）。
        结果缓存于类变量，进程生命周期内只读一次磁盘。
        派生逻辑统一委托给 zstock.common.config.strategy_config.build_runtime_config()。
        """
        if StrategyPipeline._config_cache is not None:
            return StrategyPipeline._config_cache

        cfg = build_runtime_config()
        StrategyPipeline._config_cache = cfg
        return cfg

    @classmethod
    def _merge_config(cls, override: Optional[Dict]) -> Dict[str, Dict]:
        import copy

        base = copy.deepcopy(cls._default_config())
        if not override:
            return base
        for k, v in override.items():
            if k in base and isinstance(v, dict) and isinstance(base[k], dict):
                base[k] = {**base[k], **v}
            else:
                base[k] = v
        return base
