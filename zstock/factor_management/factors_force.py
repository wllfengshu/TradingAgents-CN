"""
合力因子计算模块（M4 raw）

公开接口（行情与资金流由调用方传入，本模块不查库）：
- fcoop1_main_net_ratio / fcoop2_main_retail_ratio / fcoop3_sustained_days
- fcoop4_turnover_quality / fcoop5_main_flow_acceleration
- fcoop6_main_force_aggression / fcoop7_super_large_net_ratio
- fcoop8_main_flow_trend_5d / f_mean_reversion_signal 等
- apply_cooperative_force_raw / lhb_bonus

M4 门禁与 M5 合成见 strategy_management。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    flow_docs_asof,
    is_limit_down,
    ohlcv_asof,
    WAN_TO_YUAN,
)

from zstock.common.entity.factor_spec import FactorRegistry, FactorSpec, factor

logger = logging.getLogger(__name__)

# 预计算多窗口（网格搜索用）
_SUSTAINED_WINDOWS = (3, 5, 10)
_DEFAULT_SUSTAINED_WINDOW = 5

# P1a 龙虎榜加分（LHB）阈值
_LHB_INST_RATIO_THRESHOLD = 0.30  # 机构买入占比 >30% 加分
_LHB_TOP1_RATIO_THRESHOLD = 0.30  # 买一席位占比 <30% 加分
_LHB_INST_BONUS = 10.0  # 机构买入占比加分
_LHB_TOP1_BONUS = 8.0   # 游资分散加分
_LHB_BASE_BONUS = 5.0   # 上榜基础加分

# 各因子字段名（apply_cooperative_force_raw 输出）：
#   fcoop1 主力净流入占比 / fcoop2 主散比 / fcoop3 持续性 / fcoop4 换手质量 /
#   fcoop5 主力流入加速度 / fcoop6 主力进攻强度 / fcoop7 超大单净占比 / fcoop8 主力净流入5日趋势
#   及 A级因子 f_power_divergence / f_main_force_persistence / f_mean_reversion_signal


class ForceFactors:
    """合力层因子（M4）：公开单因子算子 + 批处理 raw。"""

    # ===================== 公开接口 =====================

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

            fcoop1 = ForceFactors.fcoop1_main_net_ratio(main_flow, total_volume)
            fcoop2 = ForceFactors.fcoop2_main_retail_ratio(main_flow, retail_flow)

            fcoop4 = ForceFactors.fcoop4_turnover_quality(metrics["turnover_rate"])

            # 【新因子】主力流入加速度
            main_flow_accel = ForceFactors.fcoop5_main_flow_acceleration(metrics["day_docs"])

            # 【新因子】龙头持续性（近5日未跌停天数）
            limit_down_free = ForceFactors.limit_down_free_days(metrics.get("ohlcv_df"), code)

            # 【A级因子1】资金价格背离度
            power_divergence = ForceFactors.f_power_divergence(metrics.get("ohlcv_df"))

            # 【A级因子2】主力执着度
            main_force_persistence = ForceFactors.f_main_force_persistence(metrics["day_docs"])

            # 【A级因子3】超涨反转信号
            mean_reversion_signal = ForceFactors.f_mean_reversion_signal(metrics.get("ohlcv_df"))

            # 【P0新因子】fcoop6: 主力进攻强度
            main_force_aggression = ForceFactors.fcoop6_main_force_aggression(metrics["day_docs"])

            # 【P0新因子】fcoop7: 超大单净占比
            super_large_net_ratio = ForceFactors.fcoop7_super_large_net_ratio(metrics["day_docs"])

            # 【P0新因子】fcoop8: 主力净流入5日趋势
            main_flow_trend_5d = ForceFactors.fcoop8_main_flow_trend_5d(metrics["day_docs"])

            # P1a：龙虎榜加分因子
            lhb_bonus = ForceFactors.lhb_bonus(code, _lhb)

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
                    row[f"fcoop3_sustained_days_{w}d"] = ForceFactors.fcoop3_sustained_days(
                        day_docs, w
                    )
                row["fcoop3_sustained_days"] = row[
                    f"fcoop3_sustained_days_{_DEFAULT_SUSTAINED_WINDOW}d"
                ]

            result.append(row)

        logger.info(f"✅ M4 原始因子收集完成: {len(result)} 只候选（含全部，不过滤）")
        return result

    @staticmethod
    @factor(
        FactorSpec(
            field="fcoop1_main_net_ratio",
            name_en="main_net_ratio",
            name_cn="主力净流入占比",
            definition="main_flow / total_volume",
            inputs=("main_flow", "total_volume"),
            outputs=("float",),
            layer="force",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def fcoop1_main_net_ratio(main_flow: float, total_volume: float) -> float:
        """主力净流入占比：main_flow / total_volume。流出为负，无效为 NaN。"""
        try:
            flow = float(main_flow)
            total = float(total_volume)
        except (TypeError, ValueError):
            return float("nan")
        if not np.isfinite(flow) or not np.isfinite(total) or total <= 0:
            return float("nan")
        return flow / total

    @staticmethod
    @factor(
        FactorSpec(
            field="fcoop2_main_retail_ratio",
            name_en="main_retail_ratio",
            name_cn="主力/散户比",
            definition="main / (main + |retail|)",
            inputs=("main_flow", "retail_flow"),
            outputs=("float",),
            layer="force",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def fcoop2_main_retail_ratio(main_flow: float, retail_flow: float) -> float:
        """主散比：main / (|main| + |retail|)。主力净流出时为负。"""
        try:
            main = float(main_flow)
            retail = float(retail_flow)
        except (TypeError, ValueError):
            return float("nan")
        if not np.isfinite(main) or not np.isfinite(retail):
            return float("nan")
        denom = abs(main) + abs(retail)
        if denom <= 0:
            return float("nan")
        return main / denom

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
                main_flow_days = ForceFactors.fcoop3_sustained_days(
                    day_docs, _DEFAULT_SUSTAINED_WINDOW
                )
            else:
                main_flow = retail_flow = total_volume = 0.0
                main_flow_days = 0.0

            if total_volume == 0.0 and ohlcv_df is not None:
                if not ohlcv_df.empty and "amount" in ohlcv_df.columns:
                    total_volume = float(ohlcv_df["amount"].iloc[-1])
                    # 单位一致性守卫：上游 OHLCV.amount 契约为"元"。若数值明显偏小
                    # （中国 A 股正常个股当日成交额至少在千万级），说明上游数据源
                    # 单位不一致——此时**不做任何自动换算**（错误换算 3 个数量级
                    # 会直接污染 fcoop1 与 M4 门槛，比 total_volume=0 危害更大），
                    # 而是丢弃该值让下游走"无成交额"分支，并打 warning 定位上游。
                    if 0.0 < total_volume < 1_000_000.0:
                        logger.warning(
                            f"  M4单位异常 {code}: 成交额 {total_volume:.0f} 明显偏小，"
                            f"疑似上游 OHLCV.amount 单位不是元；已丢弃该值。"
                            f"请上游修复单位契约，禁止在此层做启发式换算。"
                        )
                        total_volume = 0.0
                    else:
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
    @factor(
        FactorSpec(
            field="fcoop3_sustained_days",
            name_en="sustained_days",
            name_cn="主力持续天数",
            definition="近 window 日主力净流入>0 的天数",
            inputs=("day_docs", "window"),
            outputs=("float",),
            layer="force",
            default_polarity="positive",
            regimes=("momentum",),
            windows=_SUSTAINED_WINDOWS,
            include_base=True,
        )
    )
    def fcoop3_sustained_days(day_docs: list, window: int) -> float:
        """近 window 日主力净流入 > 0 的天数（整数天数，非占比）。

        历史不足 window 日时返回 nan，避免把短窗口结果误标为 *_Nd。
        """
        if not day_docs or window <= 0 or len(day_docs) < window:
            return float("nan")
        tail = day_docs[-window:]
        return float(sum(1 for d in tail if float(d.get("main_net", 0.0) or 0.0) > 0))

    @staticmethod
    @factor(
        FactorSpec(
            field="fcoop5_main_flow_acceleration",
            name_en="main_flow_acceleration",
            name_cn="主力加速",
            definition="主力净流入加速度",
            inputs=("day_docs",),
            outputs=("float",),
            layer="force",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def fcoop5_main_flow_acceleration(day_docs: list) -> float:
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
    def limit_down_free_days(ohlcv_df, code: str = "") -> float:
        """【新因子】龙头持续性：近 5 日未跌停的天数

        衡量龙头的稳定性（避免高位砸盘）。
        跌停按涨跌停价四舍五入判定。需要 6 根收盘（5 个交易日 + 前收）。
        返回值为天数，范围 [0, 5]。
        """
        if ohlcv_df is None or ohlcv_df.empty or len(ohlcv_df) < 6:
            return float("nan")
        if "close" not in ohlcv_df.columns:
            return float("nan")

        closes = ohlcv_df["close"].to_numpy(dtype=float, copy=False)
        tail = closes[-6:]
        if "trade_date" in ohlcv_df.columns:
            dates = ohlcv_df["trade_date"].astype(str).tolist()[-6:]
        else:
            dates = [None] * 6
        free_days = 0
        for i in range(1, 6):
            prev_close = tail[i - 1]
            close = tail[i]
            if not (prev_close > 0) or not np.isfinite(close):
                continue
            if is_limit_down(code or "", prev_close, close, dates[i]):
                continue
            free_days += 1

        return float(free_days)

    @staticmethod
    @factor(
        FactorSpec(
            field="fcoop6_main_force_aggression",
            name_en="main_force_aggression",
            name_cn="主力进攻性",
            definition="主力进攻强度",
            inputs=("day_docs",),
            outputs=("float",),
            layer="force",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def fcoop6_main_force_aggression(day_docs: list) -> float:
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
    @factor(
        FactorSpec(
            field="fcoop7_super_large_net_ratio",
            name_en="super_large_net_ratio",
            name_cn="超大单净占比",
            definition="超大单净流入占比",
            inputs=("day_docs",),
            outputs=("float",),
            layer="force",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def fcoop7_super_large_net_ratio(day_docs: list) -> float:
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
                return float("nan")

            ratio = xl_net / turnover
            ratio = max(-1.0, min(ratio, 1.0))
            return float(ratio)
        except Exception as e:
            logger.debug(f"fcoop7_super_large_net_ratio 计算失败: {e}")
            return float("nan")

    @staticmethod
    @factor(
        FactorSpec(
            field="fcoop8_main_flow_trend_5d",
            name_en="main_flow_trend_5d",
            name_cn="主力趋势(5日)",
            definition="近5日主力净流入占比线性斜率",
            inputs=("day_docs",),
            outputs=("float",),
            layer="force",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def fcoop8_main_flow_trend_5d(day_docs: list) -> float:
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

    @staticmethod
    def sigmoid(x: float, k: float = 1.0, x0: float = 0.0) -> float:
        """Sigmoid激活函数，将任意实数映射到[0,1]
        f(x) = 1 / (1 + exp(-k*(x-x0)))
        """
        try:
            result = 1.0 / (1.0 + np.exp(-k * (x - x0)))
            return float(result) if np.isfinite(result) else 0.5
        except (ValueError, OverflowError):
            return 0.5

    @staticmethod
    @factor(
        FactorSpec(
            field="f_power_divergence",
            name_en="power_divergence",
            name_cn="资金价格背离",
            definition="资金与价格走势背离度",
            inputs=("ohlcv_df",),
            outputs=("float",),
            layer="force",
            default_polarity="positive",
            regimes=("momentum",),
            catalog=False,
        )
    )
    def f_power_divergence(ohlcv_df: Optional[pd.DataFrame]) -> float:
        """
        【A级因子1】量价背离度：个股短期收盘价乖离率 / 成交量乖离率
        如果价格在涨，但成交量在缩，可能是“强控盘无分歧”信号；
        如果价格在涨，成交量爆量，可能是“筹码松动”即将反转信号。
        """
        if ohlcv_df is None or len(ohlcv_df) < 10:
            return float("nan")

        try:
            close = pd.to_numeric(ohlcv_df["close"], errors="coerce")
            vol = pd.to_numeric(ohlcv_df["volume"], errors="coerce")
            if int(close.notna().sum()) < 10 or int(vol.notna().sum()) < 10:
                return float("nan")
            ema5 = close.ewm(span=5, adjust=False).mean()
            vol_ema = vol.ewm(span=20, adjust=False).mean()
            close_divergence = (close - ema5) / ema5
            vol_divergence = (vol - vol_ema) / vol_ema
            divergence = close_divergence * vol_divergence
            last = divergence.iloc[-1]
            return float(last) if np.isfinite(last) else float("nan")
        except Exception as e:
            logger.debug(f"power_divergence计算失败: {e}")
            return float("nan")

    @staticmethod
    @factor(
        FactorSpec(
            field="f_main_force_persistence",
            name_en="main_force_persistence",
            name_cn="主力执着度",
            definition="主力持续买入执着度",
            inputs=("day_docs",),
            outputs=("float",),
            layer="force",
            default_polarity="positive",
            regimes=("momentum",),
            catalog=False,
        )
    )
    def f_main_force_persistence(day_docs: list) -> float:
        """【A级因子2】主力执着度 (Main Force Persistence)

        预期性能：Rank_IC ≥ 0.10, ICIR ≥ 2.0, p ≤ 0.01, Score ≥ 80分

        核心逻辑：
          连续多天主力净流入 > 0，说明主力有明确的目标和计划，
          高度执着且坚决，后续继续推升的概率大。

        公式：
          执着度 = min(连续净流入天数, 连续同向天数) / (窗口天数 + 1)

        只在连续净流入 ≥ 2 且这些天的主力净额 / 成交额 > 10% 时有效。
        """
        if not day_docs or len(day_docs) < 3:
            return float("nan")

        try:
            def _net(doc) -> float:
                return float(doc.get("main_net", 0.0) or 0.0)

            def _ratio(doc) -> float:
                net = _net(doc)
                turnover = float(doc.get("turnover", 0.0) or 0.0)
                if turnover <= 0:
                    return float("nan")
                return net / turnover

            consecutive_positive = 0
            for doc in reversed(day_docs):
                if _net(doc) > 0:
                    consecutive_positive += 1
                else:
                    break
            if consecutive_positive < 2:
                return float("nan")
            streak = list(reversed(day_docs))[:consecutive_positive]
            for doc in streak:
                ratio = _ratio(doc)
                if not np.isfinite(ratio) or ratio <= 0.10:
                    return float("nan")

            latest_sign = 1.0 if _net(day_docs[-1]) > 0 else -1.0
            same_direction = 0
            for doc in reversed(day_docs):
                net = _net(doc)
                if net == 0 or (net > 0) != (latest_sign > 0):
                    break
                same_direction += 1

            window = len(day_docs)
            persistence = min(consecutive_positive, same_direction) / float(window + 1)
            return float(persistence) if np.isfinite(persistence) else float("nan")
        except Exception as e:
            logger.debug(f"main_force_persistence计算失败: {e}")
            return float("nan")

    @staticmethod
    @factor(
        FactorSpec(
            field="f_mean_reversion_signal",
            name_en="mean_reversion_signal",
            name_cn="均值回归信号",
            definition="超涨后均值回归信号",
            inputs=("ohlcv_df",),
            outputs=("float",),
            layer="force",
            default_polarity="negative",
            regimes=("reversal",),
        )
    )
    def f_mean_reversion_signal(ohlcv_df) -> float:
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
            volume_ma20 = volume.rolling(window=20, min_periods=1).mean()
            if volume.iloc[-1] < volume_ma20.iloc[-1]:
                return float("nan")

            reversion_signal = ForceFactors.sigmoid(current_deviation, k=0.5, x0=0.0)
            return float(reversion_signal) if np.isfinite(reversion_signal) else float("nan")
        except Exception as e:
            logger.debug(f"mean_reversion_signal计算失败: {e}")
            return float("nan")

    @staticmethod
    @factor(
        FactorSpec(
            field="fcoop4_turnover_quality",
            name_en="turnover_quality",
            name_cn="换手质量",
            definition="换手率质量评分 0~1",
            inputs=("turnover_rate",),
            outputs=("float",),
            layer="force",
            default_polarity="negative",
            regimes=("reversal",),
        )
    )
    def fcoop4_turnover_quality(turnover_rate: float) -> float:
        """
        换手率质量评分（0~1）。

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
    def lhb_bonus(code: str, stock_lhb_recent: Dict[str, List[Dict]]) -> float:
        """
        P1a 龙虎榜加分计算。

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


# 测评字段（批处理产出，无独立公开算子）
FactorRegistry.register(
    FactorSpec(
        field="dragon_consistency_5d",
        name_en="dragon_consistency_5d",
        name_cn="龙头持续性(5日)",
        definition="近5日未跌停天数",
        inputs=("ohlcv_df",),
        outputs=("float",),
        layer="force",
        default_polarity="positive",
        regimes=("momentum",),
        catalog=False,
        eval=True,
    )
)
FactorRegistry.register(
    FactorSpec(
        field="longhu_board_bonus",
        name_en="longhu_board_bonus",
        name_cn="龙虎榜加分",
        definition="龙虎榜机构/游资结构加分",
        inputs=("code", "stock_lhb_recent"),
        outputs=("float",),
        layer="force",
        default_polarity="positive",
        regimes=("momentum",),
        catalog=False,
        eval=True,
    )
)
