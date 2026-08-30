"""
策略每日信号服务

与回测共用同一条信号链路：
  CrossSectionStrategyPipeline.score_signals()  → StrategyPipeline.execute_full_pipeline()

API 路径通过 SignalGenerator.generate_signals() 调用 score_signals（预计算优先）。
一致性校验对比「回测直调 score_signals」与「API/SignalGenerator」及完整管道输出。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from zstock.common.config.strategy_config import (
    build_runtime_config,
    load_strategy_params,
)
from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.strategy_management.signal_generator import SignalGenerator

logger = logging.getLogger(__name__)

_SIGNAL_COLUMNS = (
    "code",
    "sector_code",
    "rank",
    "signal_type",
    "final_score",
    "dragon_score",
    "force_composite_score",
    "market_grade",
    "position_scale",
    "trade_date",
)


class StrategySignalService:
    """策略信号查询与回测一致性校验。"""

    def __init__(self) -> None:
        self._factor_pipeline: Optional[CrossSectionStrategyPipeline] = None
        self._signal_generator: Optional[SignalGenerator] = None
        self._strategy_pipeline: Optional[StrategyPipeline] = None
        self._params_cache: Optional[Dict[str, Any]] = None
        self._runtime_config_cache: Optional[Dict[str, Dict]] = None

    @property
    def factor_pipeline(self) -> CrossSectionStrategyPipeline:
        if self._factor_pipeline is None:
            self._factor_pipeline = CrossSectionStrategyPipeline()
        return self._factor_pipeline

    @property
    def signal_generator(self) -> SignalGenerator:
        if self._signal_generator is None:
            self._signal_generator = SignalGenerator(self.factor_pipeline)
        return self._signal_generator

    @property
    def strategy_pipeline(self) -> StrategyPipeline:
        if self._strategy_pipeline is None:
            self._strategy_pipeline = StrategyPipeline(self.signal_generator)
        return self._strategy_pipeline

    def load_strategy_params(self) -> Dict[str, Any]:
        if self._params_cache is not None:
            return self._params_cache
        self._params_cache = load_strategy_params()
        return self._params_cache

    def _load_runtime_config(self, override: Optional[Dict] = None) -> Dict[str, Dict]:
        """加载运行时配置（从 strategy_params 合并）。

        派生逻辑统一委托给 zstock.common.config.strategy_config.build_runtime_config()。
        """
        params = override or self.load_strategy_params()
        return build_runtime_config(params)

    def get_strategy_meta(self) -> Dict[str, Any]:
        params = self.load_strategy_params()
        return {
            "strategy_name": params.get("strategy_name", ""),
            "version": params.get("version", ""),
            "description": params.get("description", ""),
            "scoring_method": params.get("scoring_method", ""),
            "backtest": params.get("backtest", {}),
            "adaptive_rebalance": params.get("adaptive_rebalance", {}),
        }

    async def get_daily_signals(
        self,
        trade_date: str,
        *,
        include_targets: bool = False,
        prefer_precomputed: bool = True,
        include_watch: bool = False,
    ) -> Dict[str, Any]:
        """
        获取指定交易日截面信号（与回测 score_signals 同 schema）。

        include_targets=True 时额外跑 StrategyPipeline（无当前持仓），返回目标权重。
        """
        td = trade_date
        source = "precomputed"
        try:
            signals_df = await self.signal_generator.generate_signals(
                trade_date=td,
                prefer_precomputed=prefer_precomputed,
            )
        except ValueError:
            if not prefer_precomputed:
                raise
            source = "live"
            signals_df = await self.signal_generator.generate_signals(
                trade_date=td,
                prefer_precomputed=False,
            )

        meta = self._extract_signal_meta(signals_df, td, source)
        buy_df = self._filter_buy(signals_df)
        payload: Dict[str, Any] = {
            "meta": meta,
            "buy_signals": self._dataframe_to_records(buy_df),
        }
        if include_watch:
            watch_df = signals_df
            if "signal_type" in signals_df.columns:
                watch_df = signals_df[signals_df["signal_type"] != "buy"]
            payload["watch_signals"] = self._dataframe_to_records(watch_df)

        if include_targets:
            runtime_cfg = self._load_runtime_config(self.load_strategy_params())
            summary = await self.strategy_pipeline.execute_full_pipeline(
                trade_date=td,
                config=runtime_cfg,
                precomputed_signals=signals_df,
            )
            targets = summary.get("results", {}).get("final_holdings", pd.DataFrame())
            payload["pipeline_summary"] = {
                "status": summary.get("status"),
                "regime": summary.get("regime"),
                "market_grade": summary.get("market_grade"),
                "effective_top_k": summary.get("effective_top_k"),
                "reduce_only": summary.get("reduce_only"),
                "position_scale": summary.get("position_scale"),
                "statistics": summary.get("statistics"),
            }
            payload["targets"] = self._holdings_to_records(targets)

        return payload

    async def validate_consistency(
        self,
        trade_date: str,
        *,
        score_tolerance: float = 1e-6,
        include_pipeline: bool = True,
        prefer_precomputed: bool = True,
    ) -> Dict[str, Any]:
        """
        回测一致性校验：
        1. backtest_path: factor_pipeline.score_signals()（回测快路径）
        2. api_path: SignalGenerator.generate_signals()（API 路径）
        3. pipeline: execute_full_pipeline(precomputed) vs execute_full_pipeline(内部 generate)
        """
        td = trade_date
        runtime_cfg = self._load_runtime_config(self.load_strategy_params())

        backtest_df = await self.factor_pipeline.score_signals(td)
        api_df = await self.signal_generator.generate_signals(
            trade_date=td,
            prefer_precomputed=prefer_precomputed,
        )

        signal_diffs = self._compare_signal_frames(
            backtest_df, api_df, score_tolerance=score_tolerance
        )
        checks: Dict[str, Any] = {
            "signals_backtest_vs_api": {
                "passed": len(signal_diffs) == 0,
                "backtest_universe": len(backtest_df),
                "api_universe": len(api_df),
                "backtest_buy_count": len(self._filter_buy(backtest_df)),
                "api_buy_count": len(self._filter_buy(api_df)),
            },
        }

        pipeline_diffs: List[Dict[str, Any]] = []
        if include_pipeline:
            summary_bt = await self.strategy_pipeline.execute_full_pipeline(
                trade_date=td,
                config=runtime_cfg,
                precomputed_signals=backtest_df,
            )
            summary_api = await self.strategy_pipeline.execute_full_pipeline(
                trade_date=td,
                config=runtime_cfg,
            )
            pipeline_diffs = self._compare_pipeline_summaries(
                summary_bt, summary_api, weight_tolerance=score_tolerance
            )
            checks["pipeline_precomputed_vs_generator"] = {
                "passed": len(pipeline_diffs) == 0,
                "backtest_status": summary_bt.get("status"),
                "generator_status": summary_api.get("status"),
            }

        all_diffs = signal_diffs + pipeline_diffs
        return {
            "trade_date": td,
            "strategy_version": self.load_strategy_params().get("version", ""),
            "consistent": len(all_diffs) == 0,
            "checks": checks,
            "diffs": all_diffs,
        }

    @staticmethod
    def _extract_signal_meta(
        signals_df: pd.DataFrame, trade_date: str, source: str
    ) -> Dict[str, Any]:
        attrs = getattr(signals_df, "attrs", {}) or {}
        grade = StrategyPipeline._extract_market_grade(signals_df)
        scale = attrs.get("position_scale")
        if scale is None:
            scale = attrs.get("position_scale_factor", 1.0)
        if signals_df is not None and not signals_df.empty:
            if "position_scale" in signals_df.columns:
                try:
                    scale = float(signals_df["position_scale"].iloc[0])
                except (TypeError, ValueError):
                    pass
        buy_count = len(StrategySignalService._filter_buy(signals_df))
        return {
            "trade_date": trade_date,
            "source": source,
            "regime": str(attrs.get("regime", "neutral")),
            "market_grade": grade,
            "position_scale": float(scale if scale is not None else 1.0),
            "top_k": int(attrs.get("top_k", buy_count)),
            "universe_count": len(signals_df),
            "buy_count": buy_count,
        }

    @staticmethod
    def _filter_buy(signals_df: pd.DataFrame) -> pd.DataFrame:
        if signals_df is None or signals_df.empty:
            return pd.DataFrame()
        if "signal_type" in signals_df.columns:
            buy = signals_df[signals_df["signal_type"] == "buy"]
            if not buy.empty:
                return buy
        return signals_df

    @staticmethod
    def _dataframe_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []
        cols = [c for c in _SIGNAL_COLUMNS if c in df.columns]
        out = df[cols].copy()
        out = out.replace({np.nan: None})
        records = out.to_dict(orient="records")
        for row in records:
            for k, v in list(row.items()):
                if isinstance(v, (np.floating, float)):
                    row[k] = float(v)
                elif isinstance(v, (np.integer, int)) and k == "rank":
                    row[k] = int(v)
        return records

    @staticmethod
    def _holdings_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []
        out = df.copy()
        out = out.replace({np.nan: None})
        records = out.to_dict(orient="records")
        for row in records:
            for k, v in list(row.items()):
                if isinstance(v, (np.floating, float)):
                    row[k] = float(v)
        return records

    @staticmethod
    def _compare_signal_frames(
        backtest_df: pd.DataFrame,
        api_df: pd.DataFrame,
        *,
        score_tolerance: float,
    ) -> List[Dict[str, Any]]:
        diffs: List[Dict[str, Any]] = []

        def _add(field: str, bv: Any, av: Any) -> None:
            diffs.append({"field": field, "backtest_value": bv, "api_value": av})

        for key in ("regime", "market_grade", "top_k"):
            bv = backtest_df.attrs.get(key) if hasattr(backtest_df, "attrs") else None
            av = api_df.attrs.get(key) if hasattr(api_df, "attrs") else None
            if bv is None and not backtest_df.empty and key in backtest_df.columns:
                bv = backtest_df[key].iloc[0]
            if av is None and not api_df.empty and key in api_df.columns:
                av = api_df[key].iloc[0]
            if str(bv) != str(av):
                _add(f"attrs.{key}", bv, av)

        for key in ("position_scale", "position_scale_factor"):
            bv = backtest_df.attrs.get(key) if hasattr(backtest_df, "attrs") else None
            av = api_df.attrs.get(key) if hasattr(api_df, "attrs") else None
            try:
                if abs(float(bv or 0) - float(av or 0)) > score_tolerance:
                    _add(f"attrs.{key}", bv, av)
            except (TypeError, ValueError):
                if bv != av:
                    _add(f"attrs.{key}", bv, av)

        buy_bt = StrategySignalService._filter_buy(backtest_df)
        buy_api = StrategySignalService._filter_buy(api_df)
        codes_bt = list(buy_bt["code"].astype(str)) if "code" in buy_bt.columns else []
        codes_api = list(buy_api["code"].astype(str)) if "code" in buy_api.columns else []
        if codes_bt != codes_api:
            _add("buy_codes", codes_bt, codes_api)

        if "code" not in backtest_df.columns or "code" not in api_df.columns:
            return diffs

        common = set(backtest_df["code"].astype(str)) & set(api_df["code"].astype(str))
        bt_idx = backtest_df.set_index(backtest_df["code"].astype(str))
        api_idx = api_df.set_index(api_df["code"].astype(str))

        for code in sorted(common):
            for col in ("rank", "final_score", "dragon_score", "force_composite_score"):
                if col not in bt_idx.columns or col not in api_idx.columns:
                    continue
                bv, av = bt_idx.at[code, col], api_idx.at[code, col]
                try:
                    if abs(float(bv) - float(av)) > score_tolerance:
                        _add(f"{code}.{col}", float(bv), float(av))
                except (TypeError, ValueError):
                    if bv != av:
                        _add(f"{code}.{col}", bv, av)

        return diffs

    @staticmethod
    def _compare_pipeline_summaries(
        summary_bt: Dict[str, Any],
        summary_api: Dict[str, Any],
        *,
        weight_tolerance: float,
    ) -> List[Dict[str, Any]]:
        diffs: List[Dict[str, Any]] = []

        def _add(field: str, bv: Any, av: Any) -> None:
            diffs.append({"field": field, "backtest_value": bv, "api_value": av})

        for key in ("status", "regime", "market_grade", "effective_top_k", "reduce_only"):
            bv, av = summary_bt.get(key), summary_api.get(key)
            if bv != av:
                _add(f"pipeline.{key}", bv, av)

        ps_bt = summary_bt.get("position_scale")
        ps_api = summary_api.get("position_scale")
        try:
            if abs(float(ps_bt or 0) - float(ps_api or 0)) > weight_tolerance:
                _add("pipeline.position_scale", ps_bt, ps_api)
        except (TypeError, ValueError):
            if ps_bt != ps_api:
                _add("pipeline.position_scale", ps_bt, ps_api)

        hold_bt = summary_bt.get("results", {}).get("final_holdings", pd.DataFrame())
        hold_api = summary_api.get("results", {}).get("final_holdings", pd.DataFrame())
        codes_bt = (
            sorted(hold_bt["code"].astype(str).tolist()) if not hold_bt.empty else []
        )
        codes_api = (
            sorted(hold_api["code"].astype(str).tolist()) if not hold_api.empty else []
        )
        if codes_bt != codes_api:
            _add("pipeline.final_holdings.codes", codes_bt, codes_api)
            return diffs

        if hold_bt.empty:
            return diffs

        bt_w = hold_bt.set_index(hold_bt["code"].astype(str))["weight"].astype(float)
        api_w = hold_api.set_index(hold_api["code"].astype(str))["weight"].astype(float)
        for code in codes_bt:
            try:
                if abs(float(bt_w[code]) - float(api_w[code])) > weight_tolerance:
                    _add(f"pipeline.weight.{code}", float(bt_w[code]), float(api_w[code]))
            except (KeyError, TypeError, ValueError):
                _add(f"pipeline.weight.{code}", bt_w.get(code), api_w.get(code))

        return diffs


_strategy_signal_service: Optional[StrategySignalService] = None


def get_strategy_signal_service() -> StrategySignalService:
    global _strategy_signal_service
    if _strategy_signal_service is None:
        _strategy_signal_service = StrategySignalService()
    return _strategy_signal_service
