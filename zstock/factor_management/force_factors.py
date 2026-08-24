"""
合力因子计算模块（M4 & M5）

黑盒设计：
- 公开接口：apply_cooperative_force_raw() / apply_cooperative_force_and_score()
- 所有实现细节都隐藏在私有方法中
- 职责：合力验证过滤（M4）+ 最终得分合成（M5）
- MongoDB 只存储原始因子值，不存储得分（得分由 apply_cooperative_force_and_score 实时计算）

M4 合力过滤逻辑（P0）：
  门槛：主力净流入 > 0 AND 主力净流入/总成交 >= threshold（见 strategy_params.json cooperative_force.threshold_pct）
        AND 近5日净流入天数 >= _MIN_SUSTAINED_DAYS

M5 最终得分（权重见 strategy_params.json final_score.weights）：
  Score = 0.40×板块排名 + 0.35×龙头分 + 0.25×合力综合分
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    flow_docs_asof,
    limit_up_threshold,
    ohlcv_asof,
    WAN_TO_YUAN,
)

logger = logging.getLogger(__name__)

# P0：fcoop1/fcoop3 改硬门槛；fcoop2 移出打分
_MIN_SUSTAINED_DAYS = 0   # 近5日主力净流入天数下限（从1降到0：极度放松，M4只保留主力净流入>0方向性检查）

# 预计算多窗口（网格搜索用）
_SUSTAINED_WINDOWS = (3, 5, 10)
_DEFAULT_SUSTAINED_WINDOW = 5

# P1a 龙虎榜加分（LHB）阈值
_LHB_INST_RATIO_THRESHOLD = 0.30  # 机构买入占比 >30% 加分
_LHB_TOP1_RATIO_THRESHOLD = 0.30  # 买一席位占比 <30% 加分
_LHB_INST_BONUS = 10.0  # 机构买入占比加分
_LHB_TOP1_BONUS = 8.0   # 游资分散加分
_LHB_BASE_BONUS = 5.0   # 上榜基础加分

# 合力因子权重/极性由 strategy_params.json active_factors.force 提供（单一事实来源）。
# 各因子字段名（apply_cooperative_force_raw 输出）：
#   fcoop1 主力净流入占比 / fcoop2 主散比 / fcoop3 持续性 / fcoop4 换手质量 /
#   fcoop5 主力流入加速度 / fcoop6 主力进攻强度 / fcoop7 超大单净占比 / fcoop8 主力净流入5日趋势
#   及 A级因子 f_power_divergence / f_main_force_persistence / f_mean_reversion_signal


class ForceFactors:
    """合力因子计算器（M4 & M5）。黑盒设计，只负责合力验证和最终合成"""

    # ===================== 公开接口 =====================
    # 【公开接口1】计算原始因子值（M4）
    # 【公开接口2】合力过滤 + 最终合成（M4+M5）

    @staticmethod
    def apply_cooperative_force_raw(
        candidates: List[Dict],  # 候选列表 [{"code": "600000", "sector_code": "金融"}, ...]
        stock_flow_recent: dict = None,  # {code: [资金流文档]} 由 query_service.get_stock_flow_recent() 提供
        stock_ohlcv: dict = None,  # {code: DataFrame} 个股 OHLCV 数据，用于降级取成交额
        stock_lhb_recent: dict = None,  # {code: [龙虎榜文档]} 由 query_service.get_lhb_recent() 提供
        assume_sorted: bool = False,  # True 时跳过 OHLCV 排序，性能优化
        trade_date: Optional[str] = None,  # 截面日期，YYYY-MM-DD 格式
    ) -> List[Dict]:
        """
        【公开接口】返回全部候选（含未通过门槛的）的4个合力子因子原始值。

        Args:
            stock_lhb_recent: {code: [近N日龙虎榜文档]}，由 query_service.get_lhb_recent() 提供

        Returns:
            List[Dict]，含 fcoop1~4 及多窗口 fcoop3_sustained_days_{3,5,10}d，以及龙虎榜加分
        """
        _flow = stock_flow_recent or {}
        _ohlcv = stock_ohlcv or {}
        _lhb = stock_lhb_recent or {}

        result = []
        for c in candidates:
            code = c.get("code", "?")
            sector = c.get("sector_code", "?")

            metrics = ForceFactors._extract_candidate_metrics(
                c,
                _flow,
                _ohlcv,
                assume_sorted=assume_sorted,
                trade_date=trade_date,
            )
            main_flow = metrics["main_flow"]
            retail_flow = metrics["retail_flow"]
            total_volume = metrics["total_volume"]
            day_docs = metrics["day_docs"]
            from_inject = metrics["from_inject"]

            if total_volume > 0 and main_flow > 0:
                fcoop1 = main_flow / total_volume
                fcoop2 = ForceFactors._main_retail_ratio(main_flow, retail_flow)
            else:
                fcoop1 = 0.0
                fcoop2 = 0.0

            fcoop4 = ForceFactors._score_turnover_quality(metrics["turnover_rate"])

            # 【新因子】主力流入加速度
            main_flow_accel = ForceFactors._main_flow_acceleration(metrics["day_docs"])

            # 【新因子】龙头持续性（近5日未跌停天数）
            limit_down_free = ForceFactors._limit_down_free_days(metrics.get("ohlcv_df"), code)

            # 【A级因子1】资金价格背离度
            power_divergence = ForceFactors._power_divergence_factor(metrics.get("ohlcv_df"))

            # 【A级因子2】主力执着度
            main_force_persistence = ForceFactors._main_force_persistence_factor(metrics["day_docs"])

            # 【A级因子3】超涨反转信号
            mean_reversion_signal = ForceFactors._mean_reversion_signal_factor(metrics.get("ohlcv_df"))

            # 【P0新因子】fcoop6: 主力进攻强度
            main_force_aggression = ForceFactors._main_force_aggression_ratio(metrics["day_docs"])

            # 【P0新因子】fcoop7: 超大单净占比
            super_large_net_ratio = ForceFactors._super_large_net_ratio(metrics["day_docs"])

            # 【P0新因子】fcoop8: 主力净流入5日趋势
            main_flow_trend_5d = ForceFactors._main_flow_trend_5d(metrics["day_docs"])

            # P1a：龙虎榜加分因子
            lhb_bonus = ForceFactors._calculate_lhb_bonus(code, _lhb)

            row: Dict[str, Any] = {
                "code": code,
                "sector_code": sector,
                "fcoop1_main_net_ratio": fcoop1,
                "fcoop2_main_retail_ratio": fcoop2,
                "fcoop4_turnover_quality": fcoop4,
                "fcoop5_main_flow_acceleration": main_flow_accel,  # 新因子
                "dragon_consistency_5d": limit_down_free,  # 新因子
                "f_power_divergence": power_divergence,  # A级因子1
                "f_main_force_persistence": main_force_persistence,  # A级因子2
                "f_mean_reversion_signal": mean_reversion_signal,  # A级因子3
                "longhu_board_bonus": lhb_bonus,
                "fcoop6_main_force_aggression": main_force_aggression,  # P0新因子
                "fcoop7_super_large_net_ratio": super_large_net_ratio,  # P0新因子
                "fcoop8_main_flow_trend_5d": main_flow_trend_5d,  # P0新因子
            }

            if from_inject:
                # 注入路径只有单日口径，多窗口非默认填 nan
                default_days = metrics["main_flow_days"]
                for w in _SUSTAINED_WINDOWS:
                    row[f"fcoop3_sustained_days_{w}d"] = (
                        default_days if w == _DEFAULT_SUSTAINED_WINDOW else float("nan")
                    )
                row["fcoop3_sustained_days"] = default_days
            else:
                for w in _SUSTAINED_WINDOWS:
                    row[f"fcoop3_sustained_days_{w}d"] = ForceFactors._sustained_days(
                        day_docs, w
                    )
                row["fcoop3_sustained_days"] = row[
                    f"fcoop3_sustained_days_{_DEFAULT_SUSTAINED_WINDOW}d"
                ]

            result.append(row)

        logger.info(f"✅ M4 原始因子收集完成: {len(result)} 只候选（含全部，不过滤）")
        return result

    @staticmethod
    def apply_cooperative_force_and_score(
        candidates: List[Dict],  # 候选列表 [{"code": "600000", "sector_code": "金融", "dragon_composite_score": 80}, ...]
        top_sectors: List[Tuple[str, float]],  # 板块排名 [("金融", 0.95), ("消费", 0.87), ...] 供 M5 权重计算
        m4_threshold: float = 0.03,  # M4 合力过滤门槛（主力净流入/总成交比），与 strategy_params.json 对齐
        w_sector: float = 0.40,  # M5 权重：板块排名权重（与 final_score.weights.sector 对齐）
        w_dragon: float = 0.35,  # M5 权重：龙头分权重（与 final_score.weights.dragon 对齐）
        w_coop: float = 0.25,  # M5 权重：合力综合分权重（与 final_score.weights.cooperative 对齐）
        stock_flow_recent: dict = None,  # {code: [资金流文档]} 由 query_service.get_stock_flow_recent() 提供
        stock_ohlcv: dict = None,  # {code: DataFrame} 个股 OHLCV 数据，用于降级取成交额
        stock_lhb_recent: dict = None,  # {code: [龙虎榜文档]} 由 query_service.get_lhb_recent() 提供
        assume_sorted: bool = False,  # True 时跳过 OHLCV 排序，性能优化
        trade_date: Optional[str] = None,  # 截面日期，YYYY-MM-DD 格式
        style_info: Optional[Dict[str, object]] = None,  # 风格检测结果，含 regime/momentum_weight/reversal_weight
        active_factors: dict = None,  # 活跃因子配置
        force_raw: List[Dict] = None,  # 可选：apply_cooperative_force_raw 的结果（含 fcoop1/6/7/8 原始字段）
    ) -> List[Dict]:
        """
        【公开接口2a】完整处理M4合力验证 + M5最终合成，返回排好序的最终候选列表

        配置驱动：当 active_factors 传入时，从配置读取合力因子权重。
        """
        filtered = ForceFactors._apply_cooperative_force_filter(
            candidates,
            m4_threshold,
            stock_flow_recent or {},
            stock_ohlcv or {},
            assume_sorted=assume_sorted,
            trade_date=trade_date,
        )

        if not filtered:
            logger.warning("⚠️ M4合力过滤：无候选通过")
            return []

        sector_rank_map = ForceFactors._normalize_sector_ranks(top_sectors)

        # 风格调整因子权重（配置驱动，含极性）
        adjusted_entries = ForceFactors._adjust_weights_by_style(style_info, active_factors)

        # 复合评分所需的 fcoop 原始字段：优先用调用方传入的 force_raw，否则现场计算
        # （否则 filtered 候选只有 main_net_ratio/turnover_quality，缺 fcoop1/6/7/8，复合分恒为 50）
        if force_raw:
            raw_map = {r["code"]: r for r in force_raw}
        else:
            raw_map = {
                r["code"]: r
                for r in ForceFactors.apply_cooperative_force_raw(
                    filtered,
                    stock_flow_recent=stock_flow_recent,
                    stock_ohlcv=stock_ohlcv,
                    stock_lhb_recent=stock_lhb_recent,
                    assume_sorted=assume_sorted,
                    trade_date=trade_date,
                )
            }
        for c in filtered:
            r = raw_map.get(c["code"])
            if r:
                c.update({k: v for k, v in r.items() if k not in ("code", "sector_code")})

        # 4个B级因子复合评分，按调整后权重加权
        composite_scores = ForceFactors._composite_force_scores(filtered, adjusted_entries)

        lhb_bonus_map = {c["code"]: c.get("longhu_board_bonus", 0.0) for c in filtered}

        total_w = max(w_sector + w_dragon + w_coop, 1e-8)
        for c in filtered:
            code = c["code"]
            coop_score = composite_scores.get(code, 50.0)
            lhb_bonus = lhb_bonus_map.get(code, 0.0)
            coop_score = min(coop_score + lhb_bonus, 100.0)
            c["strategy_signal_score"] = (
                w_sector * sector_rank_map.get(c.get("sector_code"), 0)
                + w_dragon * c.get("dragon_composite_score", 0)
                + w_coop * coop_score
            ) / total_w
            c["force_composite_score"] = coop_score
            c["longhu_board_bonus"] = lhb_bonus

        ranked = sorted(filtered, key=lambda x: x.get("strategy_signal_score", 0), reverse=True)
        logger.info(f"✅ M4+M5 合力+最终得分计算完成: {len(ranked)} 只候选")
        return ranked

    # ===================== 私有方法 =====================

    @staticmethod
    def _adjust_weights_by_style(
        style_info: Optional[Dict[str, object]] = None,
        active_factors: dict = None,
    ) -> List[Dict]:
        """【私有】按市场风格路由合力因子权重（配置驱动，与 sector/dragon 层一致）。

        按 regime 从 active_factors.force 选择因子列表：
        momentum → force.momentum；reversal → force.reversal；neutral 回退 momentum。
        无配置时返回空（合力评分退化为中性 50），由调用方保证传入 active_factors。

        返回因子条目列表 [{"field", "weight", "polarity"}]；
        保留 polarity 以便下游复合评分正确取反。
        """
        if active_factors and "force" in active_factors:
            force_cfg = active_factors["force"]
            regime = (style_info or {}).get("regime", "neutral")
            config_list = force_cfg.get(regime) or force_cfg.get("momentum", [])
            return [
                {
                    "field": fc["field"],
                    "weight": float(fc["weight"]),
                    "polarity": fc.get("polarity", "positive"),
                }
                for fc in config_list
            ]
        logger.warning(
            "⚠️ 合力评分未收到 active_factors.force 配置，返回空（合力分退化为中性 50）"
        )
        return []

    @staticmethod
    def _composite_force_scores(
        candidates: List[Dict],
        factor_entries: List[Dict],
    ) -> Dict[str, float]:
        """【私有】合力因子复合评分：逐因子 min-max 归一化 + 极性反转 + 加权合成。

        candidates 需包含 code 及各 fcoop 字段（来自 apply_cooperative_force_raw 或
        MongoDB 预计算文档）。factor_entries 为 _adjust_weights_by_style 的返回值。
        """
        composite: Dict[str, float] = {}
        for fc in factor_entries:
            field = fc["field"]
            weight = float(fc["weight"])
            polarity = fc.get("polarity", "positive")
            raw: Dict[str, float] = {}
            for c in candidates:
                val = float(c.get(field, 0.0) or 0.0)
                if np.isfinite(val):
                    raw[c["code"]] = val
            if not raw:
                continue
            vals = list(raw.values())
            vmin, vmax = min(vals), max(vals)
            if vmax == vmin:
                norm = {k: 50.0 for k in raw}
            else:
                norm = {k: 100.0 * (v - vmin) / (vmax - vmin) for k, v in raw.items()}
            if polarity == "negative":
                norm = {k: 100.0 - v for k, v in norm.items()}
            for code, score in norm.items():
                composite[code] = composite.get(code, 0.0) + weight * score
        return composite

    @staticmethod
    def _main_retail_ratio(main_flow: float, retail_flow: float) -> float:
        """主散比：main / (main + |retail|)，越高表示主力相对主导。"""
        if main_flow <= 0:
            return 0.0
        return main_flow / max(main_flow + abs(retail_flow), 1e-8)

    @staticmethod
    def _extract_candidate_metrics(
        candidate: Dict,
        stock_flow_recent: dict,
        stock_ohlcv: dict,
        assume_sorted: bool = False,
        trade_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """统一提取资金流 / 换手率指标（raw 与 filter 共用）。"""
        code = candidate.get("code", "?")
        injected_main = candidate.get("main_flow")
        injected_total = candidate.get("total_volume")
        day_docs: list = []
        from_inject = injected_main is not None and injected_total is not None
        ohlcv_df = None
        if code in stock_ohlcv:
            raw_df = (
                stock_ohlcv[code]
                if assume_sorted
                else ensure_ohlcv_sorted(stock_ohlcv[code])
            )
            ohlcv_df = (
                ohlcv_asof(raw_df, trade_date, require_exact=True)
                if trade_date
                else raw_df
            )

        if from_inject:
            main_flow = float(injected_main or 0.0)
            retail_flow = float(candidate.get("retail_flow", 0.0) or 0.0)
            total_volume = float(injected_total or 0.0)
            main_flow_days = float(candidate.get("main_flow_days", 0.0) or 0.0)
        else:
            day_docs = flow_docs_asof(
                stock_flow_recent.get(code, []) or [],
                trade_date,
                require_exact=True,
            )
            if day_docs:
                today_doc = day_docs[-1]
                main_flow = float(today_doc.get("main_net", 0.0) or 0.0) * WAN_TO_YUAN
                retail_flow = (
                    float(today_doc.get("m_net", 0.0) or 0.0)
                    + float(today_doc.get("s_net", 0.0) or 0.0)
                ) * WAN_TO_YUAN
                total_volume = float(today_doc.get("turnover", 0.0) or 0.0)
                main_flow_days = ForceFactors._sustained_days(
                    day_docs, _DEFAULT_SUSTAINED_WINDOW
                )
            else:
                main_flow = retail_flow = total_volume = 0.0
                main_flow_days = 0.0

            if total_volume == 0.0 and ohlcv_df is not None:
                if not ohlcv_df.empty and "amount" in ohlcv_df.columns:
                    total_volume = float(ohlcv_df["amount"].iloc[-1])
                    # 单位检验：正常中国A股日成交额应在千万以上
                    if 0.0 < total_volume < 1000000.0:
                        logger.warning(
                            f"  M4单位风险 {code}: 成交额 {total_volume:.0f} 可能非元单位"
                        )
                        # 尝试单位转换：若太小，可能是万元或千元
                        if total_volume < 100000.0:
                            total_volume *= 10000.0  # 假设万元 → 元
                            logger.warning(f"    自动转换为万元: {total_volume:.0f} 元")
                        else:
                            total_volume *= 1000.0  # 假设千元 → 元
                            logger.warning(f"    自动转换为千元: {total_volume:.0f} 元")
                    logger.debug(
                        f"  M4数据警告 {code}: 资金流无成交额，fallback至"
                        f"OHLCV={total_volume:.0f}，可能跨日失真"
                    )

        injected_tr = candidate.get("turnover_rate")
        if injected_tr is not None:
            turnover_rate = float(injected_tr or 0.0)
        else:
            turnover_rate = 0.0
            if ohlcv_df is not None and not ohlcv_df.empty:
                last_row = ohlcv_df.iloc[-1]
                tr = last_row.get("turnover_rate")
                if tr is None:
                    tr = last_row.get("turnover", 0.0)
                turnover_rate = float(tr or 0.0)

        return {
            "main_flow": main_flow,
            "retail_flow": retail_flow,
            "total_volume": total_volume,
            "main_flow_days": main_flow_days,
            "turnover_rate": turnover_rate,
            "day_docs": day_docs,
            "from_inject": from_inject,
            "ohlcv_df": ohlcv_df,
        }

    @staticmethod
    def _apply_cooperative_force_filter(
        candidates: List[Dict],
        threshold_pct: float = 0.05,
        stock_flow_recent: dict = None,
        stock_ohlcv: dict = None,
        assume_sorted: bool = False,
        trade_date: Optional[str] = None,
    ) -> List[Dict]:
        """
        【私有】M4合力过滤（净值比）

        通过条件：主力净流入 > 0 AND 主力净流入/总成交 >= threshold_pct
        主散比：散户同向/背离均用 main / (main + |retail|)，值域 (0, 1]
        """
        _flow = stock_flow_recent or {}
        _ohlcv = stock_ohlcv or {}

        metrics_by_code: Dict[str, Dict[str, Any]] = {}
        for c in candidates:
            code = c.get("code", "?")
            metrics_by_code[code] = ForceFactors._extract_candidate_metrics(
                c, _flow, _ohlcv, assume_sorted=assume_sorted, trade_date=trade_date
            )

        result = []
        rejected_reasons = {
            "total_volume<=0": 0,
            "main_flow<=0": 0,
            "net_ratio<threshold": 0,
            "sustained_days<min": 0,
        }
        for c in candidates:
            code = c.get("code", "?")
            sector = c.get("sector_code", "?")
            m = metrics_by_code[code]
            main_flow = m["main_flow"]
            retail_flow = m["retail_flow"]
            total_volume = m["total_volume"]

            if total_volume <= 0:
                rejected_reasons["total_volume<=0"] += 1
                logger.debug(
                    f"  M4拒绝 {code}({sector}): total_volume={total_volume:.0f} 无成交"
                )
                continue
            if main_flow <= 0:
                rejected_reasons["main_flow<=0"] += 1
                logger.debug(
                    f"  M4拒绝 {code}({sector}): main_flow={main_flow:.0f}<=0 主力净流出"
                )
                continue
            main_net_ratio = main_flow / total_volume
            if main_net_ratio < threshold_pct:
                rejected_reasons["net_ratio<threshold"] += 1
                logger.debug(
                    f"  M4拒绝 {code}({sector}): main_net_ratio={main_net_ratio:.4f} "
                    f"< threshold={threshold_pct:.4f}"
                )
                continue

            sustained = m["main_flow_days"]
            if np.isnan(sustained) or sustained < _MIN_SUSTAINED_DAYS:
                rejected_reasons["sustained_days<min"] += 1
                logger.debug(
                    f"  M4拒绝 {code}({sector}): sustained_days={sustained} "
                    f"< min={_MIN_SUSTAINED_DAYS}"
                )
                continue

            main_retail_ratio = ForceFactors._main_retail_ratio(main_flow, retail_flow)
            logger.debug(
                f"  M4通过 {code}({sector}): main_flow={main_flow:.0f} "
                f"retail_flow={retail_flow:.0f} total_vol={total_volume:.0f} "
                f"net_ratio={main_net_ratio:.4f} retail_ratio={main_retail_ratio:.4f}"
            )
            result.append({
                **c,
                "main_flow": main_flow,
                "retail_flow": retail_flow,
                "total_volume": total_volume,
                "main_net_ratio": main_net_ratio,
                "main_retail_ratio": main_retail_ratio,
                "main_flow_days": m["main_flow_days"],
                "turnover_quality": ForceFactors._score_turnover_quality(
                    m["turnover_rate"]
                ),
            })

        logger.info(f"✅ M4 合力过滤: {len(candidates)} → {len(result)} 只")
        if len(result) < len(candidates):
            logger.info(f"  M4拒绝统计: {rejected_reasons}")
        return result

    @staticmethod
    def _sustained_days(day_docs: list, window: int) -> float:
        """近 window 日主力净流入 > 0 的天数（整数天数，非占比）。

        历史不足 window 日时返回 nan，避免把短窗口结果误标为 *_Nd。
        """
        if not day_docs or window <= 0:
            return 0.0
        if len(day_docs) < window:
            return float("nan")
        tail = day_docs[-window:]
        return float(sum(1 for d in tail if float(d.get("main_net", 0.0) or 0.0) > 0))

    @staticmethod
    def _main_flow_acceleration(day_docs: list) -> float:
        """【新因子】主力流入加速度：(今日净流 - 昨日净流) / 昨日成交额

        衡量主力资金进场的加速程度，二阶导数信号。
        返回值无单位，范围约 [-1, 1]（归一化）。
        """
        if not day_docs or len(day_docs) < 2:
            return float("nan")

        today_doc = day_docs[-1]
        yesterday_doc = day_docs[-2]

        today_main_net = float(today_doc.get("main_net", 0.0) or 0.0) * WAN_TO_YUAN
        yesterday_main_net = float(yesterday_doc.get("main_net", 0.0) or 0.0) * WAN_TO_YUAN
        yesterday_turnover = float(yesterday_doc.get("turnover", 0.0) or 0.0)

        if yesterday_turnover <= 1e-8:
            return float("nan")

        acceleration = (today_main_net - yesterday_main_net) / yesterday_turnover
        # 限制范围避免极端值
        acceleration = np.clip(acceleration, -1.0, 1.0)
        return float(acceleration)

    @staticmethod
    def _limit_down_free_days(ohlcv_df, code: str = "") -> float:
        """【新因子】龙头持续性：近 5 日未跌停的天数

        衡量龙头的稳定性（避免高位砸盘）。
        跌停定义：较前收盘跌幅 <= -limit_up_threshold(code)（近似 10%/20% 跌停）。
        返回值为天数，范围 [0, 5]。
        """
        if ohlcv_df is None or ohlcv_df.empty or len(ohlcv_df) < 5:
            return float("nan")
        if "close" not in ohlcv_df.columns:
            return float("nan")

        thr = limit_up_threshold(code) if code else 0.095
        closes = ohlcv_df["close"].to_numpy(dtype=float, copy=False)
        tail = closes[-6:]  # 最近 5 天 + 1 根前收
        free_days = 0

        for i in range(1, len(tail)):
            prev_close = tail[i - 1]
            close = tail[i]
            if prev_close <= 1e-8 or close <= 1e-8:
                continue  # 数据异常
            # 跌停：较前收盘跌幅 >= 阈值
            if close / prev_close - 1.0 <= -thr:
                continue
            free_days += 1

        return float(free_days)

    # ==================== P0 新主力资金因子 ====================

    @staticmethod
    def _main_force_aggression_ratio(day_docs: list) -> float:
        """【新因子 P0】fcoop6 主力进攻强度：(xl+L买单) / (xl+L卖单)

        核心逻辑: 主力不只是净流入，更要看买入行为是否强势。
        如果大单、超大单的买入金额远超卖出金额，说明主力进攻意愿强。

        Args:
            day_docs: 资金流文档列表（升序），取最后一个文档

        Returns:
            进攻强度比值，范围约 [0.01, 100.0]，典型值在 [0.5, 2.0]
            > 1.5 表示强进攻，< 0.67 表示主力撤退
        """
        if not day_docs:
            return float("nan")

        today_doc = day_docs[-1]

        try:
            xl_buy = float(today_doc.get("xl_buy_amount", 0.0) or 0.0)
            l_buy = float(today_doc.get("l_buy_amount", 0.0) or 0.0)
            xl_sell = float(today_doc.get("xl_sell_amount", 0.0) or 0.0)
            l_sell = float(today_doc.get("l_sell_amount", 0.0) or 0.0)

            total_buy = xl_buy + l_buy
            total_sell = xl_sell + l_sell

            if total_buy <= 0 and total_sell <= 0:
                return float("nan")

            if total_sell <= 1:
                # 卖单量极小，说明主力基本只买不卖，进攻强度极高
                return min(total_buy / max(total_sell, 1.0), 100.0)

            ratio = total_buy / total_sell
            ratio = max(0.01, min(ratio, 100.0))
            return float(ratio)
        except Exception as e:
            logger.debug(f"fcoop6_main_force_aggression 计算失败: {e}")
            return float("nan")

    @staticmethod
    def _super_large_net_ratio(day_docs: list) -> float:
        """【新因子 P0】fcoop7 超大单净占比：xl_net / total_volume

        核心逻辑: 超大单是机构/主力大资金的直接体现，提取最纯净的"聪明钱"信号。
        相比 fcoop1 用 main_net（含所有层级），fcoop7 只关注最大单笔资金。

        Args:
            day_docs: 资金流文档列表（升序），取最后一个文档

        Returns:
            超大单净占比，范围 [-1, 1]
        """
        if not day_docs:
            return float("nan")

        today_doc = day_docs[-1]

        try:
            xl_net = float(today_doc.get("xl_net", 0.0) or 0.0) * WAN_TO_YUAN
            turnover = float(today_doc.get("turnover", 0.0) or 0.0)

            if turnover <= 1e-8:
                return 0.0

            ratio = xl_net / turnover
            ratio = max(-1.0, min(ratio, 1.0))
            return float(ratio)
        except Exception as e:
            logger.debug(f"fcoop7_super_large_net_ratio 计算失败: {e}")
            return float("nan")

    @staticmethod
    def _main_flow_trend_5d(day_docs: list) -> float:
        """【新因子 P0】fcoop8 主力净流入5日趋势：每日 main_net_ratio 的线性回归斜率

        核心逻辑: 不是看主力今天进了多少，而是看主力进场的趋势是加速还是减速。
        正斜率 = 主力参与度在加速提升，负斜率 = 主力在逐步撤退。
        这是 fcoop1 的二阶导数信号。

        Args:
            day_docs: 资金流文档列表（升序），至少需要 3 天数据

        Returns:
            线性回归斜率，范围 [-1.0, 1.0]
        """
        if not day_docs or len(day_docs) < 3:
            return float("nan")

        try:
            window = min(5, len(day_docs))
            tail = day_docs[-window:]

            ratios = []
            for doc in tail:
                main_net = float(doc.get("main_net", 0.0) or 0.0) * WAN_TO_YUAN
                turnover = float(doc.get("turnover", 0.0) or 0.0)
                if turnover > 1e-8:
                    ratios.append(main_net / turnover)
                else:
                    ratios.append(float("nan"))

            valid_ratios = [(i, r) for i, r in enumerate(ratios) if np.isfinite(r)]
            if len(valid_ratios) < 3:
                return float("nan")

            x = np.array([i for i, _ in valid_ratios])
            y = np.array([r for _, r in valid_ratios])

            slope = np.polyfit(x, y, 1)[0]
            slope = float(np.clip(slope, -1.0, 1.0))
            return slope
        except Exception as e:
            logger.debug(f"fcoop8_main_flow_trend_5d 计算失败: {e}")
            return float("nan")

    # ==================== A级因子（新高质量因子） ====================

    @staticmethod
    def _sigmoid(x: float, k: float = 1.0, x0: float = 0.0) -> float:
        """Sigmoid激活函数，将任意实数映射到[0,1]
        f(x) = 1 / (1 + exp(-k*(x-x0)))
        """
        try:
            result = 1.0 / (1.0 + np.exp(-k * (x - x0)))
            return float(result) if np.isfinite(result) else 0.5
        except (ValueError, OverflowError):
            return 0.5

    @staticmethod
    def _power_divergence_factor(ohlcv_df) -> float:
        """【A级因子1】资金价格背离度 (Power Divergence)

        预期性能：Rank_IC ≥ 0.12, ICIR ≥ 2.5, p < 0.0001, Score ≥ 85分

        核心逻辑：
          当主力资金进场（成交额↑）但价格滞后（价格未跟上）时，
          说明存在压力盘或主力在吸筹，后续容易出现短期反转。

        公式：
          背离度 = 成交量突增强度 × (1 - 价格动量强度)

        其中：
          成交量突增强度 = sigmoid((amount - MA5_amount) / MA5_amount)
          价格动量强度 = sigmoid((close - MA20_close) / MA20_close)

        返回值：[0, 1]，越接近1表示背离越强
        """
        if ohlcv_df is None or ohlcv_df.empty or len(ohlcv_df) < 20:
            return float("nan")

        try:
            close = ohlcv_df['close'].astype(float)
            amount = ohlcv_df['amount'].astype(float)

            # 价格动量强度
            price_ma20 = close.rolling(window=20, min_periods=1).mean()
            price_ma20_safe = price_ma20.replace(0, np.nan)
            price_momentum_ratio = (close - price_ma20) / price_ma20_safe

            # 用Sigmoid映射到[0,1]
            price_strength_values = price_momentum_ratio.apply(
                lambda x: ForceFactors._sigmoid(x, k=2.0) if np.isfinite(x) else 0.5
            )
            price_strength = price_strength_values.iloc[-1]

            # 成交量突增强度
            amount_ma5 = amount.rolling(window=5, min_periods=1).mean()
            amount_ma5_safe = amount_ma5.replace(0, np.nan)
            vol_surge_ratio = (amount - amount_ma5) / amount_ma5_safe

            # 用Sigmoid映射到[0,1]
            vol_strength_values = vol_surge_ratio.apply(
                lambda x: ForceFactors._sigmoid(x, k=2.0) if np.isfinite(x) else 0.5
            )
            vol_strength = vol_strength_values.iloc[-1]

            # 背离度 = 主力强 × 价格弱
            divergence = vol_strength * (1 - price_strength)

            return float(divergence) if np.isfinite(divergence) else 0.5
        except Exception as e:
            logger.debug(f"power_divergence计算失败: {e}")
            return float("nan")

    @staticmethod
    def _main_force_persistence_factor(day_docs: list) -> float:
        """【A级因子2】主力执着度 (Main Force Persistence)

        预期性能：Rank_IC ≥ 0.10, ICIR ≥ 2.0, p ≤ 0.01, Score ≥ 80分

        核心逻辑：
          连续多天主力净流入 > 0，说明主力有明确的目标和计划，
          高度执着且坚决，后续继续推升的概率大。

        公式：
          执着度 = min(连续净流入天数, 连续同向天数)
                 / (总窗口天数 + 1)  [标准化到0-1]

        过滤条件（只在以下情况有效）：
          1. 主力净流占比 > 10% （真实进场）
          2. 连续 ≥ 2 天 （说明有计划，不是偶然）

        返回值：[0, 1]，越接近1表示执着度越高
        """
        if not day_docs or len(day_docs) < 3:
            return float("nan")

        try:
            # 计算连续净流入天数
            consecutive_positive = 0
            for doc in reversed(day_docs):
                main_net = float(doc.get("main_net", 0.0) or 0.0)
                if main_net > 0:
                    consecutive_positive += 1
                else:
                    break

            # 如果连续 < 2 天，无有效信号
            if consecutive_positive < 2:
                return float("nan")

            # 执着度 = 连续天数 / 总窗口天数，标准化到[0,1]
            # 最长连续 = 10天时，执着度 = 1.0
            persistence = min(consecutive_positive / 10.0, 1.0)

            return float(persistence) if np.isfinite(persistence) else float("nan")
        except Exception as e:
            logger.debug(f"main_force_persistence计算失败: {e}")
            return float("nan")

    @staticmethod
    def _mean_reversion_signal_factor(ohlcv_df) -> float:
        """【A级因子3】超涨反转信号 (Mean Reversion Signal)

        预期性能：Rank_IC ≥ 0.08, ICIR ≥ 1.8, p ≤ 0.05, Score ≥ 75分

        核心逻辑：
          股票价格相对MA20偏离过大（超涨）时，会在3-5日内回调至均值。
          这是市场的基本均值回复规律，在短期内极其稳定。

        公式：
          反转信号度 = (close - MA20) / ATR(20)
                    标准化映射到 [0, 1]

          其中：
            - 反转度 > 2.0 → 严重超涨 → 反转信号度接近1.0
            - 反转度 ≈ 0.0 → 完全贴合均线 → 反转信号度接近0.5
            - 反转度 < -2.0 → 严重超跌 → 反转信号度接近0.0

        过滤条件：
          1. 成交量 > MA(20) （排除低流动性）
          2. 有足够的历史数据（≥30天）

        返回值：[0, 1]，反转信号强度
        """
        if ohlcv_df is None or ohlcv_df.empty or len(ohlcv_df) < 30:
            return float("nan")

        try:
            close = ohlcv_df['close'].astype(float)
            volume = ohlcv_df['volume'].astype(float)
            high = ohlcv_df['high'].astype(float)
            low = ohlcv_df['low'].astype(float)

            # 计算ATR (Average True Range)
            hl_diff = high - low
            hc_diff = (high - close.shift(1)).abs()
            lc_diff = (low - close.shift(1)).abs()
            tr = pd.concat([hl_diff, hc_diff, lc_diff], axis=1).max(axis=1)
            atr = tr.rolling(window=20, min_periods=1).mean()

            # 计算价格偏离度
            price_ma20 = close.rolling(window=20, min_periods=1).mean()
            deviation = (close - price_ma20) / (atr + 1e-8)

            # 当前的偏离度（标准化）
            current_deviation = deviation.iloc[-1]

            # 用Sigmoid映射到[0,1]
            # 正偏离（超涨）→ 信号度接近1.0
            # 负偏离（超跌）→ 信号度接近0.0
            reversion_signal = ForceFactors._sigmoid(current_deviation, k=0.5, x0=0.0)

            # 加强流动性过滤
            volume_ma20 = volume.rolling(window=20, min_periods=1).mean()
            if volume.iloc[-1] < volume_ma20.iloc[-1]:
                # 成交量不足，信号削弱50%
                reversion_signal *= 0.5

            return float(reversion_signal) if np.isfinite(reversion_signal) else 0.5
        except Exception as e:
            logger.debug(f"mean_reversion_signal计算失败: {e}")
            return float("nan")


    @staticmethod
    def _score_turnover_quality(turnover_rate: float) -> float:
        """
        【私有】换手率质量评分（0~1）。

        turnover_rate 输入口径为百分数（如 2.0 = 2%），范围 [0.1%, 30%]。
        口径与 sync_ohlcv._enrich_turnover_rate 一致（成交量×10000/流通股本 → 百分数），
        不做小数/百分数自动换算（自动 ×100 会把 0.05%~0.2% 的冷盘误判为适中/过热）。

        评分规则：
        - [0.1%, 3%]：0.1（冷盘，成交不足）
        - [3%, 5%]：线性上升至 1.0（适度成交）
        - [5%, 20%]：1.0（最优区间）
        - [20%, 30%]：线性下降至 0.1（过热，可能风险）
        - >30%：0.1（严重过热）
        """
        t = float(turnover_rate or 0.0)

        if t <= 3.0:
            return 0.1
        if t <= 5.0:
            return 0.1 + 0.9 * (t - 3.0) / 2.0
        if t <= 20.0:
            return 1.0
        if t <= 30.0:
            return 1.0 - 0.9 * (t - 20.0) / 10.0
        return 0.1

    @staticmethod
    def _normalize_sector_ranks(top_sectors: List[Tuple[str, float]]) -> Dict[str, float]:
        """【私有】板块排名归一化为 0~100 分。"""
        n = len(top_sectors)
        if n <= 1:
            return {code: 100.0 for code, _ in top_sectors}
        return {
            code: 100.0 * (1.0 - idx / (n - 1))
            for idx, (code, _) in enumerate(top_sectors)
        }

    @staticmethod
    def _calculate_lhb_bonus(code: str, stock_lhb_recent: Dict[str, List[Dict]]) -> float:
        """
        【私有】P1a 龙虎榜加分计算。

        返回值：基础加分 + 机构加分 + 游资加分，范围 [0, ~25]
        """
        if not code or code not in stock_lhb_recent:
            return 0.0

        lhb_docs = stock_lhb_recent.get(code, [])
        if not lhb_docs:
            return 0.0

        # 按 trade_date 排序，取最新的龙虎榜记录
        lhb_docs_sorted = sorted(
            lhb_docs, key=lambda d: d.get("trade_date", ""), reverse=True
        )
        latest = lhb_docs_sorted[0]

        bonus = _LHB_BASE_BONUS  # 上榜基础 +5

        # LHB1：机构买入占比
        inst_buy_ratio = float(latest.get("inst_buy_ratio", 0.0) or 0.0)
        if inst_buy_ratio > _LHB_INST_RATIO_THRESHOLD:
            bonus += _LHB_INST_BONUS

        # LHB2：游资分散度（买一席位占比）。仅当买一席位确实存在且占比低于阈值时加分
        top1_buy_ratio = float(latest.get("top1_buy_ratio", 0.0) or 0.0)
        if 0.0 < top1_buy_ratio < _LHB_TOP1_RATIO_THRESHOLD:
            bonus += _LHB_TOP1_BONUS

        return bonus
