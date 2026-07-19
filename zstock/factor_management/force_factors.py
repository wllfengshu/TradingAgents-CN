"""
合力因子计算模块（M4 & M5）

黑盒设计：
- 公开接口：apply_cooperative_force_and_score()
- 所有实现细节都隐藏在私有方法中
- 职责：合力验证过滤（M4）+ 最终得分合成（M5）

M4 合力过滤逻辑（净值比）：
  门槛：主力净流入 > 0 AND 主力净流入/总成交 >= threshold（默认3%）
  评分：4个子因子加权合成合力综合分
    F_coop1  主力净值比（主力净流入/总成交）        权重 0.35 —— 合力核心
    F_coop2  主散比（主力净流入/|散户净流入|）      权重 0.25 —— 机构主导程度
    F_coop3  净流入持续天数（近N日每日净流入>0天数） 权重 0.25 —— 持续性
    F_coop4  换手质量（换手率是否在合理区间）       权重 0.15 —— 活跃但不过热

M5 最终得分：
  Score = 0.4×板块排名 + 0.35×龙头分 + 0.25×合力综合分
"""

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# 合力综合分子因子权重
_W_COOP1 = 0.35   # F_coop1：主力净值比（核心）
_W_COOP2 = 0.25   # F_coop2：主散比（机构主导）
_W_COOP3 = 0.25   # F_coop3：净流入持续天数
_W_COOP4 = 0.15   # F_coop4：换手质量

# 资金流单位换算：xtquant L2 净额字段(main_net/m_net/s_net)单位为"万元"，
# 成交额字段(turnover)单位为"元"。统一换算到元，保证净值比量纲一致。
_WAN_TO_YUAN = 10000.0


class ForceFactors:
    """合力因子计算器（M4 & M5）。黑盒设计，只负责合力验证和最终合成"""

    # ===================== 公开接口（唯一入口）=====================

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
    ) -> List[Dict]:
        """
        【公开接口】完整处理M4合力验证 + M5最终合成，返回排好序的最终候选列表

        M4 合力过滤：主力净流入 > 0 AND 主力净流入/总成交 >= m4_threshold（默认3%）
        M5 = w_sector×板块排名分 + w_dragon×龙头分 + w_coop×合力综合分

        合力综合分 = 加权合成4个子因子（均在候选集内 min-max 归一化）：
          F_coop1=主力净值比(0.35) + F_coop2=主散比(0.25) +
          F_coop3=净流入持续天数(0.25) + F_coop4=换手质量(0.15)

        candidate dict 需包含字段：code, sector_code, dragon_score
        stock_flow_recent / stock_ohlcv：原始数据，内部提取当日资金流、连流天数、换手率等

        返回：List[Dict]（已排序，每个元素都有 final_score 字段）
        """
        filtered = ForceFactors._apply_cooperative_force_filter(
            candidates, m4_threshold, stock_flow_recent or {}, stock_ohlcv or {},
        )

        if not filtered:
            logger.error("⚠️ M4合力过滤：无候选通过")
            return []

        # 最终得分合成（M5）

        sector_rank_map = ForceFactors._normalize_sector_ranks(top_sectors)

        coop1_norm = ForceFactors._minmax_normalize({c['code']: c['main_net_ratio']   for c in filtered})
        coop2_norm = ForceFactors._minmax_normalize({c['code']: c['main_retail_ratio'] for c in filtered})
        coop3_norm = ForceFactors._minmax_normalize({c['code']: float(c['main_flow_days']) for c in filtered})
        coop4_norm = ForceFactors._minmax_normalize({c['code']: c['turnover_quality']  for c in filtered})

        total_w = max(w_sector + w_dragon + w_coop, 1e-8)
        for c in filtered:
            code = c['code']
            coop_score = (
                _W_COOP1 * coop1_norm.get(code, 50.0)
                + _W_COOP2 * coop2_norm.get(code, 50.0)
                + _W_COOP3 * coop3_norm.get(code, 50.0)
                + _W_COOP4 * coop4_norm.get(code, 50.0)
            )
            c['final_score'] = (
                w_sector * sector_rank_map.get(c.get('sector_code'), 0)
                + w_dragon * c.get('dragon_score', 0)
                + w_coop * coop_score
            ) / total_w
            c['coop_score']  = coop_score
            c['coop1_norm']  = coop1_norm.get(code, 50.0)
            c['coop2_norm']  = coop2_norm.get(code, 50.0)
            c['coop3_norm']  = coop3_norm.get(code, 50.0)
            c['coop4_norm']  = coop4_norm.get(code, 50.0)

        ranked = sorted(filtered, key=lambda x: x.get('final_score', 0), reverse=True)
        logger.info(f"✅ M4+M5 合力+最终得分计算完成: {len(ranked)} 只候选")
        return ranked

    # ===================== 私有方法（实现细节，对外隐藏）=====================

    @staticmethod
    def _apply_cooperative_force_filter(
        candidates: List[Dict],
        threshold_pct: float = 0.03,
        stock_flow_recent: dict = None,
        stock_ohlcv: dict = None,
    ) -> List[Dict]:
        """
        【私有】M4合力过滤（净值比）

        从 stock_flow_recent / stock_ohlcv 内部提取每只票的资金流指标，
        然后按净值比门槛过滤。

        通过条件：主力净流入 > 0 AND 主力净流入/总成交 >= threshold_pct
        通过后将子因子原始值注入 candidate dict 供下游打分使用。

        主散比计算逻辑（统一映射到 [0, 1] 区间，消除分支值域不连续问题）：
          - 散户同向（retail >= 0）：ratio = main / (main + retail)，值域 (0, 1]
          - 散户背离（retail < 0）：ratio = main / (main + |retail|)，值域 (0, 1]
        """
        _flow = stock_flow_recent or {}
        _ohlcv = stock_ohlcv or {}

        # 预先为每个候选提取原始指标（供过滤和日志共用）
        metrics_by_code = {}
        for c in candidates:
            code = c.get('code', '?')
            # 优先采用候选已注入的资金流指标（便于单测/已预算场景，避免重复提取）；
            # 缺失时再从 stock_flow_recent / stock_ohlcv 原始数据提取（生产管线默认路径）。
            # 生产管线构造候选时只注入 code/sector_code/dragon_score，故走提取分支，行为不变。
            injected_main = c.get('main_flow')
            injected_total = c.get('total_volume')
            if injected_main is not None and injected_total is not None:
                main_flow    = float(injected_main or 0.0)
                retail_flow  = float(c.get('retail_flow', 0.0) or 0.0)
                total_volume = float(injected_total or 0.0)
                main_flow_days = float(c.get('main_flow_days', 0.0) or 0.0)
            else:
                # ── 从 stock_flow_recent 提取资金流指标 ──
                day_docs = _flow.get(code, [])
                if day_docs:
                    today_doc = day_docs[-1]  # 升序排列，最后一条为当日
                    # xtquant L2 资金流字段：main_net/m_net/s_net 为净额(万元)，turnover 为成交额(元)。
                    # 净额统一 ×_WAN_TO_YUAN 换算到元，使 main_net_ratio = 主力净流入/总成交 量纲一致。
                    main_flow    = float(today_doc.get('main_net', 0.0) or 0.0) * _WAN_TO_YUAN
                    retail_flow  = (float(today_doc.get('m_net', 0.0) or 0.0)
                                    + float(today_doc.get('s_net', 0.0) or 0.0)) * _WAN_TO_YUAN
                    total_volume = float(today_doc.get('turnover', 0.0) or 0.0)
                    main_flow_days = sum(1 for d in day_docs[-5:] if float(d.get('main_net', 0.0) or 0.0) > 0)
                    main_flow_days = main_flow_days / min(len(day_docs), 5) if day_docs else 0.0
                else:
                    main_flow = retail_flow = total_volume = 0.0
                    main_flow_days = 0
                # 若资金流无成交额，从 OHLCV amount 补充（需警惕日期不一致）
                if total_volume == 0.0 and code in _ohlcv:
                    df = _ohlcv[code]
                    if not df.empty and 'amount' in df.columns:
                        total_volume = float(df['amount'].iloc[-1])
                        if day_docs:
                            logger.error(
                                f"  M4日期警告 {code}: 资金流无成交额，fallback至"
                                f"OHLCV={total_volume:.0f}，可能跨日失真"
                            )
            # ── 换手率：候选已注入则优先，否则从 OHLCV 提取 ──
            injected_tr = c.get('turnover_rate')
            if injected_tr is not None:
                turnover_rate = float(injected_tr or 0.0)
            else:
                turnover_rate = 0.0
                if code in _ohlcv:
                    df = _ohlcv[code]
                    if not df.empty:
                        last_row = df.iloc[-1]
                        tr = last_row.get('turnover_rate')
                        if tr is None:
                            tr = last_row.get('turnover', 0.0)
                        turnover_rate = float(tr or 0.0)

            metrics_by_code[code] = {
                'main_flow': main_flow,
                'retail_flow': retail_flow,
                'total_volume': total_volume,
                'main_flow_days': main_flow_days,
                'turnover_rate': turnover_rate,
            }

        result = []
        rejected_reasons = {'total_volume<=0': 0, 'main_flow<=0': 0, 'net_ratio<threshold': 0}
        for c in candidates:
            code   = c.get('code', '?')
            sector = c.get('sector_code', '?')
            m      = metrics_by_code[code]
            main_flow    = m['main_flow']
            retail_flow  = m['retail_flow']
            total_volume = m['total_volume']

            if total_volume <= 0:
                rejected_reasons['total_volume<=0'] += 1
                logger.info(f"  M4拒绝 {code}({sector}): total_volume={total_volume:.0f} 无成交")
                continue
            if main_flow <= 0:
                rejected_reasons['main_flow<=0'] += 1
                logger.info(f"  M4拒绝 {code}({sector}): main_flow={main_flow:.0f}<=0 主力净流出")
                continue
            main_net_ratio = main_flow / total_volume
            if main_net_ratio < threshold_pct:
                rejected_reasons['net_ratio<threshold'] += 1
                logger.info(f"  M4拒绝 {code}({sector}): main_net_ratio={main_net_ratio:.4f} < threshold={threshold_pct:.4f}")
                continue

            # 主散比：区分散户方向，同向=机构占比，反向=散户卖出力度（越高机构越主导）
            if retail_flow >= 0:
                denominator = main_flow + retail_flow
                main_retail_ratio = main_flow / max(denominator, 1e-8)
            else:
                abs_retail = abs(retail_flow)
                denominator = main_flow + abs_retail
                main_retail_ratio = abs_retail / max(denominator, 1e-8)

            logger.info(f"  M4通过 {code}({sector}): main_flow={main_flow:.0f} retail_flow={retail_flow:.0f} total_vol={total_volume:.0f} net_ratio={main_net_ratio:.4f} retail_ratio={main_retail_ratio:.4f}")
            result.append({
                **c,
                'main_flow':         main_flow,
                'retail_flow':       retail_flow,
                'total_volume':      total_volume,
                'main_net_ratio':    main_net_ratio,
                'main_retail_ratio': main_retail_ratio,
                'main_flow_days':    m['main_flow_days'],
                'turnover_quality':  ForceFactors._score_turnover_quality(m['turnover_rate']),
            })

        logger.info(f"✅ M4 合力过滤: {len(candidates)} → {len(result)} 只")
        if len(result) < len(candidates):
            logger.info(f"  M4拒绝统计: {rejected_reasons}")
            for c in candidates:
                code = c.get('code', '?')
                m = metrics_by_code.get(code, {})
                mf = m.get('main_flow', 0)
                tv = m.get('total_volume', 0)
                ratio = mf / tv if tv > 0 else 0
                logger.info(f"  M4候选明细: {code} main_flow={mf:.0f} total_volume={tv:.0f} net_ratio={ratio:.4f} days={m.get('main_flow_days',0)} turnover={m.get('turnover_rate',0):.4f}")
        return result

    @staticmethod
    def _score_turnover_quality(turnover_rate: float) -> float:
        """
        【私有】换手率质量评分（0~1）。turnover_rate 输入口径为百分数（如 2.0 = 2%），
        与 OHLCV 表存储口径及全系统约定一致。

        优质区间 5%~20%：活跃但不过热
        < 3%：冷盘无关注，分低
        3%~5%：略冷，线性过渡
        20%~30%：过热，线性衰减
        > 30%：散户化追涨，分低
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
        """
        【私有】板块排名归一化为 0~100 分。

        动态计算：第1名=100分，末位=0分，中间线性插值。
        当只有1个板块时，统一给100分。
        """
        n = len(top_sectors)
        if n <= 1:
            return {code: 100.0 for code, _ in top_sectors}
        # 等间距线性分配：rank 0 → 100, rank n-1 → 0
        return {code: 100.0 * (1.0 - idx / (n - 1)) for idx, (code, _) in enumerate(top_sectors)}

    @staticmethod
    def _minmax_normalize(values_dict: Dict[str, float]) -> Dict[str, float]:
        """【私有】min-max归一化转0-100"""
        if not values_dict:
            return {}
        min_val = min(values_dict.values())
        max_val = max(values_dict.values())
        if max_val == min_val:
            return {k: 50.0 for k in values_dict.keys()}
        return {k: 100 * (v - min_val) / (max_val - min_val) for k, v in values_dict.items()}


