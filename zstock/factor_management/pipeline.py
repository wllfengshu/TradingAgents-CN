"""
截面日频策略管道（Pipeline）

核心策略：市场情绪 → 选主线 → 选龙头 → 选合力

流程编排：M0(数据) → M1(情绪) → M2(主线) → M3(龙头) → M4(合力) → M5(合成) → M6(输出)
因子公式委托给各自 Factor 类（MarketFactors / SectorFactors / DragonFactors / ForceFactors）。

两条入口：
1. run_pipeline / score_signals_live —— 现场计算 OHLCV+资金流（实时选股）
2. score_signals —— 从 MongoDB 读预计算原始值，用当前权重打分（回测/网格搜索快路径）

M0  数据装载     OHLCV + 资金流 + 板块映射
M1  市场情绪     5因子 → 仓位系数（红/黄/绿）
M2  选主线       8因子 → Top N 板块
M3  选龙头       5因子 → 每板块 Top M 龙头
M4  选合力       4因子复合 + 风格检测动态调权
M5  最终合成     3层加权 → 策略信号分
M6  输出         TopK → buy/watch 信号
"""

from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

import pandas as pd

from .dragon_factors import DragonFactors
from .force_factors import ForceFactors
from .market_factors import MarketFactors
from .prefilters import PreFilters
from .sector_factors import SectorFactors
from .style_detector import StyleDetector
from zstock.data_management.query_service import get_data_query_service

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = (
    Path(__file__).parent.parent / "common" / "config" / "strategy_params.json"
)


class CrossSectionStrategyPipeline:
    """
    截面日频策略管道 —— 市场情绪 → 选主线 → 选龙头 → 选合力

    ═══════════════════════════════════════════════════════════════════
    策略分层（自顶向下，逐层收敛）
    ═══════════════════════════════════════════════════════════════════

    M0  数据装载          load_real_data()
         ├─ 全 A 股 OHLCV（往前 lookback 天）
         ├─ 个股资金流（L2 逐档数据）
         ├─ 沪深 300 指数 OHLCV
         └─ 板块定义 + 成分股映射

    M1  市场情绪          市场风险等级 → 仓位系数
         ├─ 5 因子合成：MF1~MF5（斜率/布林/量比/动量/ATR）
         ├─ 输出：green(满仓) / yellow(0.4倍仓) / red(空仓)
         └─ red 时直接终止，不进入后续选股

    M2  选主线            板块因子打分 → Top N 板块
         ├─ 8 个板块因子：RPS/主力资金/涨停密度/连板/量比斜率/量比增长/持续性
         ├─ 加权打分 → 取 top_sectors 个板块
         └─ 输出：主线板块列表

    M3  选龙头            板块内龙头因子打分 → 每板块 Top M 龙头
         ├─ 龙头因子：超额收益/成交额/连板/量价共振/布林趋势
         ├─ 板块内排名 → 每板块取 top_per_sector 只
         └─ 输出：候选龙头池（M4 入池）

    M4  选合力            主力资金验证 → 过滤 + 复合打分
         ├─ 过滤：主力净流入 > 0 且 净流入/总成交 >= 门槛(cooperative_force.threshold_pct)
         ├─ 合力因子复合（配置驱动 active_factors.force，按 regime 路由）：
         │   动量市：fcoop8/7/1/6 + fcoop5（主力净流入趋势/超大单/净占比/进攻/加速度）
         │   反转市：fcoop4 + f_mean_reversion（换手质量 + 超涨反转，均取反）
         └─ 风格检测器按 regime 切换动量/反转因子集

    M5  最终合成          三层加权 → 策略信号分
         ├─ 权重：sector(0.40) + dragon(0.35) + cooperative(0.25)
         ├─ 合力分 = 合力因子复合分（min-max归一化）+ 龙虎榜加分
         └─ 策略信号分 = 加权求和 → 排序

    M6  输出              取 topK 只 → buy/watch 信号
         ├─ topK 以内 → buy，以外但候选池内 → watch（供排名退出）
         ├─ 附加：market_risk_level / position_scale_factor
         └─ 返回 DataFrame（trade_date, code, scores, signal_type, ...）

    ═══════════════════════════════════════════════════════════════════
    两条入口
    ═══════════════════════════════════════════════════════════════════

    1. 现场计算路径（预计算用）
       run_pipeline() → _execute_pipeline_core()
       → compute_all_factor_raw()  ← M0→M4 全流程现场计算
       → 写入 MongoDB zstock_factor_* 集合

    2. 预计算打分路径（回测/网格搜索用）
       score_signals()
       → 从 MongoDB 读预计算原始值
       → 用当前权重/门槛重新打分
       → 不加载 OHLCV/资金流（改权重无需重跑预计算）
    """

    def __init__(
        self,
        config_path: str = None,
        config_override: Optional[Dict] = None,
    ):
        """
        Args:
            config_path: 策略配置文件路径
            config_override: 覆盖配置（改权重/门槛/topK，供网格搜索）
        """
        if config_path is None:
            config_path = str(_DEFAULT_CONFIG_PATH)
        self.config = self._load_config(str(config_path))
        if config_override:
            self._merge_config(config_override)
        config_dir = Path(config_path).parent
        self.prefilters = PreFilters(config_dir=str(config_dir))
        self._query_service = get_data_query_service()
        self._precomputed_cache: Optional["PrecomputedFactorCache"] = None
        self._all_stocks_cache: Optional[List[Dict[str, Any]]] = None
        logger.info("✅ 策略管道初始化完成")

    def _load_config(self, config_path: str) -> Dict:
        """加载策略配置（JSON 格式）"""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            logger.info(f"✅ 加载配置文件: {config_path}")
            return config
        except Exception as e:
            logger.error(f"❌ 加载配置文件失败: {e}")
            raise

    def _merge_config(self, override: Dict) -> None:
        """合并配置覆盖。"""
        for k, v in override.items():
            if (
                k in self.config
                and isinstance(v, dict)
                and isinstance(self.config[k], dict)
            ):
                self.config[k] = {**self.config[k], **v}
            else:
                self.config[k] = v

    def _cfg_from_layered_config(self, layer_key: str, config_key: str, default: Any) -> Any:
        """统一的分层配置查询（先查分层，再查全局）。使用 None 检查避免 falsy 值被忽略。"""
        val = self.config.get(layer_key, {}).get(config_key)
        if val is not None:
            return val
        return self.config.get(config_key, default)

    def _cfg_top_sectors(self) -> int:
        return int(self._cfg_from_layered_config("sector_layer", "top_sectors", 3))

    def _cfg_top_per_sector(self) -> int:
        return int(self._cfg_from_layered_config("dragon_layer", "top_per_sector", 3))

    def _cfg_top_k(self, regime: str = "neutral") -> int:
        fs = self.config.get("final_score") or {}
        by_regime = fs.get("by_regime") or {}
        sub = by_regime.get(regime) or by_regime.get(str(regime))
        if isinstance(sub, dict) and sub.get("top_k") is not None:
            return int(sub["top_k"])
        return int(fs.get("top_k", 5))

    def _cfg_final_weights(self) -> Dict[str, float]:
        default = {"sector": 0.4, "dragon": 0.35, "cooperative": 0.25}
        cfg = self._cfg_from_layered_config("final_score", "weights", {}) or {}
        return {**default, **cfg}

    def _resolve_position_scale(
        self, grade: str, scale: float, regime: str = "neutral"
    ) -> float:
        """strategy_params.market_overlay 可覆盖 M1 默认黄/绿灯仓位系数；支持 by_regime。"""
        overlay = self.config.get("market_overlay") or {}
        by_regime = overlay.get("by_regime") or {}
        sub = by_regime.get(regime) or by_regime.get(str(regime))
        if isinstance(sub, dict):
            key = {
                "green": "position_scale_green",
                "yellow": "position_scale_yellow",
                "red": "position_scale_red",
            }.get(str(grade), "")
            if key and sub.get(key) is not None:
                return float(sub[key])
        key = {
            "green": "position_scale_green",
            "yellow": "position_scale_yellow",
            "red": "position_scale_red",
        }.get(str(grade), "")
        if key and overlay.get(key) is not None:
            return float(overlay[key])
        return float(scale)

    def _get_active_factors(self, regime: str = "neutral") -> Dict[str, Any]:
        from zstock.factor_management.factor_decay import apply_factor_decay

        base = self.config.get("active_factors") or {}
        return apply_factor_decay(
            base, self.config.get("factor_decay") or {}, regime=regime
        )

    async def preload_precomputed_factors(
        self, start_date: str, end_date: str
    ) -> None:
        """回测前批量加载 M1~M4 预计算因子，后续 score_signals 走内存。"""
        from .precomputed_cache import PrecomputedFactorCache

        if self._precomputed_cache is None:
            self._precomputed_cache = PrecomputedFactorCache()
        await self._precomputed_cache.preload(
            self._query_service, start_date, end_date
        )
        if self._all_stocks_cache is None:
            docs, _ = await self._query_service.get_all_stocks()
            self._all_stocks_cache = docs

    async def _get_all_stocks_cached(self) -> List[Dict[str, Any]]:
        if self._all_stocks_cache is None:
            docs, _ = await self._query_service.get_all_stocks()
            self._all_stocks_cache = docs
        return self._all_stocks_cache

    async def _detect_style(self, trade_date: str) -> Dict[str, object]:
        """检测当日市场风格（动量/反转/中性），带缓存。

        style_switching=false 时跳过检测直接返回中性，
        避免每个再平衡日多余的沪深300指数查询。
        """
        if not self.config.get("style_switching", True):
            return {
                "regime": "neutral",
                "momentum_weight": 0.5,
                "reversal_weight": 0.5,
            }
        cache_key = f"_style_{trade_date}"
        if hasattr(self, cache_key):
            return getattr(self, cache_key)
        try:
            detector = StyleDetector()
            result = await detector.detect_from_mongo(trade_date)
        except Exception as e:
            logger.warning(f"风格检测失败，使用中性默认: {e}")
            result = {
                "regime": "neutral",
                "momentum_weight": 0.5,
                "reversal_weight": 0.5,
            }
        setattr(self, cache_key, result)
        return result

    def _top_by_score(
        self,
        scores_dict: Dict[str, float],
        top_k: int,
        reverse: bool = True,
    ) -> List[Tuple[str, float]]:
        """按得分排序并取前 K 个"""
        return sorted(scores_dict.items(), key=lambda x: x[1], reverse=reverse)[:top_k]

    @staticmethod
    def _make_candidate(
        code: str,
        sector_code: str,
        dragon_score: float,
        **extra_fields
    ) -> Dict:
        """构建候选股票字典"""
        return {
            'code': code,
            'sector_code': sector_code,
            'dragon_composite_score': dragon_score,
            **extra_fields,
        }

    # ===================== 数据装载 =====================

    async def load_real_data(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 60,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        从数据库加载真实数据，返回 run_pipeline 所需的入参字典

        数据源：MongoDB（通过 query_service）

        Args:
            trade_date: 交易日期（YYYY-MM-DD 格式），默认今天
            lookback_days: 回溯天数，用于计算技术指标（默认 60 天）
            sectors: 指定板块列表，None 表示全量板块
            max_stocks: 限制处理的股票数量，None 表示全量

        Returns:
            包含以下字段的字典：
            - trade_date: 交易日期
            - all_stocks: 股票代码列表
            - stock_infos: 股票信息字典 {code: {name, is_st, ...}}
            - stock_ohlcv: OHLCV 数据字典 {code: DataFrame}
            - stock_flow_recent: 近期资金流字典 {code: List[Dict]}
            - sectors: 板块列表
            - sector_stocks: 板块成分股字典 {sector_code: [stock_codes]}
            - index_ohlcv: 指数 OHLCV 数据（沪深 300）
            - index_name: 指数名称
        """
        from zstock.data_management.query_service import get_data_query_service

        qs = get_data_query_service()

        # 1. 确定交易日期
        td = trade_date or datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.strptime(td, '%Y-%m-%d') - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        logger.info(f"📥 [real-data] 开始加载数据: trade_date={td}, lookback={lookback_days}天 ({start_date} ~ {td})")

        # 2. 并发加载基础元数据（互不依赖）
        all_stock_result, sector_result = await asyncio.gather(
            qs.get_all_stocks(),
            qs.get_sector_list(),
        )
        all_stock_docs, _ = all_stock_result
        if max_stocks:
            all_stock_docs = all_stock_docs[:max_stocks]
        all_stocks = [d['code'] for d in all_stock_docs]
        stock_infos: Dict[str, Dict] = {
            doc['code']: {
                'code': doc['code'],
                'name': doc.get('name', ''),
                'is_st': doc.get('is_st', False),
                'is_mainboard': doc.get('is_mainboard', False),
            }
            for doc in all_stock_docs
        }
        logger.info(f"✅ [real-data] 股票信息加载完成: {len(all_stocks)} 只")

        # 3. 处理板块数据
        sector_list, _ = sector_result
        sector_list = sector_list or []
        if sectors:
            sector_list = [s for s in sector_list if s.get('sector_code') in sectors]

        # 构建板块成分股映射
        sector_stocks_map = {}
        for sector in sector_list:
            sector_code = sector.get('sector_code')
            stocks = sector.get('stocks', [])
            # 只保留在 all_stocks 中的股票
            stocks = [s for s in stocks if s in all_stocks]
            if stocks:  # 只保留有成分股的板块
                sector_stocks_map[sector_code] = stocks

        logger.info(f"✅ [real-data] 板块数据加载完成: {len(sector_stocks_map)} 个板块")

        # 4. 并发加载依赖 all_stocks 的大表数据
        flow_days = max(15, lookback_days // 4)
        stock_ohlcv, stock_flow_recent, index_ohlcv = await asyncio.gather(
            qs.get_ohlcv_batch(all_stocks, start_date, td),
            qs.get_capital_flow_recent_days(all_stocks, end_date=td, days=flow_days),
            qs.get_ohlcv_batch(['399300'], start_date, td),
        )

        logger.info(f"✅ [real-data] OHLCV 数据加载完成: {len(stock_ohlcv)} 只股票")
        logger.info(
            f"✅ [real-data] 资金流数据加载完成: {len(stock_flow_recent)} 只股票 "
            f"(days={flow_days})"
        )

        # 5. 指数数据（沪深 300）
        index_name = '沪深 300'
        logger.info(f"✅ [real-data] 指数数据加载完成: {index_name}")

        return {
            'trade_date': td,
            'all_stocks': all_stocks,
            'stock_infos': stock_infos,
            'stock_ohlcv': stock_ohlcv,
            'stock_flow_recent': stock_flow_recent,
            'sectors': sector_list,
            'sector_stocks': sector_stocks_map,
            'index_ohlcv': index_ohlcv,
            'index_name': index_name,
        }

    # ===================== 主流程 =====================

    async def _execute_pipeline_core(
        self,
        trade_date: str,
        all_stocks: List[str],
        stock_infos: Dict[str, Dict],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Dict[str, List[Dict]],
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        index_ohlcv: Dict[str, pd.DataFrame],
        index_name: str = '沪深 300',
        collect_raw: bool = False,
    ) -> Dict[str, Any]:
        """
        统一的 M1→M6 策略核心流程（单一事实来源 SSOT）。

        Args:
            collect_raw: True 时收集 M2/M3/M4 的原始因子数据，用于预计算存储；
                        False 时仅保留必要的评分结果。

        Returns:
            包含所有中间结果的字典，由调用方（run_pipeline_raw/run_pipeline）适配返回值。
        """
        logger.info(f"🚀 开始执行策略管道核心流程: trade_date={trade_date}, collect_raw={collect_raw}")

        # ===== M1: 市场情绪评估 =====
        idx_df = self._get_index_df(index_ohlcv)
        index_code = list(index_ohlcv.keys())[0] if index_ohlcv else ""
        market_sentiment = MarketFactors.calculate_market_sentiment(
            idx_df,
            index_name=index_name,
            trade_date=trade_date,
        )
        market_raw = market_sentiment.get('detail', {}) if collect_raw else {}
        logger.info(
            f"📊 M1 市场情绪评估完成: score={market_sentiment.get('market_composite_score', 0):.2f} "
            f"grade={market_sentiment.get('market_risk_level')}"
        )

        # M1 红灯判定 - 早期返回
        if not market_sentiment.get("allow_new_open", True):
            logger.info(f"🔴 {trade_date} 市场红灯，管道终止")
            return {
                'early_exit': True,
                'exit_reason': 'market_red_light',
                'trade_date': trade_date,
                'market_raw': market_raw,
                'index_code': index_code,
                'index_name': index_name,
                'market_risk_level': market_sentiment.get('market_risk_level', 'red'),
                'position_scale_factor': market_sentiment.get('position_scale_factor', 0.0),
                'market_composite_score': market_sentiment.get('market_composite_score', 0.0),
            }

        # ★ P4 风格自适应：检测市场风格，传递给 M2/M3 层
        style_info = await self._detect_style(trade_date)
        regime = style_info["regime"]
        logger.info(
            f"🎨 {trade_date} 市场风格: {regime} | "
            f"autocorr={style_info.get('autocorr', 0):.3f}"
        )

        # ===== M2.1-M3.2: 宇宙过滤（技术过滤 + 黑名单）=====
        filtered_stocks_set, filtered_sectors, blacklisted_codes = await self._apply_universe_filters(
            all_stocks, stock_ohlcv, stock_infos, sectors
        )
        logger.info(f"🔍 M2.1 黑名单过滤: {len(sectors)} → {len(filtered_sectors)} 个板块")
        logger.info(
            f"🔍 M3.1-M3.2 个股过滤: {len(all_stocks)} → {len(filtered_stocks_set)} 只股票"
        )

        # ===== M2: 板块筛选 =====
        market_sector_ohlcv = self._compute_market_sector_ohlcv(
            None, sector_stocks, stock_ohlcv, stock_flow_recent
        )
        sector_raw = SectorFactors.calculate_all_sector_factors_raw(
            filtered_sectors,
            sector_stocks,
            stock_ohlcv,
            stock_flow_recent,
            market_sector_ohlcv=market_sector_ohlcv,
            trade_date=trade_date,
            eligible_codes=filtered_stocks_set,
        )
        m2_scores = SectorFactors.scores_from_raw(
            sector_raw,
            regime=regime,
            active_factors=self._get_active_factors(regime),
            top_n=self._cfg_top_sectors(),
        )
        if not collect_raw:
            sector_raw = {}
        logger.info(f"📊 M2.2 板块得分计算完成: {len(m2_scores)} 个板块")

        top_k = self._cfg_top_sectors()
        top_sectors = self._top_by_score(m2_scores, top_k)
        logger.info(f"✅ M2.3 选出 Top {top_k} 板块: {[s[0] for s in top_sectors]}")

        # ===== M3: 龙头股筛选 =====
        all_candidates = []
        m3_scores_by_sector: Dict[str, Dict[str, float]] = {}
        dragon_raw_by_sector: Dict[str, Dict] = {}
        top_per_sector = self._cfg_top_per_sector()

        for sector_code, _ in top_sectors:
            sector_stock_list = [
                s for s in sector_stocks.get(sector_code, []) if s in filtered_stocks_set
            ]
            if not sector_stock_list:
                continue

            m3_scores, dragon_raw = await self._compute_dragon_scores_for_sector(
                sector_code,
                sector_stock_list,
                stock_ohlcv,
                blacklisted_codes=blacklisted_codes,
                trade_date=trade_date,
                regime=regime,
            )
            if not dragon_raw:
                continue

            if collect_raw:
                m3_scores_by_sector[sector_code] = m3_scores
                dragon_raw_by_sector[sector_code] = dragon_raw

            candidates = self._top_by_score(m3_scores, top_per_sector)
            for code, m3_score in candidates:
                all_candidates.append(self._make_candidate(code, sector_code, m3_score))

        logger.info(f"✅ M3 龙头股筛选完成: {len(all_candidates)} 只候选")

        # M3 无候选 - 早期返回
        if not all_candidates:
            logger.warning("⚠️ M3 筛选后无候选股票，返回空结果")
            return {
                'early_exit': True,
                'exit_reason': 'no_candidates',
                'trade_date': trade_date,
                'market_raw': market_raw,
                'index_code': index_code,
                'index_name': index_name,
                'sector_raw': sector_raw,
                'm3_scores_by_sector': m3_scores_by_sector,
                'dragon_raw_by_sector': dragon_raw_by_sector,
                'all_candidates': all_candidates,
                'ranked_candidates': [],
                'market_risk_level': market_sentiment.get('market_risk_level', 'neutral'),
                'position_scale_factor': market_sentiment.get('position_scale_factor', 1.0),
                'market_composite_score': market_sentiment.get('market_composite_score', 0.0),
            }

        # ===== M4+M5: 合力评分 =====
        force_raw, ranked_candidates = await self._apply_force_factors(
            all_candidates,
            top_sectors,
            stock_flow_recent=stock_flow_recent,
            stock_ohlcv=stock_ohlcv,
            trade_date=trade_date,
        )
        if not collect_raw:
            force_raw = []

        # ===== M6: 最终选股 =====
        ranked_all = ranked_candidates or []
        top_k_final = self._cfg_top_k(regime)
        signals = ranked_all[:top_k_final]
        grade = market_sentiment.get('market_risk_level', 'neutral')
        scale = self._resolve_position_scale(
            grade, float(market_sentiment.get('position_scale_factor', 1.0)), regime=regime
        )
        for sig in signals:
            sig['market_risk_level'] = grade
            sig['position_scale_factor'] = scale

        logger.info(f"🎉 策略管道核心流程执行完成: 共选出 {len(signals)} 只股票")

        return {
            'early_exit': False,
            'trade_date': trade_date,
            'market_raw': market_raw,
            'index_code': index_code,
            'index_name': index_name,
            'sector_raw': sector_raw,
            'm2_scores': m2_scores,
            'top_sectors': top_sectors,
            'filtered_sectors': filtered_sectors,
            'm3_scores_by_sector': m3_scores_by_sector,
            'dragon_raw_by_sector': dragon_raw_by_sector,
            'all_candidates': all_candidates,
            'force_raw': force_raw,
            'ranked_candidates': signals,
            'ranked_all': ranked_all,
            'market_risk_level': grade,
            'position_scale_factor': scale,
            'regime': regime,
            'market_composite_score': market_sentiment.get('market_composite_score', 0.0),
            'stock_infos': stock_infos,
        }

    async def run_pipeline(
        self,
        trade_date: str,
        all_stocks: List[str],
        stock_infos: Dict[str, Dict],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Dict[str, List[Dict]],
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        index_ohlcv: Dict[str, pd.DataFrame],
        index_name: str = '沪深 300',
    ) -> List[Dict]:
        """执行完整的截面日频策略流程，返回最终选出的股票列表。"""
        result = await self._execute_pipeline_core(
            trade_date, all_stocks, stock_infos, stock_ohlcv, stock_flow_recent,
            sectors, sector_stocks, index_ohlcv, index_name, collect_raw=False
        )
        if result.get('early_exit'):
            return []
        return result['ranked_candidates']

    async def score_signals_live(
        self,
        trade_date: str,
        lookback_days: int = 60,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
        prebuilt_data: Optional[Dict[str, Any]] = None,
    ) -> pd.DataFrame:
        """
        实时计算 M0~M5 → 输出与 score_signals() 相同 schema 的 DataFrame。

        供 SignalGenerator / 非预计算回测使用；含 buy/watch 候选池与市场仓位元数据。
        """
        td = trade_date
        if prebuilt_data is not None:
            data = prebuilt_data
        else:
            data = await self.load_real_data(
                trade_date=td,
                lookback_days=lookback_days,
                sectors=sectors,
                max_stocks=max_stocks,
            )

        result = await self._execute_pipeline_core(
            data["trade_date"],
            data["all_stocks"],
            data["stock_infos"],
            data["stock_ohlcv"],
            data["stock_flow_recent"],
            data["sectors"],
            data["sector_stocks"],
            data["index_ohlcv"],
            data.get("index_name", "沪深 300"),
            collect_raw=False,
        )
        grade = str(result.get("market_risk_level", "unknown"))
        scale = float(result.get("position_scale_factor", 1.0))

        if result.get("early_exit"):
            logger.info(
                f"{td} 实时管道终止: {result.get('exit_reason')} grade={grade}"
            )
            return self._empty_signals_df(
                td, market_risk_level=grade, position_scale_factor=scale
            )

        ranked_all = result.get("ranked_all") or []
        if not ranked_all:
            logger.warning(f"{td} 实时管道无候选")
            return self._empty_signals_df(
                td, market_risk_level=grade, position_scale_factor=scale
            )

        return self._ranked_to_signals_df(
            ranked_all, td, grade, scale, regime=str(result.get("regime", "neutral"))
        )

    # ===================== 预计算打分快路径 =====================

    async def score_signals(self, trade_date: str) -> pd.DataFrame:
        """
        从 MongoDB 读预计算原始值 → 用当前权重打分 → signals DataFrame。

        不加载 OHLCV/资金流；改权重/门槛/topK 无需重跑预计算。
        直接使用 MongoDB 规范字段名（f21_rps_20d 等），无需转换。
        """
        td = trade_date
        qs = self._query_service
        cache = self._precomputed_cache

        if cache is not None and cache.loaded:
            market_doc = cache.get_factor_market(td)
        else:
            market_doc = await qs.get_factor_market(td)
        if market_doc is None:
            raise ValueError(f"无预计算 M1 数据: {td}")

        sentiment = MarketFactors.score_from_raw(
            mf1_slope_pct=float(market_doc.get("mf1_slope_pct", float("nan"))),
            mf2_boll_pct=float(market_doc.get("mf2_boll_pct", float("nan"))),
            mf3_vol_ratio=float(market_doc.get("mf3_vol_ratio", float("nan"))),
            mf4_momentum_5d=float(market_doc.get("mf4_momentum_5d", float("nan"))),
            mf5_atr_ratio=float(market_doc.get("mf5_atr_ratio", float("nan"))),
        )
        grade = sentiment["market_risk_level"]

        if grade == "red":
            logger.info(f"🔴 {td} 市场红灯，无信号")
            return self._empty_signals_df(
                td, market_risk_level=grade, position_scale_factor=float(
                    self._resolve_position_scale(
                        grade, sentiment["position_scale_factor"], regime="neutral"
                    )
                )
            )

        # ★ P4 风格自适应：提前检测市场风格，传递给 M2/M3 层及仓位 overlay
        style_info = await self._detect_style(td)
        regime = style_info["regime"]
        position_scale_factor = self._resolve_position_scale(
            grade, sentiment["position_scale_factor"], regime=regime
        )
        logger.info(
            f"🎨 {td} 市场风格: {regime} | "
            f"autocorr={style_info.get('autocorr', 0):.3f} "
            f"momentum_w={style_info['momentum_weight']:.2f} "
            f"reversal_w={style_info['reversal_weight']:.2f}"
        )

        if cache is not None and cache.loaded:
            sector_docs = cache.get_factor_sectors(td)
        else:
            sector_docs = await qs.get_factor_sectors(td)
        if not sector_docs:
            raise ValueError(f"无预计算 M2 数据: {td}")

        # 直接用规范字段名调用 scores_from_raw()，无需 _reconstruct_sector_scores()
        # P3 新因子 f27/f29/f30 必须传入，否则 scores_from_raw 降级到旧因子路径
        m2_scores = SectorFactors.scores_from_raw({
            "f21_rps_20d": {d["sector_code"]: d.get("f21_rps_20d", d.get("f21_rps", float("nan"))) for d in sector_docs},
            "f21_rps_10d": {d["sector_code"]: d.get("f21_rps_10d", float("nan")) for d in sector_docs},
            "f21_rps_60d": {d["sector_code"]: d.get("f21_rps_60d", float("nan")) for d in sector_docs},
            "f22_main_flow": {d["sector_code"]: d.get("f22_main_flow", 0.0) for d in sector_docs},
            "f23_limit_up_density": {d["sector_code"]: d.get("f23_limit_up_density", 0.0) for d in sector_docs},
            "f24_max_consecutive": {d["sector_code"]: d.get("f24_max_consecutive", 0.0) for d in sector_docs},
            "f25_volume_slope_5d": {d["sector_code"]: d.get("f25_volume_slope_5d", d.get("f25_volume_slope", float("nan"))) for d in sector_docs},
            "f25_volume_slope_3d": {d["sector_code"]: d.get("f25_volume_slope_3d", float("nan")) for d in sector_docs},
            "f25_volume_slope_10d": {d["sector_code"]: d.get("f25_volume_slope_10d", float("nan")) for d in sector_docs},
            "f26_volume_growth_5d": {d["sector_code"]: d.get("f26_volume_growth_5d", d.get("f26_volume_growth", float("nan"))) for d in sector_docs},
            "f26_volume_growth_20d": {d["sector_code"]: d.get("f26_volume_growth_20d", float("nan")) for d in sector_docs},
            "f27_new_high_ratio": {d["sector_code"]: d.get("f27_new_high_ratio", float("nan")) for d in sector_docs},
            "f28_consistency": {d["sector_code"]: d.get("f28_consistency", 0) for d in sector_docs},
            "f29_sector_breadth": {d["sector_code"]: d.get("f29_sector_breadth", float("nan")) for d in sector_docs},
            "f30_sector_concentration": {d["sector_code"]: d.get("f30_sector_concentration", float("nan")) for d in sector_docs},
        }, regime=regime, active_factors=self._get_active_factors(regime), top_n=self._cfg_top_sectors())

        top_k_sectors = self._cfg_top_sectors()
        top_sectors = sorted(m2_scores.items(), key=lambda x: x[1], reverse=True)[
            :top_k_sectors
        ]

        all_stocks_docs = await self._get_all_stocks_cached()
        all_stocks = [d["code"] for d in all_stocks_docs]
        stock_infos = {d["code"]: d for d in all_stocks_docs}

        # 【优化】统一调用 2 个公开接口（不需要sectors）
        tech_filtered = self.prefilters.apply_technical_filters(
            all_stocks, {}, stock_infos, apply_main_board=True, apply_bollinger=False
        )
        blacklist_result = self.prefilters.apply_blacklist_filters(tech_filtered)
        non_blacklisted = blacklist_result["stocks"]
        blacklisted_set = set(tech_filtered) - set(non_blacklisted)

        top_sector_codes = [sc for sc, _ in top_sectors]
        if cache is not None and cache.loaded:
            dragon_docs = cache.get_factor_dragons(td, sector_codes=top_sector_codes)
        else:
            dragon_docs = await qs.get_factor_dragons(td, sector_codes=top_sector_codes)
        if not dragon_docs:
            logger.warning(f"{td} 无 M3 数据 for top sectors")
            return self._empty_signals_df(
                td, market_risk_level=grade, position_scale_factor=float(position_scale_factor)
            )

        dragon_by_sector: Dict[str, List[Dict]] = {}
        for doc in dragon_docs:
            sc = doc.get("sector_code", "")
            dragon_by_sector.setdefault(sc, []).append(doc)

        top_per_sector = self._cfg_top_per_sector()
        all_candidates: List[Dict] = []

        for sector_code, _ in top_sectors:
            sector_dragons = dragon_by_sector.get(sector_code, [])
            if not sector_dragons:
                continue

            # 直接用规范字段名调用 scores_from_raw()，无需 _reconstruct_dragon_scores()
            # P3 新因子 f36/f37 必须传入，否则 scores_from_raw 降级到旧因子路径（f32/f34）
            dragon_map = {d["code"]: d for d in sector_dragons}  # O(n) 而非 O(n²)
            m3_scores = DragonFactors.scores_from_raw({
                code: {
                    "f31b_rps_percentile": d.get("f31b_rps_percentile", float("nan")),
                    "f31_excess_return_5d": d.get("f31_excess_return_5d", d.get("f31_excess_return", float("nan"))),
                    "f31_excess_return_10d": d.get("f31_excess_return_10d", float("nan")),
                    "f31_excess_return_15d": d.get("f31_excess_return_15d", float("nan")),
                    "f31_excess_return_20d": d.get("f31_excess_return_20d", float("nan")),
                    "f32_amount": d.get("f32_amount", float("nan")),
                    "f33_consecutive_boards": d.get("f33_consecutive_boards", 0),
                    "f34_resonance_pct_5d": d.get("f34_resonance_pct_5d", d.get("f34_resonance_pct", float("nan"))),
                    "f34_resonance_pct_3d": d.get("f34_resonance_pct_3d", float("nan")),
                    "f34_resonance_pct_10d": d.get("f34_resonance_pct_10d", float("nan")),
                    "f35_bollinger_trend": d.get("f35_bollinger_trend", float("nan")),
                    "f35_bollinger_pass": d.get("f35_bollinger_pass", 0),
                    "f36_identity_premium": d.get("f36_identity_premium", float("nan")),
                    "f37_relative_strength": d.get("f37_relative_strength", float("nan")),
                    "f38_turnover_anomaly": d.get("f38_turnover_anomaly", float("nan")),
                    # 基本面因子
                    "f39_pb": d.get("f39_pb", float("nan")),
                    "f40_holder_change": d.get("f40_holder_change", float("nan")),
                }
                for code, d in dragon_map.items()
            }, regime=regime, active_factors=self._get_active_factors(regime), scoring_method=self.config.get("scoring_method", "linear"))

            if blacklisted_set:
                m3_scores = {
                    k: v for k, v in m3_scores.items() if k not in blacklisted_set
                }
            if not m3_scores:
                continue
            candidates = self._top_by_score(m3_scores, top_per_sector)
            for code, m3_score in candidates:
                all_candidates.append(self._make_candidate(code, sector_code, m3_score))

        if not all_candidates:
            logger.warning(f"{td} 无 M3 候选")
            return self._empty_signals_df(
                td, market_risk_level=grade, position_scale_factor=float(position_scale_factor)
            )

        if cache is not None and cache.loaded:
            force_docs = cache.get_factor_forces(td)
        else:
            force_docs = await qs.get_factor_forces(td)
        if not force_docs:
            raise ValueError(f"无预计算 M4 数据: {td}")

        # 简化 M4+M5 打分逻辑：与实盘 apply_cooperative_force_and_score 对齐
        force_map = {d["code"]: d for d in force_docs}
        m4_threshold = self.config.get("cooperative_force", {}).get(
            "threshold_pct", 0.03
        )

        # M4 合力过滤：与 _apply_cooperative_force_filter 等价。
        # 通过条件 total_volume>0 AND main_flow>0 AND main_net_ratio>=threshold
        #   ⟺ fcoop1_main_net_ratio >= threshold（fcoop1 仅在 main_flow>0 时为正）
        filtered = []
        for c in all_candidates:
            fd = force_map.get(c["code"])
            if fd is None:
                continue
            fcoop1 = float(fd.get("fcoop1_main_net_ratio", 0.0) or 0.0)
            if fcoop1 < m4_threshold:
                continue
            merged = {
                "code": fd["code"],
                "sector_code": c.get("sector_code") or fd.get("sector_code", ""),
                "dragon_composite_score": c["dragon_composite_score"],
            }
            # 合并 fcoop 原始字段，供 _composite_force_scores 读取
            merged.update({k: v for k, v in fd.items() if k not in ("code", "sector_code")})
            filtered.append(merged)

        if not filtered:
            logger.warning(f"{td} M4 合力过滤后无候选")
            return self._empty_signals_df(
                td, market_risk_level=grade, position_scale_factor=float(position_scale_factor)
            )

        # ──────── M5 打分：合力因子复合 ────────
        # 风格检测已在 score_signals 顶部完成，直接使用 style_info/regime
        adjusted_entries = ForceFactors._adjust_weights_by_style(
            style_info, active_factors=self._get_active_factors(regime)
        )
        composite_scores = ForceFactors._composite_force_scores(filtered, adjusted_entries)

        m5_weights = self._cfg_final_weights()
        w_sector = m5_weights["sector"]
        w_dragon = m5_weights["dragon"]
        w_coop = m5_weights["cooperative"]
        total_w = max(w_sector + w_dragon + w_coop, 1e-8)
        sector_rank_map = ForceFactors._normalize_sector_ranks(top_sectors)

        ranked = []
        for c in filtered:
            code = c["code"]
            coop_score = composite_scores.get(code, 50.0)
            lhb_bonus = float(c.get("longhu_board_bonus", 0.0) or 0.0)
            coop_score = min(coop_score + lhb_bonus, 100.0)
            c["force_composite_score"] = coop_score
            c["longhu_board_bonus"] = lhb_bonus
            c["strategy_signal_score"] = (
                w_sector * sector_rank_map.get(c.get("sector_code", ""), 0.0)
                + w_dragon * c.get("dragon_composite_score", 0.0)
                + w_coop * coop_score
            ) / total_w
            ranked.append(c)

        ranked = sorted(ranked, key=lambda x: x.get("strategy_signal_score", 0), reverse=True)

        top_k_final = self._cfg_top_k(regime)
        # 多返回若干名供排名退出判定；组合层仍按 top_k 买入
        exit_universe_n = max(top_k_final * 4, 20)
        signals = ranked[:exit_universe_n]
        return self._ranked_to_signals_df(signals, td, grade, position_scale_factor, regime=regime)

    def _ranked_to_signals_df(
        self,
        ranked: List[Dict[str, Any]],
        trade_date: str,
        market_risk_level: str,
        position_scale_factor: float,
        regime: str = "neutral",
    ) -> pd.DataFrame:
        """将 M5 排序候选转为策略层统一 signals DataFrame（与 score_signals 同 schema）。"""
        td = trade_date
        grade = market_risk_level
        scale = float(position_scale_factor)
        top_k_final = self._cfg_top_k(regime)
        now_iso = datetime.now().astimezone().isoformat()
        rows = [
            {
                "trade_date": td,
                "code": sig.get("code"),
                "sector_code": sig.get("sector_code"),
                "strategy_signal_score": float(
                    sig.get("strategy_signal_score", sig.get("final_score", 0.0)) or 0.0
                ),
                "final_score": float(
                    sig.get("strategy_signal_score", sig.get("final_score", 0.0)) or 0.0
                ),
                "dragon_composite_score": float(
                    sig.get("dragon_composite_score", sig.get("dragon_score", 0.0)) or 0.0
                ),
                "dragon_score": float(
                    sig.get("dragon_composite_score", sig.get("dragon_score", 0.0)) or 0.0
                ),
                "force_composite_score": float(sig.get("force_composite_score", 0.0) or 0.0),
                "rank": i,
                "signal_type": "buy" if i <= top_k_final else "watch",
                "market_risk_level": grade,
                "market_grade": grade,
                "position_scale_factor": scale,
                "position_scale": scale,
                "created_at": now_iso,
            }
            for i, sig in enumerate(ranked, start=1)
        ]
        df = pd.DataFrame(rows)
        df.attrs["market_risk_level"] = grade
        df.attrs["market_grade"] = grade
        df.attrs["position_scale_factor"] = scale
        df.attrs["position_scale"] = scale
        df.attrs["regime"] = regime
        df.attrs["top_k"] = int(top_k_final)
        logger.info(
            f"✅ {td} 信号 DataFrame: buy={min(len(df), top_k_final)} "
            f"universe={len(df)} (grade={grade}, scale={scale:.2f})"
        )
        return df

    def _get_index_df(self, index_ohlcv: Dict[str, pd.DataFrame]) -> Optional[pd.DataFrame]:
        """【辅助方法】安全获取指数OHLCV数据

        逻辑：
        1. 优先使用字典中的第一个key
        2. 如果不存在则尝试 "399300"（沪深300）
        3. 如果仍不存在则返回 None
        """
        if not index_ohlcv:
            return None
        index_code = list(index_ohlcv.keys())[0]
        df = index_ohlcv.get(index_code) or index_ohlcv.get("399300")
        return df

    async def _apply_universe_filters(
        self,
        all_stocks: List[str],  # 全市场股票
        stock_ohlcv: Dict[str, pd.DataFrame],  # OHLCV 数据
        stock_infos: Optional[Dict[str, Dict]] = None,  # 股票信息
        sectors: Optional[List[Dict]] = None,  # 板块列表
    ) -> Tuple[Set[str], List[Dict], Set[str]]:
        """【辅助方法】统一的宇宙过滤逻辑

        职责：应用技术过滤 + 黑名单过滤

        Args:
            all_stocks: 全市场股票代码列表
            stock_ohlcv: 个股OHLCV数据
            stock_infos: 股票信息（ST判断）
            sectors: 板块列表

        Returns:
            (filtered_stocks_set, filtered_sectors, blacklisted_codes)
        """
        # 技术过滤 + 黑名单过滤
        tech_filtered = self.prefilters.apply_technical_filters(
            all_stocks, stock_ohlcv, stock_infos, apply_main_board=True, apply_bollinger=False
        )
        blacklist_result = self.prefilters.apply_blacklist_filters(tech_filtered, sectors=sectors)
        non_blacklisted_stocks = blacklist_result["stocks"]
        filtered_sectors = blacklist_result.get("sectors", sectors) if sectors else []
        blacklisted_codes = set(tech_filtered) - set(non_blacklisted_stocks)
        filtered_stocks_set = set(non_blacklisted_stocks)

        logger.info(f"🔍 板块过滤: {len(sectors or [])} → {len(filtered_sectors)}")
        logger.info(f"🔍 个股过滤: {len(all_stocks)} → {len(filtered_stocks_set)} 只")
        if blacklisted_codes:
            logger.debug(f"  黑名单排除: {len(blacklisted_codes)} 只")

        return filtered_stocks_set, filtered_sectors, blacklisted_codes

    def _compute_market_sector_ohlcv(
        self,
        sector_ohlcv: Optional[Dict[str, pd.DataFrame]],
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Optional[Dict[str, List[Dict]]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """【辅助方法】计算全市场板块OHLCV（用于F2.8排名计算）

        如果预聚合了sector_ohlcv，直接用；否则从所有股票聚合

        Args:
            sector_ohlcv: 预聚合的板块OHLCV（可选）
            sector_stocks: 板块包含的股票
            stock_ohlcv: 个股OHLCV数据
            stock_flow_recent: 个股最近资金流向数据

        Returns:
            全市场板块OHLCV DataFrame
        """
        if sector_ohlcv is not None:
            # 如果预聚合了 sector_ohlcv，直接用该数据作为全市场板块OHLCV
            return sector_ohlcv
        else:
            # 从所有 sectors 聚合全市场板块OHLCV
            market_sector_ohlcv, _ = SectorFactors._aggregate_sectors_from_stocks(
                sector_stocks,
                stock_ohlcv,
                stock_flow_recent or {},
                ohlcv_only=True,
            )
            return market_sector_ohlcv

    async def _compute_dragon_scores_for_sector(
        self,
        sector_code: str,
        sector_stock_list: List[str],
        stock_ohlcv: Dict[str, pd.DataFrame],
        blacklisted_codes: Optional[Set[str]] = None,
        trade_date: str = "",
        regime: str = "neutral",
    ) -> Tuple[Dict[str, float], Dict]:
        """【辅助方法】计算单个板块内的龙头因子分数

        Args:
            sector_code: 板块代码
            sector_stock_list: 该板块的股票列表
            stock_ohlcv: 个股OHLCV数据
            blacklisted_codes: 黑名单股票集合（用于过滤）
            trade_date: 交易日期

        Returns:
            (m3_scores, dragon_raw)：打分后的分数字典和原始因子字典
        """
        dragon_raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw(
            sector_stock_list,
            stock_ohlcv,
            trade_date=trade_date,
        )
        if not dragon_raw:
            return {}, {}

        m3_scores = DragonFactors.scores_from_raw(
            dragon_raw,
            regime=regime,
            active_factors=self._get_active_factors(regime),
            scoring_method=self.config.get("scoring_method", "linear"),
        )

        # 应用黑名单过滤
        if blacklisted_codes:
            m3_scores = {k: v for k, v in m3_scores.items() if k not in blacklisted_codes}

        return m3_scores, dragon_raw

    async def _apply_force_factors(
        self,
        all_candidates: List[Dict],
        top_sectors: List[Tuple[str, float]],
        stock_flow_recent: Optional[Dict[str, List[Dict]]] = None,
        stock_ohlcv: Optional[Dict[str, pd.DataFrame]] = None,
        trade_date: str = "",
    ) -> Tuple[List[Dict], List[Dict]]:
        """【辅助方法】应用合力因子（M4+M5）

        依次计算：M4 原始合力因子 → M5 合力评分

        Args:
            all_candidates: 候选股票列表
            top_sectors: Top 板块列表
            stock_flow_recent: 个股最近资金流向数据
            stock_ohlcv: 个股OHLCV数据
            trade_date: 交易日期

        Returns:
            (force_raw, ranked_candidates)
        """
        m4_threshold = self.config.get('cooperative_force', {}).get('threshold_pct', 0.03)
        weights = self._cfg_final_weights()

        # 风格检测
        style_info = await self._detect_style(trade_date)
        logger.info(
            f"🎨 市场风格: {style_info['regime']} | "
            f"momentum_weight={style_info['momentum_weight']:.2f} "
            f"reversal_weight={style_info['reversal_weight']:.2f}"
        )

        # M4：计算原始合力因子值（全部候选，不过滤）
        force_raw = ForceFactors.apply_cooperative_force_raw(
            all_candidates,
            stock_flow_recent=stock_flow_recent,
            stock_ohlcv=stock_ohlcv,
            trade_date=trade_date,
        )
        logger.info(f"✅ M4 原始合力因子收集完成: {len(force_raw)} 只候选")

        # M5：计算合力评分+最终合成（用于信号生成）
        ranked_candidates = ForceFactors.apply_cooperative_force_and_score(
            all_candidates,
            top_sectors,
            m4_threshold=m4_threshold,
            w_sector=weights['sector'],
            w_dragon=weights['dragon'],
            w_coop=weights['cooperative'],
            stock_flow_recent=stock_flow_recent,
            stock_ohlcv=stock_ohlcv,
            trade_date=trade_date,
            style_info=style_info,
            active_factors=self._get_active_factors(regime),
            force_raw=force_raw,
        )
        logger.info(f"✅ M4+M5 合力评分完成: {len(ranked_candidates)} 只候选")

        return force_raw, ranked_candidates

    @staticmethod
    def _empty_signals_df(
        trade_date: str,
        market_risk_level: str = "red",
        position_scale_factor: float = 0.0,
    ) -> pd.DataFrame:
        grade = market_risk_level
        scale = float(position_scale_factor)
        df = pd.DataFrame(
            columns=[
                "trade_date",
                "code",
                "sector_code",
                "strategy_signal_score",
                "final_score",
                "dragon_composite_score",
                "dragon_score",
                "force_composite_score",
                "rank",
                "signal_type",
                "market_risk_level",
                "market_grade",
                "position_scale_factor",
                "position_scale",
                "created_at",
            ]
        )
        df.attrs["market_risk_level"] = grade
        df.attrs["market_grade"] = grade
        df.attrs["position_scale_factor"] = scale
        df.attrs["position_scale"] = scale
        df.attrs["top_k"] = 0
        return df
