"""截面持仓：把打分结果变成股票权重，并处理退出、换手和仓位缩放。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Set

import numpy as np
import pandas as pd

from zstock.common.book_codes import BOOK_OHLCV_CODES
from zstock.common.utils.common_utils import normalize_code
from zstock.strategy_management.strategy_section import exit as exit_policy
from zstock.strategy_management.market_book import book_enabled, park_attack_idle
from zstock.strategy_management.strategy_section.optimizer import PortfolioOptimizer
from zstock.strategy_management.strategy_section.risk import RiskManager
from zstock.strategy_management.turnover_controller import TurnoverController

logger = logging.getLogger(__name__)

def clip_scale(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def resolve_effective_top_k(cfg: Dict[str, Any], regime: str = "neutral") -> int:
    fs = cfg.get("final_score") or {}
    by_regime = fs.get("by_regime") or {}
    sub = by_regime.get(regime) or by_regime.get(str(regime))
    if isinstance(sub, dict) and sub.get("top_k") is not None:
        return int(sub["top_k"])
    return int(fs.get("top_k", 5))


def extract_market_grade(signals_df: Optional[pd.DataFrame]) -> str:
    if signals_df is None:
        return "unknown"
    grade = signals_df.attrs.get("market_grade")
    if grade:
        return str(grade)
    if not signals_df.empty and "market_grade" in signals_df.columns:
        val = signals_df["market_grade"].iloc[0]
        if val is not None and str(val) != "nan":
            return str(val)
    return "unknown"


def strategy_blocks_new_on_red(params: Dict[str, Any]) -> bool:
    ms = params.get("market_selection") or {}
    return bool(ms.get("strategy_blocks_new_on_red", True))


def position_scale_in_strategy(params: Dict[str, Any]) -> bool:
    ms = params.get("market_selection") or {}
    return str(ms.get("position_scale_applied_in", "factor")).strip().lower() == "strategy"


def resolve_market_position_scale(
    grade: str,
    params: Dict[str, Any],
    *,
    regime: str = "neutral",
    m1_default_scale: Optional[float] = None,
) -> float:
    grade = str(grade or "unknown").lower()
    regime = str(regime or "neutral").lower()
    if grade == "red":
        return 0.0

    te = params.get("target_equity") or {}
    if te.get("enabled"):
        by_ctx = te.get("by_context") or {}
        key = f"{regime}_{grade}"
        if key in by_ctx:
            return clip_scale(by_ctx[key])

    overlay = params.get("market_overlay") or {}
    mf = params.get("market_factors") or {}
    by_regime = overlay.get("by_regime") or {}
    sub = by_regime.get(regime) or by_regime.get(str(regime))
    okey = {
        "green": "position_scale_green",
        "yellow": "position_scale_yellow",
        "red": "position_scale_red",
    }.get(grade, "")
    if isinstance(sub, dict) and okey and sub.get(okey) is not None:
        return clip_scale(sub[okey])
    if okey and overlay.get(okey) is not None:
        return clip_scale(overlay[okey])
    if m1_default_scale is not None:
        return clip_scale(m1_default_scale)
    if grade == "green":
        return clip_scale(float(mf.get("scale_green", 1.0)))
    if grade == "yellow":
        return clip_scale(
            float(mf.get("scale_yellow", overlay.get("position_scale_yellow", 0.5)))
        )
    return 1.0


def should_reduce_only(
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


def apply_reduce_only_filter(
    target_holdings: pd.DataFrame,
    current_positions: Optional[pd.DataFrame],
    force_exit_codes: Optional[Set[str]] = None,
) -> pd.DataFrame:
    force_exit_codes = force_exit_codes or set()
    if current_positions is None or current_positions.empty:
        return pd.DataFrame(columns=["code", "weight", "score"])
    cur_codes = set(current_positions["code"].astype(str))
    if target_holdings is None or target_holdings.empty:
        keep = current_positions[~current_positions["code"].isin(force_exit_codes)].copy()
        return (
            keep
            if not keep.empty
            else pd.DataFrame(columns=["code", "weight", "score"])
        )
    df = target_holdings.copy()
    df["code"] = df["code"].astype(str)
    filtered = df[
        df["code"].isin(cur_codes) & ~df["code"].isin(force_exit_codes)
    ].copy()
    return filtered.reset_index(drop=True)


def _attack_candidates(signals_df: pd.DataFrame, minimum: int = 3) -> pd.DataFrame:
    """进攻日取排序最前的股票，凑满 minimum 只；名单不够就全取。"""
    if signals_df is None or signals_df.empty or "code" not in signals_df.columns:
        return pd.DataFrame(columns=["code", "weight", "final_score"])
    frame = signals_df.copy()
    frame["code"] = frame["code"].map(lambda c: normalize_code(str(c)))
    blocked = set(BOOK_OHLCV_CODES)
    frame = frame[~frame["code"].isin(blocked)]
    if "rank" in frame.columns:
        frame = frame.sort_values("rank", ascending=True)
    elif "final_score" in frame.columns:
        frame = frame.sort_values("final_score", ascending=False)
    frame = frame.drop_duplicates(subset=["code"], keep="first")
    if frame.empty:
        return frame
    out = frame.head(max(1, int(minimum))).copy()
    out["signal_type"] = "buy"
    return out.reset_index(drop=True)

def run_equity_execution(
    *,
    trade_date: str,
    signals_df: pd.DataFrame,
    current_positions: Optional[pd.DataFrame],
    total_capital: float,
    cfg: Dict[str, Any],
    results: Dict[str, Any],
    portfolio_optimizer: PortfolioOptimizer,
    risk_manager: RiskManager,
    turnover_controller: TurnoverController,
    ohlcv_by_code: Optional[Dict[str, pd.DataFrame]] = None,
    prev_f35_by_code: Optional[Dict[str, float]] = None,
    decision: Any = None,
    asset_closes: Optional[Dict[str, Any]] = None,
    apply_position_scale: bool = True,
) -> Dict[str, Any]:
    """进攻日股票执行：组合 → 风控 → 退出 → 换手 → 缩放。

    apply_position_scale 为假时不按 target_equity 缩小股票权重。
    灯账户的绿灯要用截面自己的权重，空出来的部分才交给行业 ETF。
    """
    signal_grade = extract_market_grade(signals_df)
    style_regime = str(results.get("style_regime") or "neutral")
    if results.get("market_grade"):
        signal_grade = str(results["market_grade"])
    if "signal_type" in signals_df.columns:
        buy_df = signals_df[signals_df["signal_type"] == "buy"]
    else:
        buy_df = signals_df

    regime = style_regime
    market_grade = str(results.get("market_grade") or signal_grade or "yellow").lower()
    params_root = cfg

    on_attack = decision is not None and getattr(decision, "state", None) == "attack"
    if strategy_blocks_new_on_red(params_root) and market_grade == "red" and not on_attack:
        buy_df = buy_df.iloc[0:0]
        logger.info("🔴 信号红灯：禁止新开仓")
    if on_attack:
        # 账户已经是进攻：从排序名单里取满 3 只，不因信号黄灯把新股票丢掉。
        buy_df = _attack_candidates(signals_df, minimum=3)
        logger.info(f"进攻日候选 {len(buy_df)} 只（目标至少 3 只）")

    effective_top_k = resolve_effective_top_k(cfg, regime)
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
    if on_attack:
        port_cfg["min_holdings"] = max(3, int(port_cfg["min_holdings"]))
        port_cfg["max_holdings"] = max(3, int(port_cfg["max_holdings"]))
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
            opt_kwargs = {k: v for k, v in port_cfg.items() if k in valid_keys}
            opt_result = portfolio_optimizer.optimize_portfolio(
                signals_df=buy_df, **opt_kwargs
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
            return {
                "status": "optimization_failed",
                "trade_date": trade_date,
                "results": results,
            }
        optimized_holdings = opt_result["holdings_df"].copy()
    results["optimization"] = opt_result

    if (
        "sector_code" in signals_df.columns
        and "sector_code" not in optimized_holdings.columns
    ):
        mapping = signals_df.set_index("code")["sector_code"].to_dict()
        optimized_holdings["sector_code"] = (
            optimized_holdings["code"].map(mapping).fillna("UNKNOWN")
        )

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
        compliance, optimized_holdings = risk_manager.apply_corrections(
            optimized_holdings,
            signals_df,
            limits_override=risk_limits_override,
        )
    except Exception as e:
        logger.error(f"❌ 风控检查异常: {e}")
        compliance = {"status": "error", "issues": [str(e)], "metrics": {}}
    results["risk_check"] = compliance
    logger.info(
        f"🔍 风控状态: {compliance['status']} issues={len(compliance['issues'])}"
    )
    if compliance.get("status") != "passed":
        blocking = [
            i
            for i in (compliance.get("issues") or [])
            if ("权重和" in i)
            or ("单股权重" in i)
            or ("板块" in i and "暴露" in i)
        ]
        if blocking:
            logger.error("❌ 风控阻断: %s", blocking)
            return {
                "status": "risk_failed",
                "trade_date": trade_date,
                "results": results,
                "market_grade": results.get("market_grade"),
            }

    exit_rules = cfg.get("exit_rules") or {}
    force_exit_codes = exit_policy.rank_force_exit_codes(
        current_positions, signals_df, exit_rules
    )
    if (
        exit_policy.trend_exit_enabled(exit_rules, regime=regime)
        and current_positions is not None
        and not current_positions.empty
    ):
        held = set(current_positions["code"].astype(str))
        trend_cfg = exit_policy.trend_cfg(exit_rules)
        field = str(trend_cfg.get("bollinger_trend_field", "f35_bollinger_trend"))
        f35_map = exit_policy.f35_map_from_signals(signals_df, field=field)
        force_exit_codes = force_exit_codes | exit_policy.trend_force_exit_codes(
            held,
            trade_date,
            exit_rules,
            ohlcv_by_code=ohlcv_by_code or {},
            f35_by_code=f35_map,
            prev_f35_by_code=prev_f35_by_code,
            regime=regime,
        )
    if force_exit_codes:
        logger.info(f"🚪 强制卖出: {sorted(force_exit_codes)}")

    reduce_only = False
    if on_attack:
        reduce_only = False
    elif should_reduce_only(cfg.get("weak_regime_protection"), regime, market_grade):
        reduce_only = True
        optimized_holdings = apply_reduce_only_filter(
            optimized_holdings,
            current_positions,
            force_exit_codes=force_exit_codes,
        )
        logger.info(f"🛡️ reversal+yellow 只减不加: 目标 {len(optimized_holdings)} 只")

    try:
        tc_cfg = cfg.get("turnover_control") or {}
        final_holdings = turnover_controller.apply_buffer_mechanism(
            new_holdings=optimized_holdings,
            current_holdings=current_positions,
            buffer_threshold=tc_cfg.get("buffer_threshold", 0.25),
            min_hold_days=int(
                tc_cfg.get("min_hold_days", turnover_controller.min_hold_days)
            ),
            trade_date=trade_date,
            force_exit_codes=force_exit_codes,
            fill_open_slots=bool(tc_cfg.get("fill_open_slots", True)),
        )
        trading_costs = turnover_controller.estimate_trading_costs(
            current_holdings=current_positions,
            new_holdings=final_holdings,
            total_capital=total_capital,
        )
    except Exception as e:
        logger.error(f"❌ 换手控制异常: {e}")
        final_holdings = optimized_holdings.copy()
        trading_costs = {
            "turnover": 0.0,
            "cost_pct": 0.0,
            "cost_amount": 0.0,
            "fee_rate": 0.0,
        }

    if not final_holdings.empty and "weight" in final_holdings.columns:
        po_cfg = cfg.get("portfolio_optimization") or {}
        port_cfg2 = cfg.get("portfolio") or {}
        cap = float(
            po_cfg.get(
                "max_weight_per_stock",
                port_cfg2.get("max_weight_per_stock", 1.0),
            )
        )
        fill_full = bool(po_cfg.get("fill_weights_to_position_scale"))
        fill_min_names = int(po_cfg.get("fill_weights_min_names", 0) or 0)
        if fill_full and fill_min_names > 0 and len(final_holdings) < fill_min_names:
            fill_full = False
        base_single_cap = float(
            po_cfg.get(
                "max_single_stock_cap",
                port_cfg2.get("max_single_stock_cap", 1.0 if fill_full else cap),
            )
        )
        by_grade = po_cfg.get("single_stock_cap_by_grade") or {}
        by_regime_cap = po_cfg.get("single_stock_cap_by_regime") or {}
        mg_key = str(market_grade).lower()
        reg_key = str(regime).lower()
        if mg_key in by_grade:
            single_cap = float(by_grade[mg_key])
        elif reg_key in by_regime_cap:
            single_cap = float(by_regime_cap[reg_key])
        else:
            single_cap = base_single_cap
        # 风控单票上限是硬顶。打满仓位不能把 20% 再抬到 40%。
        single_cap = min(single_cap, cap)
        wsum = float(final_holdings["weight"].astype(float).sum())
        final_holdings = portfolio_optimizer.rescale_to_target(
            final_holdings,
            cap=cap,
            fill_full=fill_full,
            single_cap=single_cap,
        )
        new_sum = float(final_holdings["weight"].astype(float).sum())
        if abs(new_sum - wsum) > 1e-4:
            tag = "打满目标权重" if fill_full else "权重归一化"
            eff_cap = single_cap if fill_full else cap
            logger.info(
                f"📈 {tag}: {wsum:.2%} → {new_sum:.2%}"
                f"（n={len(final_holdings)}, cap={eff_cap:.2%}）"
            )

    position_scale = 1.0
    # 缩放在策略层时，进攻日也读 target_equity，不再固定满仓。
    # 缩放在因子层时，进攻日不再把信号里的缩放乘第二次。
    # 灯账户的绿灯不缩放，避免把挖出来的权重交给行业 ETF。
    book_cfg = (cfg.get("book_regime") or {}) if isinstance(cfg, dict) else {}
    full_attack = (
        on_attack
        and market_grade == "green"
        and str(book_cfg.get("attack_scale") or "target") == "full"
    )
    if not apply_position_scale or full_attack:
        position_scale = 1.0
    elif position_scale_in_strategy(params_root):
        m1_default = None
        if not signals_df.empty and "position_scale" in signals_df.columns:
            try:
                m1_default = float(signals_df["position_scale"].iloc[0])
            except (TypeError, ValueError):
                pass
        position_scale = resolve_market_position_scale(
            market_grade,
            params_root,
            regime=regime,
            m1_default_scale=m1_default,
        )
    elif book_enabled(cfg) and decision is not None and decision.state == "attack":
        position_scale = 1.0
    else:
        if "position_scale" in signals_df.columns and not signals_df.empty:
            try:
                position_scale = float(signals_df["position_scale"].iloc[0])
            except (TypeError, ValueError):
                position_scale = 1.0
        elif signals_df.attrs.get("position_scale") is not None:
            position_scale = float(signals_df.attrs.get("position_scale", 1.0))
    position_scale = float(np.clip(position_scale, 0.0, 1.0))
    if (
        not final_holdings.empty
        and position_scale < 1.0 - 1e-9
        and "weight" in final_holdings.columns
    ):
        final_holdings = final_holdings.copy()
        final_holdings["weight"] = (
            final_holdings["weight"].astype(float) * position_scale
        )
        logger.info(
            f"📉 市场仓位缩放 position_scale={position_scale:.2f}，"
            f"权益仓位={final_holdings['weight'].sum():.2%}"
        )

    if book_enabled(cfg) and decision is not None and decision.state == "attack":
        final_holdings = park_attack_idle(
            final_holdings, cfg, asset_closes, params_root
        )
        results["book_state"] = "attack"
    results["final_holdings"] = final_holdings
    results["trading_costs"] = trading_costs
    results["position_scale"] = position_scale
    results["force_exit_codes"] = sorted(force_exit_codes)

    summary = {
        "status": "success",
        "trade_date": trade_date,
        "execution_time": datetime.now().astimezone().isoformat(),
        "market_grade": results["market_grade"],
        "style_regime": regime,
        "regime": regime,
        "book_state": results.get("book_state"),
        "attack_idle": bool(results.get("attack_idle", False)),
        "effective_top_k": effective_top_k,
        "reduce_only": reduce_only,
        "statistics": {
            "signals_count": int(
                (buy_df["signal_type"] == "buy").sum()
                if "signal_type" in buy_df.columns
                else len(buy_df)
            ),
            "universe_count": len(signals_df),
            "optimized_holdings": len(optimized_holdings),
            "final_holdings": len(final_holdings),
            "risk_status": compliance["status"],
            "turnover": trading_costs["turnover"],
            "cost_pct": trading_costs["cost_pct"],
            "total_capital": total_capital,
            "position_scale": position_scale,
        },
        "results": results,
        "position_scale": position_scale,
    }
    logger.info(
        f"✅ 完成: signals={len(signals_df)} opt={len(optimized_holdings)} "
        f"final={len(final_holdings)} turnover={trading_costs['turnover']:.2%}"
    )
    return summary
