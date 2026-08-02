"""
合力因子计算模块（M4 & M5）

黑盒设计：
- 公开接口：apply_cooperative_force_and_score() / apply_cooperative_force_raw()
- 所有实现细节都隐藏在私有方法中
- 职责：合力验证过滤（M4）+ 最终得分合成（M5）

M4 合力过滤逻辑（净值比）：
  门槛：主力净流入 > 0 AND 主力净流入/总成交 >= threshold（默认3%）
  评分：4个子因子加权合成合力综合分
    F_coop1  主力净值比（主力净流入/总成交）        权重 0.35 —— 合力核心
    F_coop2  主散比 main/(main+|retail|)            权重 0.25 —— 机构主导程度
    F_coop3  净流入持续天数（近N日 main_net>0 天数）权重 0.25 —— 持续性
    F_coop4  换手质量（换手率是否在合理区间）       权重 0.15 —— 活跃但不过热

M5 最终得分：
  Score = 0.4×板块排名 + 0.35×龙头分 + 0.25×合力综合分
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    flow_docs_asof,
    ohlcv_asof,
)

logger = logging.getLogger(__name__)

# 合力综合分子因子权重
_W_COOP1 = 0.35   # F_coop1：主力净值比（核心）
_W_COOP2 = 0.25   # F_coop2：主散比（机构主导）
_W_COOP3 = 0.25   # F_coop3：净流入持续天数
_W_COOP4 = 0.15   # F_coop4：换手质量（打分取反：反过热）

# 测评 RankIC 显著为负：打分侧对 fcoop4 取反后再 min-max
_INVERT_COOP4_FOR_SCORE = True

# 预计算多窗口（网格搜索用）
_SUSTAINED_WINDOWS = (3, 5, 10)
_DEFAULT_SUSTAINED_WINDOW = 5

# 资金流单位换算：xtquant L2 净额字段(main_net/m_net/s_net)单位为"万元"，
# 成交额字段(turnover)单位为"元"。统一换算到元，保证净值比量纲一致。
_WAN_TO_YUAN = 10000.0


class ForceFactors:
    """合力因子计算器（M4 & M5）。黑盒设计，只负责合力验证和最终合成"""

    # ===================== 公开接口 =====================

    @staticmethod
    def apply_cooperative_force_raw(
        candidates: List[Dict],
        stock_flow_recent: dict = None,
        stock_ohlcv: dict = None,
        assume_sorted: bool = False,
        trade_date: Optional[str] = None,
    ) -> List[Dict]:
        """
        【公开接口】返回全部候选（含未通过门槛的）的4个合力子因子原始值。

        Returns:
            List[Dict]，含 fcoop1~4 及多窗口 fcoop3_sustained_days_{3,5,10}d
        """
        _flow = stock_flow_recent or {}
        _ohlcv = stock_ohlcv or {}

        result = []
        for c in candidates:
            code = c.get("code", "?")
            sector = c.get("sector_code", "?")
            dragon_score = float(c.get("dragon_score", 0.0) or 0.0)

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

            row: Dict[str, Any] = {
                "code": code,
                "sector_code": sector,
                "dragon_score": dragon_score,
                "fcoop1_main_net_ratio": fcoop1,
                "fcoop2_main_retail_ratio": fcoop2,
                "fcoop4_turnover_quality": fcoop4,
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
        candidates: List[Dict],
        top_sectors: List[Tuple[str, float]],
        m4_threshold: float = 0.03,
        w_sector: float = 0.4,
        w_dragon: float = 0.35,
        w_coop: float = 0.25,
        stock_flow_recent: dict = None,
        stock_ohlcv: dict = None,
        trade_date: Optional[str] = None,
    ) -> List[Dict]:
        """
        【公开接口】完整处理M4合力验证 + M5最终合成，返回排好序的最终候选列表
        """
        filtered = ForceFactors._apply_cooperative_force_filter(
            candidates,
            m4_threshold,
            stock_flow_recent or {},
            stock_ohlcv or {},
            trade_date=trade_date,
        )

        if not filtered:
            logger.warning("⚠️ M4合力过滤：无候选通过")
            return []

        sector_rank_map = ForceFactors._normalize_sector_ranks(top_sectors)

        coop1_norm = ForceFactors._minmax_normalize(
            {c["code"]: c["main_net_ratio"] for c in filtered}
        )
        coop2_norm = ForceFactors._minmax_normalize(
            {c["code"]: c["main_retail_ratio"] for c in filtered}
        )
        coop3_norm = ForceFactors._minmax_normalize(
            {c["code"]: float(c["main_flow_days"]) for c in filtered}
        )
        coop4_raw = {c["code"]: c["turnover_quality"] for c in filtered}
        if _INVERT_COOP4_FOR_SCORE:
            coop4_raw = {k: -float(v) for k, v in coop4_raw.items()}
        coop4_norm = ForceFactors._minmax_normalize(coop4_raw)

        total_w = max(w_sector + w_dragon + w_coop, 1e-8)
        for c in filtered:
            code = c["code"]
            coop_score = (
                _W_COOP1 * coop1_norm.get(code, 50.0)
                + _W_COOP2 * coop2_norm.get(code, 50.0)
                + _W_COOP3 * coop3_norm.get(code, 50.0)
                + _W_COOP4 * coop4_norm.get(code, 50.0)
            )
            c["final_score"] = (
                w_sector * sector_rank_map.get(c.get("sector_code"), 0)
                + w_dragon * c.get("dragon_score", 0)
                + w_coop * coop_score
            ) / total_w
            c["coop_score"] = coop_score
            c["coop1_norm"] = coop1_norm.get(code, 50.0)
            c["coop2_norm"] = coop2_norm.get(code, 50.0)
            c["coop3_norm"] = coop3_norm.get(code, 50.0)
            c["coop4_norm"] = coop4_norm.get(code, 50.0)

        ranked = sorted(filtered, key=lambda x: x.get("final_score", 0), reverse=True)
        logger.info(f"✅ M4+M5 合力+最终得分计算完成: {len(ranked)} 只候选")
        return ranked

    # ===================== 私有方法 =====================

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
                main_flow = float(today_doc.get("main_net", 0.0) or 0.0) * _WAN_TO_YUAN
                retail_flow = (
                    float(today_doc.get("m_net", 0.0) or 0.0)
                    + float(today_doc.get("s_net", 0.0) or 0.0)
                ) * _WAN_TO_YUAN
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
                    if day_docs:
                        logger.debug(
                            f"  M4日期警告 {code}: 资金流无成交额，fallback至"
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
        }

    @staticmethod
    def _apply_cooperative_force_filter(
        candidates: List[Dict],
        threshold_pct: float = 0.03,
        stock_flow_recent: dict = None,
        stock_ohlcv: dict = None,
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
                c, _flow, _ohlcv, trade_date=trade_date
            )

        result = []
        rejected_reasons = {
            "total_volume<=0": 0,
            "main_flow<=0": 0,
            "net_ratio<threshold": 0,
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
    def _score_turnover_quality(turnover_rate: float) -> float:
        """
        【私有】换手率质量评分（0~1）。turnover_rate 输入口径为百分数（如 2.0 = 2%）。
        """
        t = turnover_rate
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
    def _minmax_normalize(values_dict: Dict[str, float]) -> Dict[str, float]:
        """【私有】min-max归一化转0-100（自动跳过 nan）。"""
        clean = {
            k: float(v)
            for k, v in values_dict.items()
            if v is not None and v == v  # NaN != NaN
        }
        if not clean:
            return {}
        min_val = min(clean.values())
        max_val = max(clean.values())
        if max_val == min_val:
            return {k: 50.0 for k in clean}
        return {k: 100 * (v - min_val) / (max_val - min_val) for k, v in clean.items()}
