"""
策略层完整管道（截面因子方案）

新版策略层不再依赖大模型/ML 预测，直接基于 zstock.factor_management的 CrossSectionStrategyPipeline 产出的 top K 信号做：

    信号生成 (factor pipeline) → 组合优化 → 风控检查 → 换手控制 → 最终持仓
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd

from .portfolio_optimizer import PortfolioOptimizer
from .risk_manager import RiskManager
from .turnover_controller import TurnoverController
from zstock.common.config.strategy_config import build_runtime_config
from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

logger = logging.getLogger(__name__)


class StrategyPipeline:
    """策略层全流程：信号 → 组合 → 风控 → 换手。"""

    _config_cache: Optional[Dict] = None

    def __init__(
        self,
        factor_pipeline: Optional[CrossSectionStrategyPipeline] = None,
        portfolio_optimizer: Optional[PortfolioOptimizer] = None,
        risk_manager: Optional[RiskManager] = None,
        turnover_controller: Optional[TurnoverController] = None,
    ):
        self._factor_pipeline = factor_pipeline or CrossSectionStrategyPipeline()
        self._portfolio_optimizer = portfolio_optimizer or PortfolioOptimizer()
        self._risk_manager = risk_manager or RiskManager()
        if turnover_controller is None:
            tov = self._default_config().get("turnover_control", {})
            turnover_controller = TurnoverController(
                buffer_threshold=float(tov.get("buffer_threshold", 0.25)),
                min_hold_days=int(tov.get("min_hold_days", 3)),
                fee_rate=float(tov.get("fee_rate", 0.0015)),
            )
        self._turnover_controller = turnover_controller
        self._pipeline_result: Dict[str, Any] = {}
        logger.info("✅ StrategyPipeline(factor) 初始化完成")

    async def _generate_signals(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 60,
        sectors: Optional[list] = None,
        max_stocks: Optional[int] = None,
        prebuilt_data: Optional[Dict] = None,
        prefer_precomputed: bool = True,
    ) -> pd.DataFrame:
        td = trade_date or datetime.now().strftime("%Y-%m-%d")
        logger.info(f"🎯 生成截面信号: trade_date={td}")
        df = await self._factor_pipeline.compute_signals(
            trade_date=td,
            lookback_days=lookback_days,
            sectors=sectors,
            max_stocks=max_stocks,
            prebuilt_data=prebuilt_data,
            prefer_precomputed=prefer_precomputed,
        )
        logger.info(
            f"✅ 信号生成完成: universe={len(df)} "
            f"grade={df.attrs.get('market_grade', '?')}"
        )
        return df

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
        precomputed_signals: Optional[pd.DataFrame] = None,
        ohlcv_by_code: Optional[Dict[str, pd.DataFrame]] = None,
        prev_f35_by_code: Optional[Dict[str, float]] = None,
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
            ohlcv_by_code: 趋势退出用的个股日线；缺省则「未创新高」支路为 False。
            prev_f35_by_code: 上一交易日 f35，供 bollinger_require_declining。
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
            signals_df = await self._generate_signals(
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
            self._pipeline_result = {
                'status': 'no_signals',
                'trade_date': td,
                'results': results,
                'market_grade': results['market_grade'],
            }
            return self._pipeline_result

        # 2. 组合优化（regime 动态 top_k → max/min holdings）
        if "signal_type" in signals_df.columns:
            buy_df = signals_df[signals_df["signal_type"] == "buy"]
        else:
            buy_df = signals_df

        regime = "neutral"
        if hasattr(signals_df, "attrs"):
            regime = str(signals_df.attrs.get("regime", regime))
        market_grade = self._extract_market_grade(signals_df)

        params_root = cfg
        if self._strategy_blocks_new_on_red(params_root) and market_grade == "red":
            buy_df = buy_df.iloc[0:0]
            logger.info("🔴 红灯：strategy 层禁止新开仓（保留 watch 池供排名退出）")
        effective_top_k = self._resolve_effective_top_k(cfg, regime)
        port_cfg = dict(cfg["portfolio_optimization"])
        portfolio_sec = params_root.get("portfolio") or {}
        regime_port = (portfolio_sec.get("by_regime") or {}).get(regime) or {}
        if not isinstance(regime_port, dict):
            regime_port = {}

        def _port_int(key: str, fallback):
            if regime_port.get(key) is not None:
                return int(regime_port[key])
            if portfolio_sec.get(key) is not None:
                return int(portfolio_sec[key])
            return fallback

        port_cfg["max_holdings"] = _port_int("max_holdings", effective_top_k)
        port_cfg["min_holdings"] = _port_int("min_holdings", max(1, effective_top_k - 2))
        if regime_port.get("max_weight_per_stock") is not None:
            port_cfg["max_weight_per_stock"] = float(regime_port["max_weight_per_stock"])
        elif portfolio_sec.get("max_weight_per_stock") is not None:
            port_cfg["max_weight_per_stock"] = float(portfolio_sec["max_weight_per_stock"])
        logger.info(
            f"📊 regime={regime} effective_top_k={effective_top_k} "
            f"holdings=[{port_cfg['min_holdings']}, {port_cfg['max_holdings']}]"
        )

        if buy_df.empty:
            opt_result = {
                "status": "success",
                "reason": "no_buy_signals",
                "holdings_df": pd.DataFrame(columns=["code", "weight"]),
                "weights": np.array([]),
            }
            optimized_holdings = opt_result["holdings_df"].copy()
        else:
            try:
                valid_keys = {
                    "min_holdings",
                    "max_holdings",
                    "max_weight_per_stock",
                    "weighting",
                    "softmax_temperature",
                }
                opt_kwargs = {
                    k: v
                    for k, v in port_cfg.items()
                    if k in valid_keys
                }
                opt_result = self._portfolio_optimizer.optimize_portfolio(
                    signals_df=buy_df,
                    **opt_kwargs,
                )
            except Exception as e:
                logger.error(f"❌ 组合优化异常: {e}")
                opt_result = {
                    "status": "failed",
                    "reason": str(e),
                    "holdings_df": pd.DataFrame(),
                }
            results["optimization"] = opt_result
            if opt_result.get("status") != "success":
                self._pipeline_result = {
                    "status": "optimization_failed",
                    "trade_date": td,
                    "results": results,
                }
                return self._pipeline_result
            optimized_holdings = opt_result["holdings_df"].copy()
        results["optimization"] = opt_result
        # 回填 sector，便于风控按行业检查
        if 'sector_code' in signals_df.columns and 'sector_code' not in optimized_holdings.columns:
            mapping = signals_df.set_index('code')['sector_code'].to_dict()
            optimized_holdings['sector_code'] = optimized_holdings['code'].map(mapping).fillna('UNKNOWN')

        # 3. 风控检查 + 纠正（regime 动态 top_k）
        explicit_hold_cap = (
            regime_port.get("max_holdings") is not None
            or portfolio_sec.get("max_holdings") is not None
        )
        risk_limits_override = {
            "top_k": effective_top_k,
            "min_holdings": port_cfg["min_holdings"],
            "max_holdings": port_cfg["max_holdings"]
            if explicit_hold_cap
            else effective_top_k * 2,
        }
        try:
            compliance, optimized_holdings = self._risk_manager.apply_corrections(
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
        exit_rules = cfg.get("exit_rules") or {}
        force_exit_codes = self._rank_force_exit_codes(
            current_positions, signals_df, exit_rules
        )
        if self._trend_exit_enabled(exit_rules, regime=regime) and current_positions is not None and not current_positions.empty:
            held = set(current_positions["code"].astype(str))
            trend_cfg = self._trend_cfg(exit_rules)
            field = str(trend_cfg.get("bollinger_trend_field", "f35_bollinger_trend"))
            f35_map = self._f35_map_from_signals(signals_df, field=field)
            public_f35 = getattr(self._factor_pipeline, "dragon_field_by_code", None)
            if public_f35 is not None:
                cached = public_f35(td, field, held)
                f35_map.update(cached)
            trend_out = self._trend_force_exit_codes(
                held,
                td,
                exit_rules,
                ohlcv_by_code=ohlcv_by_code or {},
                f35_by_code=f35_map,
                prev_f35_by_code=prev_f35_by_code,
                regime=regime,
            )
            force_exit_codes = force_exit_codes | trend_out
        if force_exit_codes:
            logger.info(f"🚪 强制卖出: {sorted(force_exit_codes)}")

        reduce_only = False
        if self._should_reduce_only(cfg.get("weak_regime_protection"), regime, market_grade):
            reduce_only = True
            optimized_holdings = self._apply_reduce_only_filter(
                optimized_holdings,
                current_positions,
                force_exit_codes=force_exit_codes,
            )
            logger.info(
                f"🛡️ reversal+yellow 只减不加: 目标 {len(optimized_holdings)} 只"
            )

        # 5. 换手控制（buffer + 最短持有；force_exit 不受 min_hold 保护）
        try:
            tc_cfg = cfg.get('turnover_control') or {}
            final_holdings = self._turnover_controller.apply_buffer_mechanism(
                new_holdings=optimized_holdings,
                current_holdings=current_positions,
                buffer_threshold=tc_cfg.get('buffer_threshold', 0.25),
                min_hold_days=int(tc_cfg.get('min_hold_days', 0)),
                trade_date=td,
                force_exit_codes=force_exit_codes,
                fill_open_slots=bool(tc_cfg.get('fill_open_slots', False)),
            )
            trading_costs = self._turnover_controller.estimate_trading_costs(
                current_holdings=current_positions,
                new_holdings=final_holdings,
                total_capital=total_capital,
            )
        except Exception as e:
            logger.error(f"❌ 换手控制异常: {e}")
            final_holdings = optimized_holdings.copy()
            trading_costs = {'turnover': 0.0, 'cost_pct': 0.0, 'cost_amount': 0.0, 'fee_rate': 0.0}

        # 6. 换手后权重量放（逻辑在 PortfolioOptimizer，此处只取 cap 参数）
        if (
            not final_holdings.empty
            and 'weight' in final_holdings.columns
        ):
            po_cfg = cfg.get('portfolio_optimization') or {}
            port_cfg = cfg.get('portfolio') or {}
            cap = float(po_cfg.get('max_weight_per_stock', port_cfg.get('max_weight_per_stock', 1.0)))
            fill_full = bool(po_cfg.get('fill_weights_to_position_scale'))
            fill_min_names = int(po_cfg.get("fill_weights_min_names", 0) or 0)
            if fill_full and fill_min_names > 0 and len(final_holdings) < fill_min_names:
                fill_full = False
            base_single_cap = float(po_cfg.get('max_single_stock_cap', port_cfg.get('max_single_stock_cap', 1.0 if fill_full else cap)))
            by_grade = po_cfg.get('single_stock_cap_by_grade') or {}
            by_regime_cap = po_cfg.get('single_stock_cap_by_regime') or {}
            mg_key = str(market_grade).lower()
            reg_key = str(regime).lower()
            if mg_key in by_grade:
                single_cap = float(by_grade[mg_key])
            elif reg_key in by_regime_cap:
                single_cap = float(by_regime_cap[reg_key])
            else:
                single_cap = base_single_cap
            wsum = float(final_holdings['weight'].astype(float).sum())
            final_holdings = self._portfolio_optimizer.rescale_to_target(
                final_holdings,
                cap=cap,
                fill_full=fill_full,
                single_cap=single_cap,
            )
            new_sum = float(final_holdings['weight'].astype(float).sum())
            if abs(new_sum - wsum) > 1e-4:
                tag = "打满目标权重" if fill_full else "权重归一化"
                eff_cap = single_cap if fill_full else cap
                logger.info(
                    f"📈 {tag}: {wsum:.2%} → {new_sum:.2%}"
                    f"（n={len(final_holdings)}, cap={eff_cap:.2%}）"
                )

        position_scale = 1.0
        if self._position_scale_in_strategy(params_root):
            m1_default = None
            if not signals_df.empty:
                for col in ("position_scale", "position_scale_factor"):
                    if col in signals_df.columns:
                        try:
                            m1_default = float(signals_df[col].iloc[0])
                            break
                        except (TypeError, ValueError):
                            pass
            position_scale = self._resolve_market_position_scale(
                market_grade,
                params_root,
                regime=regime,
                m1_default_scale=m1_default,
            )
        else:
            scale_col = None
            for col in ("position_scale", "position_scale_factor"):
                if col in signals_df.columns and not signals_df.empty:
                    scale_col = col
                    break
            if scale_col:
                try:
                    position_scale = float(signals_df[scale_col].iloc[0])
                except (TypeError, ValueError):
                    position_scale = 1.0
            elif signals_df.attrs.get("position_scale") is not None:
                position_scale = float(signals_df.attrs.get("position_scale", 1.0))
            elif signals_df.attrs.get("position_scale_factor") is not None:
                position_scale = float(
                    signals_df.attrs.get("position_scale_factor", 1.0)
                )
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
        self._pipeline_result = summary
        logger.info(
            f"✅ 完成: signals={len(signals_df)} opt={len(optimized_holdings)} "
            f"final={len(final_holdings)} turnover={trading_costs['turnover']:.2%}"
        )
        return summary

    @staticmethod
    def _resolve_effective_top_k(cfg: Dict[str, Any], regime: str = "neutral") -> int:
        """从 final_score.by_regime 解析当日 effective top_k。"""
        fs = cfg.get("final_score") or {}
        by_regime = fs.get("by_regime") or {}
        sub = by_regime.get(regime) or by_regime.get(str(regime))
        if isinstance(sub, dict) and sub.get("top_k") is not None:
            return int(sub["top_k"])
        return int(fs.get("top_k", 5))

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
    def _f35_map_from_signals(
        signals_df: Optional[pd.DataFrame],
        field: str = "f35_bollinger_trend",
    ) -> Dict[str, float]:
        out: Dict[str, float] = {}
        if signals_df is None or signals_df.empty or field not in signals_df.columns:
            return out
        for _, row in signals_df.drop_duplicates("code").iterrows():
            try:
                out[str(row["code"])] = float(row.get(field, float("nan")))
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _clip_scale(x: float) -> float:
        return max(0.0, min(1.0, float(x)))

    @staticmethod
    def _strategy_blocks_new_on_red(params: Dict[str, Any]) -> bool:
        ms = params.get("market_selection") or {}
        return bool(ms.get("strategy_blocks_new_on_red", True))

    @staticmethod
    def _position_scale_in_strategy(params: Dict[str, Any]) -> bool:
        ms = params.get("market_selection") or {}
        return str(ms.get("position_scale_applied_in", "factor")).strip().lower() == "strategy"

    @staticmethod
    def _resolve_market_position_scale(
        grade: str,
        params: Dict[str, Any],
        *,
        regime: str = "neutral",
        m1_default_scale: Optional[float] = None,
    ) -> float:
        """最终 position_scale（0~1）。

        优先级：red→0 → target_equity.by_context → market_overlay → M1 默认。
        """
        grade = str(grade or "unknown").lower()
        regime = str(regime or "neutral").lower()

        if grade == "red":
            return 0.0

        te = params.get("target_equity") or {}
        if te.get("enabled"):
            by_ctx = te.get("by_context") or {}
            key = f"{regime}_{grade}"
            if key in by_ctx:
                return StrategyPipeline._clip_scale(by_ctx[key])

        overlay = params.get("market_overlay") or {}
        mf = params.get("market_factors") or {}

        by_regime = overlay.get("by_regime") or {}
        sub = by_regime.get(regime) or by_regime.get(str(regime))
        if isinstance(sub, dict):
            okey = {
                "green": "position_scale_green",
                "yellow": "position_scale_yellow",
                "red": "position_scale_red",
            }.get(grade, "")
            if okey and sub.get(okey) is not None:
                return StrategyPipeline._clip_scale(sub[okey])

        okey = {
            "green": "position_scale_green",
            "yellow": "position_scale_yellow",
            "red": "position_scale_red",
        }.get(grade, "")
        if okey and overlay.get(okey) is not None:
            return StrategyPipeline._clip_scale(overlay[okey])

        if m1_default_scale is not None:
            return StrategyPipeline._clip_scale(m1_default_scale)

        if grade == "green":
            return StrategyPipeline._clip_scale(float(mf.get("scale_green", 1.0)))
        if grade == "yellow":
            return StrategyPipeline._clip_scale(
                float(mf.get("scale_yellow", overlay.get("position_scale_yellow", 0.5)))
            )
        return 1.0

    @staticmethod
    def _should_reduce_only(
        cfg: Optional[Dict[str, Any]], regime: str, market_grade: str
    ) -> bool:
        root = cfg or {}
        if not root.get("enabled", False):
            return False
        ro = root.get("reduce_only") or {}
        if not ro.get("enabled", False):
            return False
        regimes = {str(x).lower() for x in (ro.get("when_regime") or ["reversal"])}
        grades = {str(x).lower() for x in (ro.get("when_market_grade") or ["yellow"])}
        return str(regime).lower() in regimes and str(market_grade).lower() in grades

    @staticmethod
    def _apply_reduce_only_filter(
        target_holdings: pd.DataFrame,
        current_positions: Optional[pd.DataFrame],
        force_exit_codes: Optional[Set[str]] = None,
    ) -> pd.DataFrame:
        """只减不加：无持仓不建仓；有持仓仅保留当前 ∩ 目标（强制退出除外）。"""
        force_exit_codes = force_exit_codes or set()
        if current_positions is None or current_positions.empty:
            return pd.DataFrame(columns=["code", "weight", "score"])

        cur_codes = set(current_positions["code"].astype(str))
        if target_holdings is None or target_holdings.empty:
            keep = current_positions[~current_positions["code"].isin(force_exit_codes)].copy()
            return keep if not keep.empty else pd.DataFrame(columns=["code", "weight", "score"])

        df = target_holdings.copy()
        df["code"] = df["code"].astype(str)
        filtered = df[df["code"].isin(cur_codes) & ~df["code"].isin(force_exit_codes)].copy()
        return filtered.reset_index(drop=True)

    @staticmethod
    def _trend_cfg(exit_rules: Dict[str, Any]) -> Dict[str, Any]:
        te = (exit_rules or {}).get("trend_exit") or {}
        return te if isinstance(te, dict) else {}

    @staticmethod
    def _trend_exit_enabled(
        exit_rules: Dict[str, Any],
        regime: Optional[str] = None,
    ) -> bool:
        te = StrategyPipeline._trend_cfg(exit_rules)
        if not te.get("enabled"):
            return False
        when = te.get("when_regimes")
        if when and regime is not None:
            allowed = {str(x) for x in when}
            if str(regime) not in allowed:
                return False
        return True

    @staticmethod
    def _no_new_high(
        ohlcv: pd.DataFrame,
        trade_date: str,
        lookback: int,
    ) -> bool:
        if ohlcv is None or ohlcv.empty or lookback < 2:
            return False
        df = ohlcv.copy()
        if "trade_date" in df.columns:
            df = df.sort_values("trade_date")
            df = df[df["trade_date"].astype(str) <= str(trade_date)]
        else:
            df = df.sort_index()
        if len(df) < lookback:
            return False
        tail = df.tail(lookback)
        highs = tail["high"].astype(float) if "high" in tail.columns else tail["close"].astype(float)
        closes = tail["close"].astype(float)
        today_close = float(closes.iloc[-1])
        prior_peak = float(highs.iloc[:-1].max()) if len(highs) > 1 else float(highs.iloc[0])
        return today_close < prior_peak - 1e-9

    @staticmethod
    def _bollinger_weak(
        f35: float,
        *,
        below: float,
        prev_f35: Optional[float],
        require_declining: bool,
    ) -> bool:
        if not np.isfinite(f35):
            return False
        if f35 < below:
            return True
        if require_declining and prev_f35 is not None and np.isfinite(prev_f35):
            return f35 < prev_f35 - 1e-9
        return False

    @staticmethod
    def _should_trend_exit(
        *,
        ohlcv: Optional[pd.DataFrame],
        trade_date: str,
        f35: float,
        prev_f35: Optional[float],
        trend_cfg: Dict[str, Any],
    ) -> bool:
        if not trend_cfg.get("enabled"):
            return False
        lookback = int(trend_cfg.get("no_new_high_lookback", 20))
        below = float(trend_cfg.get("bollinger_trend_below", 40.0))
        require_both = bool(trend_cfg.get("require_both", True))
        require_declining = bool(trend_cfg.get("bollinger_require_declining", False))

        nnh = (
            StrategyPipeline._no_new_high(ohlcv, trade_date, lookback)
            if ohlcv is not None
            else False
        )
        boll = StrategyPipeline._bollinger_weak(
            f35,
            below=below,
            prev_f35=prev_f35,
            require_declining=require_declining,
        )
        if require_both:
            return nnh and boll
        return nnh or boll

    @staticmethod
    def _trend_force_exit_codes(
        held_codes: Set[str],
        trade_date: str,
        exit_rules: Dict[str, Any],
        *,
        ohlcv_by_code: Dict[str, pd.DataFrame],
        f35_by_code: Dict[str, float],
        prev_f35_by_code: Optional[Dict[str, float]] = None,
        regime: Optional[str] = None,
    ) -> Set[str]:
        if not StrategyPipeline._trend_exit_enabled(exit_rules, regime=regime) or not held_codes:
            return set()
        trend_cfg = StrategyPipeline._trend_cfg(exit_rules)
        out: Set[str] = set()
        prev_f35_by_code = prev_f35_by_code or {}
        for code in held_codes:
            f35 = float(f35_by_code.get(code, float("nan")))
            prev = prev_f35_by_code.get(code)
            if StrategyPipeline._should_trend_exit(
                ohlcv=ohlcv_by_code.get(code),
                trade_date=trade_date,
                f35=f35,
                prev_f35=prev,
                trend_cfg=trend_cfg,
            ):
                out.add(str(code))
        return out

    @staticmethod
    def _default_config() -> Dict[str, Dict]:
        """
        从 strategy_params.json 派生运行态配置（含弱段保护 / overlay）。
        结果缓存于类变量；派生逻辑在 strategy_config.build_runtime_config()。
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
