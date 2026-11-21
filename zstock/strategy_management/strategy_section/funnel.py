"""信号漏斗：M1→M5 共用编排（现场 / 预计算同一套打分）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from zstock.common.config.strategy_config import factor_regime_for_grade
from zstock.common.utils.common_utils import select_top_k
from zstock.factor_management.factors_dragon import DragonFactors
from zstock.factor_management.factors_force import ForceFactors
from zstock.factor_management.factors_sector import SectorFactors
from zstock.strategy_management.strategy_section import schema as signal_schema
from zstock.strategy_management.strategy_section.selection import (
    score_and_compose_final,
    score_dragons_from_raw,
    score_sectors_from_raw,
)

logger = logging.getLogger(__name__)


class SignalFunnelMixin:
    """截面打分。

    市场红灯则停止。否则确定风格，过滤股票池，再依次做板块、龙头、合力门禁和最终排序。
    现场用行情现算，预计算读已落库的因子，两条路径的打分规则相同。
    """

    # ===================== 主流程（现场 / 预计算共用）=====================
    # 唯一编排：M1 红灯 → 风格 → 宇宙过滤 → M2 → M3 → M4/M5。
    # source 只决定 raw 从哪来（OHLCV 现场算 vs Mongo 预计算文档），打分/门禁/TopK 同一套。

    async def _run_funnel(
        self,
        trade_date: str,
        sentiment: Dict[str, Any],
        *,
        source: str,
        index_df: Optional[pd.DataFrame] = None,
        all_stocks: List[str],
        stock_infos: Dict[str, Dict],
        sectors: List[Dict],
        stock_ohlcv: Optional[Dict[str, pd.DataFrame]] = None,
        sector_stocks: Optional[Dict[str, List[str]]] = None,
        stock_flow_recent: Optional[Dict[str, List[Dict]]] = None,
    ) -> Dict[str, Any]:
        """M1→M5 共用漏斗。返回 early_exit / ranked_all / funnel，由入口转 DataFrame 或 list。"""
        td = trade_date
        grade = str(sentiment.get("market_risk_level") or "yellow")
        m1_scale = float(sentiment.get("position_scale_factor", 1.0))

        if grade == "red" and self._factor_stops_on_red():
            scale = self._output_position_scale(grade, m1_scale, regime="neutral")
            logger.info(f"🔴 {td} 市场红灯，因子链终止")
            return {
                "early_exit": True,
                "exit_reason": "market_red_light",
                "trade_date": td,
                "market_risk_level": grade,
                "position_scale_factor": scale,
                "regime": "neutral",
                "market_composite_score": float(sentiment.get("market_composite_score", 0.0)),
                "ranked_all": [],
                "funnel": {"n_m3": 0, "n_m4": 0, "n_buy": 0, "stop": "red", "regime": "neutral"},
            }

        style_info = await self._detect_style(td, index_df)
        regime = str(style_info.get("regime") or "neutral")
        scale = self._output_position_scale(grade, m1_scale, regime=regime)
        factor_regime = factor_regime_for_grade(regime, grade)
        active = self._get_active_factors(factor_regime, grade)
        logger.info(
            f"🎨 {td} 市场风格: {regime} | "
            f"autocorr={style_info.get('autocorr', 0):.3f} "
            f"momentum_w={style_info.get('momentum_weight', 0.5):.2f} "
            f"reversal_w={style_info.get('reversal_weight', 0.5):.2f}"
        )

        umode = self._universe_mode()
        if umode == "full":
            filtered_stocks = set(all_stocks)
            filtered_sectors = list(sectors or [])
            logger.info(
                f"🔍 宇宙模式 full: 股 {len(filtered_stocks)} "
                f"板块 {len(filtered_sectors)}"
            )
        else:
            # 预计算路径通常无 OHLCV：禁止依赖价量的预过滤，保证与 live 主板过滤语义一致
            if source == "precomputed" and not stock_ohlcv:
                logger.info(
                    "预计算宇宙过滤不使用 OHLCV（apply_bollinger=False，仅元数据门禁）"
                )
            universe_res = self.prefilters.filter_universe(
                all_stocks,
                stock_infos=stock_infos,
                sectors=sectors,
                stock_data=stock_ohlcv or {},
                apply_main_board=True,
                apply_bollinger=False,
            )
            filtered_stocks = set(universe_res["stocks"])
            filtered_sectors = universe_res["sectors"]
            logger.info(
                f"🔍 宇宙模式 {umode}: 股 {len(all_stocks)}→{len(filtered_stocks)} "
                f"板块 {len(sectors or [])}→{len(filtered_sectors)}"
            )

        # ----- M2 -----
        sector_raw = await self._load_sector_raw(
            source=source,
            trade_date=td,
            filtered_sectors=filtered_sectors,
            sector_stocks=sector_stocks,
            stock_ohlcv=stock_ohlcv,
            stock_flow_recent=stock_flow_recent,
            filtered_stocks=filtered_stocks,
        )
        # full/prefilter：不截板块 topN；funnel：按配置 top_sectors
        sector_top_n = (
            10_000 if umode in ("full", "prefilter") else self._cfg_top_sectors()
        )
        m2_scores = score_sectors_from_raw(
            sector_raw,
            regime=factor_regime,
            active_factors=active,
            top_n=sector_top_n,
            gates=self._cfg_layer_gates("sector_layer"),
        )
        top_sectors = select_top_k(m2_scores, sector_top_n)
        logger.info(
            f"✅ M2 板块 mode={umode} n={len(top_sectors)}: "
            f"{[s[0] for s in top_sectors[:12]]}"
            f"{'…' if len(top_sectors) > 12 else ''}"
        )

        # ----- M3 -----
        dragon_by_sector = await self._load_dragon_raw_by_sector(
            source=source,
            trade_date=td,
            top_sectors=top_sectors,
            sector_stocks=sector_stocks,
            stock_ohlcv=stock_ohlcv,
            filtered_stocks=filtered_stocks,
        )
        top_per = self._cfg_top_per_sector()
        all_candidates: List[Dict] = []
        for sector_code, _ in top_sectors:
            dragon_raw = dragon_by_sector.get(sector_code) or {}
            dragon_raw = {k: v for k, v in dragon_raw.items() if k in filtered_stocks}
            if not dragon_raw:
                continue
            m3_scores = score_dragons_from_raw(
                dragon_raw,
                regime=factor_regime,
                active_factors=active,
                gates=self._cfg_layer_gates("dragon_layer"),
            )
            if not m3_scores:
                continue
            for code, m3_score in select_top_k(m3_scores, top_per):
                raw = dragon_raw.get(code) or {}
                f35 = raw.get("f35_bollinger_trend", float("nan"))
                try:
                    f35_f = float(f35)
                except (TypeError, ValueError):
                    f35_f = float("nan")
                all_candidates.append(
                    signal_schema.make_candidate(
                        code,
                        sector_code,
                        m3_score,
                        f35_bollinger_trend=f35_f,
                    )
                )
        all_candidates = signal_schema.dedupe_candidates_by_code(all_candidates)
        if hasattr(self, "_remember_dragon_fields"):
            self._remember_dragon_fields(
                td,
                {
                    str(c["code"]): {
                        "f35_bollinger_trend": float(
                            c.get("f35_bollinger_trend", float("nan"))
                        )
                    }
                    for c in all_candidates
                    if c.get("code")
                },
            )
        m3_codes = {str(c["code"]) for c in all_candidates if c.get("code")}
        logger.info(f"✅ M3 候选: {len(all_candidates)}")

        if not all_candidates:
            logger.warning(f"{td} 无 M3 候选")
            return {
                "early_exit": True,
                "exit_reason": "no_candidates",
                "trade_date": td,
                "market_risk_level": grade,
                "position_scale_factor": scale,
                "regime": regime,
                "market_composite_score": float(sentiment.get("market_composite_score", 0.0)),
                "ranked_all": [],
                "m3_codes": set(),
                "funnel": {"n_m3": 0, "n_m4": 0, "n_buy": 0, "stop": "no_m3", "regime": regime},
            }

        # ----- M4 + M5 -----
        force_map = await self._load_force_docs_map(
            source=source,
            trade_date=td,
            candidates=all_candidates,
            stock_flow_recent=stock_flow_recent,
            stock_ohlcv=stock_ohlcv,
        )
        coop_cfg = self.config.get("cooperative_force") or {}
        m4_threshold, gate_mode, min_sustained = self._resolve_m4_gate_settings(
            coop_cfg, regime, grade
        )
        ranked = score_and_compose_final(
            candidates=all_candidates,
            top_sectors=top_sectors,
            m5_weights=self._cfg_final_weights(),
            style_info={**style_info, "regime": factor_regime},
            active_factors=active,
            force_docs_map=force_map,
            m4_threshold=m4_threshold,
            gate_mode=gate_mode,
            min_sustained_days=min_sustained,
        )
        logger.info(f"✅ M4+M5 合力后: {len(ranked)}")

        if not ranked:
            logger.warning(f"{td} M4 合力过滤后无候选")
            return {
                "early_exit": True,
                "exit_reason": "m4_empty",
                "trade_date": td,
                "market_risk_level": grade,
                "position_scale_factor": scale,
                "regime": regime,
                "market_composite_score": float(sentiment.get("market_composite_score", 0.0)),
                "ranked_all": [],
                "m3_codes": m3_codes,
                "funnel": {
                    "n_m3": len(all_candidates),
                    "n_m4": 0,
                    "n_buy": 0,
                    "stop": "m4_empty",
                    "regime": regime,
                },
            }

        top_k = int(self._cfg_top_k(regime)) if hasattr(self, "_cfg_top_k") else len(ranked)
        return {
            "early_exit": False,
            "trade_date": td,
            "market_risk_level": grade,
            "position_scale_factor": scale,
            "regime": regime,
            "market_composite_score": float(sentiment.get("market_composite_score", 0.0)),
            "ranked_all": ranked,
            "m3_codes": m3_codes,
            "funnel": {
                "n_m3": len(all_candidates),
                "n_m4": len(ranked),
                # 估算买入名额；result_to_signals_df 会按 allow_new_open 写准确 attrs
                "n_buy": min(len(ranked), max(0, top_k)),
                "stop": "ok",
                "regime": regime,
            },
        }

    async def _load_sector_raw(
        self,
        *,
        source: str,
        trade_date: str,
        filtered_sectors: List[Dict],
        sector_stocks: Optional[Dict[str, List[str]]],
        stock_ohlcv: Optional[Dict[str, pd.DataFrame]],
        stock_flow_recent: Optional[Dict[str, List[Dict]]],
        filtered_stocks: set,
    ) -> Dict[str, Dict[str, Any]]:
        """M2 raw：预计算读文档，现场用 OHLCV 算。"""
        if source == "precomputed":
            docs = await self._load_factor_docs("get_factor_sectors", trade_date)
            if not docs:
                raise ValueError(f"无预计算 M2 数据: {trade_date}")
            allowed = {
                s.get("sector_code") for s in filtered_sectors if s.get("sector_code")
            }
            if allowed:
                docs = [d for d in docs if d.get("sector_code") in allowed]
            return SectorFactors.raw_from_docs(docs)
        return SectorFactors.calculate_all_sector_factors_raw(
            filtered_sectors,
            sector_stocks or {},
            stock_ohlcv or {},
            stock_flow_recent or {},
            trade_date=trade_date,
            eligible_codes=filtered_stocks,
        )

    async def _load_dragon_raw_by_sector(
        self,
        *,
        source: str,
        trade_date: str,
        top_sectors: List[Tuple[str, float]],
        sector_stocks: Optional[Dict[str, List[str]]],
        stock_ohlcv: Optional[Dict[str, pd.DataFrame]],
        filtered_stocks: set,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """M3 raw：{sector_code: {code: raw}}。预计算读文档，现场用 OHLCV 算。"""
        if source == "precomputed":
            codes = [sc for sc, _ in top_sectors]
            docs = await self._load_factor_docs(
                "get_factor_dragons", trade_date, sector_codes=codes
            )
            by: Dict[str, Dict[str, Dict[str, Any]]] = {}
            for d in docs or []:
                code = d.get("code")
                sc = d.get("sector_code", "")
                if not code or code not in filtered_stocks:
                    continue
                by.setdefault(sc, {})[code] = DragonFactors.raw_from_doc(d)
            return by

        jobs = []
        for sc, _ in top_sectors:
            stocks = [s for s in (sector_stocks or {}).get(sc, []) if s in filtered_stocks]
            if stocks:
                jobs.append((sc, stocks))
        fundamentals = await self._load_fundamental_asof(trade_date)
        raw_by = (
            DragonFactors.calculate_all_dragon_factors_raw(
                jobs,
                stock_ohlcv or {},
                trade_date=trade_date,
                fund_provider=fundamentals,
            )
            if jobs
            else {}
        )
        return {sc: (raw_by.get(sc) or {}) for sc, _ in jobs}

    async def _load_fundamental_asof(self, trade_date: str) -> Optional[Dict[str, Dict[str, float]]]:
        """现场 M3 直接查库，不在进程内缓存基本面。"""
        qs = self._query_service
        if qs is None or not hasattr(qs, "get_fundamental_asof"):
            return None
        try:
            data = await qs.get_fundamental_asof(trade_date)
            return data or None
        except Exception as e:
            logger.warning("基本面查询失败，f39/f40 将为空: %s", e)
            return None

    async def _load_force_docs_map(
        self,
        *,
        source: str,
        trade_date: str,
        candidates: List[Dict],
        stock_flow_recent: Optional[Dict[str, List[Dict]]],
        stock_ohlcv: Optional[Dict[str, pd.DataFrame]],
    ) -> Dict[str, Dict[str, Any]]:
        """M4 raw：{code: doc}。预计算读文档，现场用资金流/OHLCV/龙虎榜算。"""
        if source == "precomputed":
            docs = await self._load_factor_docs("get_factor_forces", trade_date)
            if not docs:
                raise ValueError(f"无预计算 M4 数据: {trade_date}")
            return {d["code"]: d for d in docs if d.get("code")}

        codes = [c.get("code") for c in candidates if c.get("code")]
        try:
            stock_lhb_recent = await self._query_service.batch_get_lhb_recent(
                codes, trade_date, days=10
            )
        except Exception as e:
            logger.warning("龙虎榜加载失败（longhu_board_bonus 将为 0）: %s", e)
            stock_lhb_recent = {}
        raw_list = ForceFactors.apply_cooperative_force_raw(
            candidates,
            stock_flow_recent=stock_flow_recent,
            stock_ohlcv=stock_ohlcv,
            stock_lhb_recent=stock_lhb_recent,
            trade_date=trade_date,
        )
        return {r["code"]: r for r in raw_list if r.get("code")}


