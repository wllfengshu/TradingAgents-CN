"""
策略配置单一访问层（Single Source of Truth）。

所有策略参数都从 zstock/common/config/strategy_params.json 读取，各层禁止自行写死。

对外提供：
- load_strategy_params()         读并缓存原始 JSON
- build_runtime_config()         派生 pipeline 各阶段配置（portfolio/risk/turnover/exit）
- build_risk_limits()            派生风控 limits

设计约定：
- 兜底默认值必须与 strategy_params.json 保持一致，避免「配置缺失时静默回退到错误值」。
- 各层（pipeline / signal_service / backtester / risk_manager）都委托到本模块，
  不再各自实现一套读取 + 拼接逻辑。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STRATEGY_PARAMS_PATH = Path(__file__).resolve().parent / "strategy_params.json"

_params_cache: Optional[Dict[str, Any]] = None


def _clear_cache() -> None:
    """测试夹具用：清空缓存，强制下次重新读盘。"""
    global _params_cache
    _params_cache = None


def load_strategy_params() -> Dict[str, Any]:
    """读原始策略参数 JSON（进程内缓存，读盘一次）。"""
    global _params_cache
    if _params_cache is not None:
        return _params_cache

    params: Dict[str, Any] = {}
    try:
        with open(_STRATEGY_PARAMS_PATH, "r", encoding="utf-8") as f:
            params = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("❌ 加载策略参数失败: %s, %s", _STRATEGY_PARAMS_PATH, e)
    _params_cache = params
    return params


def build_runtime_config(params: Optional[Dict[str, Any]] = None) -> Dict[str, Dict]:
    """由原始 JSON 派生 pipeline 各阶段配置结构。

    结构与历史 _default_config() 保持一致，供 StrategyPipeline /
    StrategySignalService / backtester 复用。
    """
    params = params if params is not None else load_strategy_params()

    final_score = params.get("final_score") or {}
    top_k = int(final_score.get("top_k", 5))
    portfolio = params.get("portfolio") or {}
    tov = params.get("turnover_control") or {}
    exit_rules = params.get("exit_rules") or {}
    backtest = params.get("backtest") or {}

    return {
        "portfolio_optimization": {
            "min_holdings": max(1, top_k - 2),
            "max_holdings": top_k,
            "max_weight_per_stock": float(
                portfolio.get("max_weight_per_stock", round(1.0 / max(top_k, 1), 2))
            ),
            "weighting": "score",
        },
        "risk_management": {
            "hard_stop_loss_pct": float(exit_rules.get("hard_stop_loss_pct", -0.08)),
            "max_sector_exposure": float(portfolio.get("max_sector_exposure", 0.4)),
        },
        "turnover_control": {
            "buffer_threshold": float(tov.get("buffer_threshold", 0.25)),
            "min_hold_days": int(tov.get("min_hold_days", 3)),
            "fee_rate": float(backtest.get("fee_rate", 0.0015)),
        },
        "exit_rules": {
            "hard_stop_loss_pct": float(exit_rules.get("hard_stop_loss_pct", -0.08)),
            "rank_percentile_threshold": float(
                exit_rules.get("rank_percentile_threshold", 0.85)
            ),
            # 兼容旧字段名 consecutive_days_out_of_top3；默认与 JSON 一致为 3
            "consecutive_days_out_of_candidates": int(
                exit_rules.get(
                    "consecutive_days_out_of_candidates",
                    exit_rules.get("consecutive_days_out_of_top3", 3),
                )
            ),
            "flat_after_bad_days": int(exit_rules.get("flat_after_bad_days", 5)),
            "no_signal_action": str(exit_rules.get("no_signal_action", "hold")),
            "no_signal_reduce_scale": float(
                exit_rules.get("no_signal_reduce_scale", 0.5)
            ),
        },
    }


def build_risk_limits(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """由原始 JSON 派生风控 limits（与 RiskManager 期望的结构一致）。"""
    params = params if params is not None else load_strategy_params()

    final_score = params.get("final_score") or {}
    top_k = int(final_score.get("top_k", 5))
    top_sectors = int((params.get("sector_layer") or {}).get("top_sectors", 4))
    portfolio = params.get("portfolio") or {}

    return {
        "top_k": top_k,
        "min_holdings": max(1, top_k - 2),
        "max_holdings": top_k * 2,  # 允许 buffer / 最短持有保留旧持仓
        "max_weight_per_stock": float(
            portfolio.get("max_weight_per_stock", round(1.0 / max(top_k, 1), 2))
        ),
        "max_top5_concentration": 1.0,  # top_k=5 时不做 top5 集中度限制
        "max_sector_exposure": float(portfolio.get("max_sector_exposure", 0.4)),
        "weight_sum_tolerance": 1e-3,
        "allow_cash": True,
    }
