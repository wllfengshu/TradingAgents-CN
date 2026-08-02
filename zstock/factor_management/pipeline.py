"""
截面日频策略管道（Pipeline）

职责：流程编排；因子公式委托给各自 Factor 类。

两条入口：
1. run_pipeline / compute_all_factor_raw —— 用 OHLCV+资金流现场计算
2. score_signals —— 从 MongoDB 预计算原始值打分（回测/网格搜索快路径）

流程：M1 → M2 → M3 → M4+M5 → M6
"""

from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .dragon_factors import DragonFactors
from .force_factors import ForceFactors
from .market_factors import MarketFactors
from .prefilters import PreFilters
from .sector_factors import SectorFactors

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = (
    Path(__file__).parent.parent / "common" / "config" / "strategy_params.json"
)


class CrossSectionStrategyPipeline:
    """
    截面日频策略管道

    职责：
    1. 加载数据（load_real_data）
    2. 现场计算编排（run_pipeline / compute_all_factor_raw）
    3. 预计算打分（score_signals）
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
        self._query_service = None
        logger.info("✅ 策略管道初始化完成")

    @property
    def query_service(self):
        if self._query_service is None:
            from zstock.data_management.query_service import get_data_query_service

            self._query_service = get_data_query_service()
        return self._query_service

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

    def _cfg_top_sectors(self) -> int:
        return int(
            self.config.get("sector_layer", {}).get("top_sectors")
            or self.config.get("top_sectors", 3)
        )

    def _cfg_top_per_sector(self) -> int:
        return int(
            self.config.get("dragon_layer", {}).get("top_per_sector")
            or self.config.get("top_per_sector", 3)
        )

    def _cfg_top_k(self) -> int:
        return int(
            self.config.get("final_score", {}).get("top_k")
            or self.config.get("top_k", 5)
        )

    def _cfg_final_weights(self) -> Dict[str, float]:
        default = {"sector": 0.4, "dragon": 0.35, "cooperative": 0.25}
        cfg = self.config.get("final_score", {}).get("weights") or {}
        return {**default, **cfg}

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

    # ===================== 全市场因子预计算 =====================

    async def compute_all_factor_raw(
        self,
        trade_date: str,
        all_stocks: List[str],
        stock_infos: Dict[str, Dict],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Dict[str, List[Dict]],
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        index_ohlcv: Dict[str, pd.DataFrame],
        index_name: str = "沪深 300",
        *,
        assume_sorted: bool = False,
        skip_scores: bool = True,
        filtered_sectors: Optional[List[Dict]] = None,
        filtered_stocks_set: Optional[set] = None,
        dragon_workers: int = 8,
        sector_ohlcv: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Dict[str, Any]:
        """
        全市场因子原始值计算（供预计算入库，不做 topK 选股裁剪）。

        与 run_pipeline_raw 的差异：
        - M2：全部非黑名单板块
        - M3：全部过滤后板块 × 主板非黑名单成分股
        - M4：上述全部股票（按 code 去重）

        Args:
            assume_sorted: OHLCV 已按 trade_date 升序时跳过重复排序
            skip_scores: 预计算入库不需要合成分时跳过 scores_from_raw
            filtered_sectors / filtered_stocks_set: 预计算可外置过滤结果，避免每日重算
            dragon_workers: 兼容旧参数（已忽略；个股特征全市场一次计算）
            sector_ohlcv: 可选预聚合板块 OHLCV，跳过每日 M2 聚合
        """
        from zstock.common.utils.common_utils import ensure_ohlcv_sorted

        logger.info(f"🚀 全市场因子预计算: trade_date={trade_date}")
        _ = dragon_workers

        if not assume_sorted:
            stock_ohlcv = {
                c: ensure_ohlcv_sorted(df) for c, df in (stock_ohlcv or {}).items()
            }
            index_ohlcv = {
                c: ensure_ohlcv_sorted(df) for c, df in (index_ohlcv or {}).items()
            }
            assume_sorted = True

        index_code = list(index_ohlcv.keys())[0] if index_ohlcv else ""
        idx_df = None
        if index_ohlcv:
            idx_df = index_ohlcv.get(index_code)
            if idx_df is None:
                idx_df = index_ohlcv.get("399300")

        market_sentiment = MarketFactors.calculate_market_sentiment(
            idx_df, index_name=index_name, trade_date=trade_date
        )
        market_raw = market_sentiment.get("detail", {})
        logger.info(
            f"📊 M1 完成: score={market_sentiment.get('market_score', 0):.2f}"
        )

        if filtered_sectors is None:
            filtered_sectors = self.prefilters.filter_sectors(sectors)
        logger.info(
            f"🔍 板块过滤: {len(sectors)} → {len(filtered_sectors)}"
        )

        # 先确定主板宇宙，供 M2 涨停/连板/资金流与 M3 共用
        if filtered_stocks_set is None:
            main_board_stocks = await self.prefilters.apply_main_board_filter(
                all_stocks, stock_infos
            )
            non_blacklisted_stocks = self.prefilters.filter_stocks(main_board_stocks)
            filtered_stocks_set = set(non_blacklisted_stocks)
        logger.info(
            f"🔍 个股过滤后可用: {len(filtered_stocks_set)} 只"
        )

        sector_raw = SectorFactors.calculate_all_sector_factors_raw(
            filtered_sectors,
            sector_stocks,
            stock_ohlcv,
            stock_flow_recent,
            assume_sorted=assume_sorted,
            sector_ohlcv=sector_ohlcv,
            trade_date=trade_date,
            eligible_codes=filtered_stocks_set,
        )
        m2_scores = (
            {} if skip_scores else SectorFactors.scores_from_raw(sector_raw)
        )
        logger.info(f"📊 M2 完成: {len(sector_raw.get('f21_rps', {}))} 个板块")

        sector_jobs = []
        all_m3_codes: set = set()
        for sector in filtered_sectors:
            sector_code = sector.get("sector_code")
            if not sector_code:
                continue
            sector_stock_list = [
                s
                for s in sector_stocks.get(sector_code, [])
                if s in filtered_stocks_set
            ]
            if sector_stock_list:
                sector_jobs.append((sector_code, sector_stock_list))
                all_m3_codes.update(sector_stock_list)

        # 个股特征全市场算一次；板块循环只做超额收益（相对中位数）
        m3_features = DragonFactors.precompute_stock_features(
            stock_ohlcv,
            sorted(all_m3_codes),
            assume_sorted=True,
            trade_date=trade_date,
        )

        dragon_raw_by_sector: Dict[str, Dict] = {}
        force_candidates: List[Dict] = []
        seen_force_codes: set = set()
        for sector_code, codes in sector_jobs:
            dragon_raw = DragonFactors.assemble_sector_raw_from_features(
                codes, m3_features
            )
            if not dragon_raw:
                continue
            dragon_raw_by_sector[sector_code] = dragon_raw
            for code in dragon_raw:
                if code in seen_force_codes:
                    continue
                seen_force_codes.add(code)
                force_candidates.append(
                    {
                        "code": code,
                        "sector_code": sector_code,
                        "dragon_score": 0.0,
                    }
                )

        logger.info(
            f"📊 M3 完成: {len(dragon_raw_by_sector)} 个板块, "
            f"{sum(len(v) for v in dragon_raw_by_sector.values())} 条 "
            f"(force 候选 {len(force_candidates)} 只去重, "
            f"features={len(all_m3_codes)})"
        )

        force_raw = ForceFactors.apply_cooperative_force_raw(
            force_candidates,
            stock_flow_recent=stock_flow_recent,
            stock_ohlcv=stock_ohlcv,
            assume_sorted=assume_sorted,
            trade_date=trade_date,
        )
        logger.info(f"📊 M4 完成: {len(force_raw)} 只")

        return {
            "trade_date": trade_date,
            "market_raw": market_raw,
            "index_code": index_code,
            "index_name": index_name,
            "sector_raw": sector_raw,
            "m2_scores": m2_scores,
            "top_sectors": [],
            "filtered_sectors": filtered_sectors,
            "m3_scores_by_sector": {},
            "dragon_raw_by_sector": dragon_raw_by_sector,
            "all_candidates": force_candidates,
            "force_raw": force_raw,
            "market_grade": market_sentiment.get("market_grade", "neutral"),
            "position_scale": market_sentiment.get("position_scale", 1.0),
            "market_score": market_sentiment.get("market_score", 0.0),
            "signals": [],
            "stock_infos": stock_infos,
        }

    # ===================== 主流程 =====================

    async def run_pipeline_raw(
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
    ) -> Dict[str, Any]:
        """
        执行完整的截面日频策略流程，同时收集各层原始因子值供预计算存储。

        与 run_pipeline() 流程完全一致（M1→M2→M3→M4→M5→M6），但额外调用
        各层 _raw() 方法收集归一化前的原始因子值，一并返回给上层存储到 MongoDB。

        Returns:
            包含以下字段的字典：
            - trade_date: 交易日期
            - market_raw: M1 市场情绪 detail dict（含 mf5_atr_ratio 等原始值）
            - index_code: 指数代码（如 '399300'）
            - index_name: 指数名称
            - sector_raw: M2 板块原始因子 dict
            - m2_scores: M2 板块综合得分（用于 top K 选择）
            - top_sectors: 选出的 top K 板块
            - filtered_sectors: 黑名单过滤后的板块列表
            - m3_scores_by_sector: 各板块 M3 龙头得分 dict
            - dragon_raw_by_sector: 各板块 M3 原始因子 dict
            - all_candidates: 带 dragon_score 的候选列表
            - force_raw: M4 合力原始因子列表
            - market_grade: 市场等级
            - position_scale: 仓位缩放系数
            - market_score: 市场综合得分
            - signals: 最终选出的股票列表（与 run_pipeline() 一致）
            - stock_infos: 股票信息字典（透传，供 stock_name 查找）
        """
        logger.info(f"🚀 开始执行策略管道(raw): trade_date={trade_date}")

        # ===== M1: 市场情绪评估 =====
        # 提取 index_code（index_ohlcv 的 key，如 '399300'）
        index_code = list(index_ohlcv.keys())[0] if index_ohlcv else ''

        market_sentiment = MarketFactors.calculate_market_sentiment(
            index_ohlcv.get(index_code)
            if index_ohlcv.get(index_code) is not None
            else index_ohlcv.get("399300"),
            index_name=index_name,
            trade_date=trade_date,
        )
        # market_raw = M1 的 detail dict（包含各子因子原始值）
        market_raw = market_sentiment.get('detail', {})
        logger.info(
            f"📊 M1 市场情绪评估完成: score={market_sentiment.get('market_score', 0):.2f} "
            f"grade={market_sentiment.get('market_grade')}"
        )

        if not market_sentiment.get("allow_new_open", True):
            logger.info(f"🔴 {trade_date} 市场红灯，管道终止（仍返回 M1 raw）")
            return {
                "trade_date": trade_date,
                "market_raw": market_raw,
                "index_code": index_code,
                "index_name": index_name,
                "sector_raw": {},
                "m2_scores": {},
                "top_sectors": [],
                "filtered_sectors": [],
                "m3_scores_by_sector": {},
                "dragon_raw_by_sector": {},
                "all_candidates": [],
                "force_raw": [],
                "market_grade": market_sentiment.get("market_grade", "red"),
                "position_scale": market_sentiment.get("position_scale", 0.0),
                "market_score": market_sentiment.get("market_score", 0.0),
                "signals": [],
                "stock_infos": stock_infos,
            }

        # ===== 宇宙过滤（M2 涨停类因子与 M3 共用主板宇宙）=====
        filtered_sectors = self.prefilters.filter_sectors(sectors)
        logger.info(f"🔍 M2.1 黑名单过滤: {len(sectors)} → {len(filtered_sectors)} 个板块")

        main_board_stocks = await self.prefilters.apply_main_board_filter(
            all_stocks, stock_infos
        )
        logger.info(
            f"🔍 M3.1 主板过滤: {len(all_stocks)} → {len(main_board_stocks)} 只股票"
        )
        non_blacklisted_stocks = self.prefilters.filter_stocks(main_board_stocks)
        blacklisted_codes = set(main_board_stocks) - set(non_blacklisted_stocks)
        filtered_stocks_set = set(non_blacklisted_stocks)
        logger.info(
            f"🔍 M3.2 个股黑名单过滤: {len(main_board_stocks)} → "
            f"{len(non_blacklisted_stocks)} 只股票"
        )

        # ===== M2: 板块筛选 =====
        sector_raw = SectorFactors.calculate_all_sector_factors_raw(
            filtered_sectors,
            sector_stocks,
            stock_ohlcv,
            stock_flow_recent,
            trade_date=trade_date,
            eligible_codes=filtered_stocks_set,
        )
        m2_scores = SectorFactors.scores_from_raw(sector_raw)
        logger.info(
            f"📊 M2.2 板块得分+原始值计算完成: {len(m2_scores)} 个板块, "
            f"{len(sector_raw)} 个板块raw"
        )

        top_k = self._cfg_top_sectors()
        top_sectors = sorted(m2_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
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

            dragon_raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw(
                sector_stock_list,
                stock_ohlcv,
                trade_date=trade_date,
            )
            m3_scores = DragonFactors.scores_from_raw(dragon_raw)

            m3_scores_by_sector[sector_code] = m3_scores
            dragon_raw_by_sector[sector_code] = dragon_raw

            # 选出板块内 top K 候选：先过滤黑名单，再按得分降序排序后取前 top_per_sector 个
            if blacklisted_codes:
                m3_scores = {k: v for k, v in m3_scores.items() if k not in blacklisted_codes}
            candidates = sorted(m3_scores.items(), key=lambda x: x[1], reverse=True)[:top_per_sector]

            for code, m3_score in candidates:
                all_candidates.append({
                    'code': code,
                    'sector_code': sector_code,
                    'dragon_score': m3_score,
                })

        logger.info(f"✅ M3 龙头股筛选完成: {len(all_candidates)} 只候选, {len(dragon_raw_by_sector)} 个板块raw")

        if not all_candidates:
            logger.warning("⚠️ M3 筛选后无候选股票，返回空结果")
            return {
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
                'force_raw': [],
                'market_grade': market_sentiment.get('market_grade', 'neutral'),
                'position_scale': market_sentiment.get('position_scale', 1.0),
                'market_score': market_sentiment.get('market_score', 0.0),
                'signals': [],
                'stock_infos': stock_infos,
            }

        # ===== M4+M5: 合力评分 =====
        m4_threshold = self.config.get('cooperative_force', {}).get('threshold_pct', 0.03)
        weights = self._cfg_final_weights()

        # 计算合力原始因子值（raw，全部候选，不过滤）
        force_raw = ForceFactors.apply_cooperative_force_raw(
            all_candidates,
            stock_flow_recent=stock_flow_recent,
            stock_ohlcv=stock_ohlcv,
            trade_date=trade_date,
        )
        logger.info(f"✅ M4 原始合力因子收集完成: {len(force_raw)} 只候选")

        # 计算合力评分+最终合成（scored，用于信号生成）
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
        )
        logger.info(f"✅ M4+M5 合力评分完成: {len(ranked_candidates)} 只候选")

        # ===== M6: 最终选股 =====
        signals = []
        if ranked_candidates:
            top_k_final = self._cfg_top_k()
            signals = ranked_candidates[:top_k_final]

            for sig in signals:
                sig['market_grade'] = market_sentiment.get('market_grade', 'neutral')
                sig['position_scale'] = market_sentiment.get('position_scale', 1.0)

        logger.info(f"🎉 策略管道(raw)执行完成: 共选出 {len(signals)} 只股票")

        return {
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
            'market_grade': market_sentiment.get('market_grade', 'neutral'),
            'position_scale': market_sentiment.get('position_scale', 1.0),
            'market_score': market_sentiment.get('market_score', 0.0),
            'signals': signals,
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
        """
        执行完整的截面日频策略流程

        流程步骤：
        1. M1 市场情绪评估（MarketFactors）
        2. M2 板块筛选（SectorFactors）
        3. M3 龙头股筛选（DragonFactors）
        4. M4+M5 合力评分（ForceFactors）
        5. M6 最终选股

        Args:
            trade_date: 交易日期
            all_stocks: 股票代码列表
            stock_infos: 股票信息字典
            stock_ohlcv: OHLCV 数据字典
            stock_flow_recent: 近期资金流字典
            sectors: 板块列表
            sector_stocks: 板块成分股字典
            index_ohlcv: 指数 OHLCV 数据
            index_name: 指数名称

        Returns:
            最终选出的股票列表，每个元素包含：
            - code: 股票代码
            - final_score: 最终得分
            - market_grade: 市场等级
            - position_scale: 仓位缩放比例
            - 其他中间结果字段
        """
        logger.info(f"🚀 开始执行策略管道: trade_date={trade_date}")

        # ===== M1: 市场情绪评估 =====
        market_sentiment = MarketFactors.calculate_market_sentiment(
            index_ohlcv.get("399300"),
            index_name=index_name,
            trade_date=trade_date,
        )
        logger.info(
            f"📊 M1 市场情绪评估完成: score={market_sentiment.get('market_score', 0):.2f} "
            f"grade={market_sentiment.get('market_grade')}"
        )
        if not market_sentiment.get("allow_new_open", True):
            logger.info(f"🔴 {trade_date} 市场红灯，禁止开新仓，管道终止")
            return []

        filtered_sectors = self.prefilters.filter_sectors(sectors)
        logger.info(f"🔍 M2.1 黑名单过滤: {len(sectors)} → {len(filtered_sectors)} 个板块")

        main_board_stocks = await self.prefilters.apply_main_board_filter(
            all_stocks, stock_infos
        )
        logger.info(
            f"🔍 M3.1 主板过滤: {len(all_stocks)} → {len(main_board_stocks)} 只股票"
        )
        non_blacklisted_stocks = self.prefilters.filter_stocks(main_board_stocks)
        blacklisted_codes = set(main_board_stocks) - set(non_blacklisted_stocks)
        filtered_stocks_set = set(non_blacklisted_stocks)
        logger.info(
            f"🔍 M3.2 个股黑名单过滤: {len(main_board_stocks)} → "
            f"{len(non_blacklisted_stocks)} 只股票"
        )

        m2_scores = SectorFactors.calculate_all_sector_factors(
            filtered_sectors,
            sector_stocks,
            stock_ohlcv,
            stock_flow_recent,
            trade_date=trade_date,
            eligible_codes=filtered_stocks_set,
        )
        logger.info(f"📊 M2.2 板块得分计算完成: {len(m2_scores)} 个板块")

        top_k = self._cfg_top_sectors()
        top_sectors = sorted(m2_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        logger.info(f"✅ M2.3 选出 Top {top_k} 板块: {[s[0] for s in top_sectors]}")

        all_candidates = []
        top_per_sector = self._cfg_top_per_sector()

        for sector_code, _ in top_sectors:
            sector_stock_list = [
                s for s in sector_stocks.get(sector_code, []) if s in filtered_stocks_set
            ]
            if not sector_stock_list:
                continue

            m3_scores = DragonFactors.calculate_all_dragon_factors_in_sector(
                sector_stock_list,
                stock_ohlcv,
                trade_date=trade_date,
            )

            if blacklisted_codes:
                m3_scores = {
                    k: v for k, v in m3_scores.items() if k not in blacklisted_codes
                }
            candidates = sorted(
                m3_scores.items(), key=lambda x: x[1], reverse=True
            )[:top_per_sector]

            for code, m3_score in candidates:
                all_candidates.append({
                    'code': code,
                    'sector_code': sector_code,
                    'dragon_score': m3_score,
                })

        logger.info(f"✅ M3 龙头股筛选完成: {len(all_candidates)} 只候选")

        if not all_candidates:
            logger.error("⚠️ M3 筛选后无候选股票，流程终止")
            return []

        # ===== M4+M5: 合力评分 =====
        m4_threshold = self.config.get('cooperative_force', {}).get('threshold_pct', 0.03)
        weights = self._cfg_final_weights()

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
        )
        logger.info(f"✅ M4+M5 合力评分完成: {len(ranked_candidates)} 只候选")

        if not ranked_candidates:
            logger.error("⚠️ M4+M5 筛选后无候选股票，流程终止")
            return []

        # ===== M6: 最终选股 =====
        top_k_final = self._cfg_top_k()
        signals = ranked_candidates[:top_k_final]
        logger.info(f"✅ M6 最终选股完成: {len(signals)} 只股票")

        for sig in signals:
            sig['market_grade'] = market_sentiment.get('market_grade', 'neutral')
            sig['position_scale'] = market_sentiment.get('position_scale', 1.0)

        logger.info(f"🎉 策略管道执行完成: 共选出 {len(signals)} 只股票")
        return signals

    # ===================== 预计算打分快路径 =====================

    async def score_signals(self, trade_date: str) -> pd.DataFrame:
        """
        从 MongoDB 读预计算原始值 → 用当前权重打分 → signals DataFrame。

        不加载 OHLCV/资金流；改权重/门槛/topK 无需重跑预计算。
        无数据时 raise ValueError。
        """
        td = trade_date
        qs = self.query_service

        market_doc = await qs.get_factor_market(td)
        if market_doc is None:
            raise ValueError(f"无预计算 M1 数据: {td}")

        sentiment = MarketFactors.score_from_raw(
            mf1_slope_pct=self._field(market_doc, "mf1_slope_pct"),
            mf2_boll_pct=self._field(market_doc, "mf2_boll_pct"),
            mf3_vol_ratio=self._field(market_doc, "mf3_vol_ratio"),
            mf4_momentum_5d=self._field(market_doc, "mf4_momentum_5d"),
            mf5_atr_ratio=self._field(market_doc, "mf5_atr_ratio"),
        )
        grade = sentiment["market_grade"]
        position_scale = sentiment["position_scale"]

        if grade == "red":
            logger.info(f"🔴 {td} 市场红灯，无信号")
            return self._empty_signals_df(td)

        sector_docs = await qs.get_factor_sectors(td)
        if not sector_docs:
            raise ValueError(f"无预计算 M2 数据: {td}")

        m2_scores = self._reconstruct_sector_scores(sector_docs)
        top_k_sectors = self._cfg_top_sectors()
        top_sectors = sorted(m2_scores.items(), key=lambda x: x[1], reverse=True)[
            :top_k_sectors
        ]

        all_stocks_docs, _ = await qs.get_all_stocks()
        all_stocks = [d["code"] for d in all_stocks_docs]
        stock_infos = {d["code"]: d for d in all_stocks_docs}

        main_board_stocks = await self.prefilters.apply_main_board_filter(
            all_stocks, stock_infos
        )
        non_blacklisted = self.prefilters.filter_stocks(main_board_stocks)
        blacklisted_set = set(main_board_stocks) - set(non_blacklisted)

        top_sector_codes = [sc for sc, _ in top_sectors]
        dragon_docs = await qs.get_factor_dragons(td, sector_codes=top_sector_codes)
        if not dragon_docs:
            logger.warning(f"{td} 无 M3 数据 for top sectors")
            return self._empty_signals_df(td)

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
            m3_scores = self._reconstruct_dragon_scores(sector_dragons)
            if blacklisted_set:
                m3_scores = {
                    k: v for k, v in m3_scores.items() if k not in blacklisted_set
                }
            if not m3_scores:
                continue
            candidates = sorted(
                m3_scores.items(), key=lambda x: x[1], reverse=True
            )[:top_per_sector]
            for code, m3_score in candidates:
                all_candidates.append(
                    {
                        "code": code,
                        "sector_code": sector_code,
                        "dragon_score": m3_score,
                    }
                )

        if not all_candidates:
            logger.warning(f"{td} 无 M3 候选")
            return self._empty_signals_df(td)

        force_docs = await qs.get_factor_forces(td)
        if not force_docs:
            raise ValueError(f"无预计算 M4 数据: {td}")

        ranked = self._reconstruct_force_scores(
            all_candidates, force_docs, top_sectors
        )
        if not ranked:
            logger.warning(f"{td} M4 合力过滤后无候选")
            return self._empty_signals_df(td)

        top_k_final = self._cfg_top_k()
        signals = ranked[:top_k_final]
        for sig in signals:
            sig["market_grade"] = grade
            sig["position_scale"] = position_scale

        now_iso = datetime.now().astimezone().isoformat()
        rows = [
            {
                "trade_date": td,
                "code": sig.get("code"),
                "sector_code": sig.get("sector_code"),
                "final_score": float(sig.get("final_score", 0.0)),
                "dragon_score": float(sig.get("dragon_score", 0.0)),
                "rank": i,
                "signal_type": "buy",
                "created_at": now_iso,
            }
            for i, sig in enumerate(signals, start=1)
        ]
        df = pd.DataFrame(rows)
        logger.info(f"✅ {td} 因子打分完成: {len(df)} 只信号")
        return df

    @staticmethod
    def _field(doc: Dict, *keys: str, default: float = float("nan")) -> float:
        for k in keys:
            if k in doc and doc[k] is not None:
                try:
                    return float(doc[k])
                except (TypeError, ValueError):
                    continue
        return default

    def _reconstruct_sector_scores(self, sector_docs: List[Dict]) -> Dict[str, float]:
        f21_raw = {
            d["sector_code"]: self._field(d, "f21_rps_20d", "f21_rps")
            for d in sector_docs
        }
        f22_raw = {
            d["sector_code"]: self._field(d, "f22_main_flow", default=0.0)
            for d in sector_docs
        }
        f23_raw = {
            d["sector_code"]: self._field(d, "f23_limit_up_density", default=0.0)
            for d in sector_docs
        }
        f24_raw = {
            d["sector_code"]: self._field(d, "f24_max_consecutive", default=0.0)
            for d in sector_docs
        }
        f25_raw = {
            d["sector_code"]: self._field(
                d, "f25_volume_slope_5d", "f25_volume_slope"
            )
            for d in sector_docs
        }
        return SectorFactors.scores_from_raw(
            {
                "f21_rps": f21_raw,
                "f22_main_flow": f22_raw,
                "f23_limit_up_density": f23_raw,
                "f24_max_consecutive": f24_raw,
                "f25_volume_slope": f25_raw,
            }
        )

    def _reconstruct_dragon_scores(
        self, sector_dragons: List[Dict]
    ) -> Dict[str, float]:
        f31_raw = {
            d["code"]: self._field(d, "f31_excess_return_5d", "f31_excess_return")
            for d in sector_dragons
        }
        f32_raw = {d["code"]: self._field(d, "f32_amount") for d in sector_dragons}
        f33_raw = {
            d["code"]: self._field(d, "f33_consecutive_boards", default=0.0)
            for d in sector_dragons
        }
        f34_raw = {
            d["code"]: self._field(d, "f34_resonance_pct_5d", "f34_resonance_pct")
            for d in sector_dragons
        }
        f35_raw = {
            d["code"]: self._field(d, "f35_bollinger_trend") for d in sector_dragons
        }
        raw = {
            code: {
                "f31_excess_return": f31_raw.get(code, float("nan")),
                "f32_amount": f32_raw.get(code, float("nan")),
                "f33_consecutive_boards": f33_raw.get(code, 0.0),
                "f34_resonance_pct": f34_raw.get(code, float("nan")),
                "f35_bollinger_trend": f35_raw.get(code, float("nan")),
            }
            for code in (
                set(f31_raw) | set(f32_raw) | set(f33_raw) | set(f34_raw) | set(f35_raw)
            )
        }
        return DragonFactors.scores_from_raw(raw)

    def _reconstruct_force_scores(
        self,
        all_candidates: List[Dict],
        force_docs: List[Dict],
        top_sectors: List[Tuple[str, float]],
    ) -> List[Dict]:
        force_map = {d["code"]: d for d in force_docs}
        m4_threshold = self.config.get("cooperative_force", {}).get(
            "threshold_pct", 0.03
        )
        filtered = []
        for c in all_candidates:
            fd = force_map.get(c["code"])
            if fd is None:
                continue
            main_net_ratio = self._field(fd, "fcoop1_main_net_ratio", default=0.0)
            # 与实时路径一致：主力净流入>0 且 净值比>=门槛
            # （raw 路径在 main_flow<=0 时将 fcoop1 置 0）
            if main_net_ratio <= 0 or main_net_ratio < m4_threshold:
                continue
            filtered.append(
                {
                    "code": fd["code"],
                    # 优先用选股上下文板块（多板块归属时 force 文档可能是首次遍历到的板块）
                    "sector_code": c.get("sector_code") or fd.get("sector_code", ""),
                    "dragon_score": c["dragon_score"],
                    "fcoop1_main_net_ratio": main_net_ratio,
                    "fcoop2_main_retail_ratio": self._field(
                        fd, "fcoop2_main_retail_ratio", default=0.0
                    ),
                    "fcoop3_sustained_days": self._field(
                        fd,
                        "fcoop3_sustained_days_5d",
                        "fcoop3_sustained_days",
                        default=0.0,
                    ),
                    "fcoop4_turnover_quality": self._field(
                        fd, "fcoop4_turnover_quality", default=0.0
                    ),
                }
            )

        if not filtered:
            return []

        coop1_norm = ForceFactors._minmax_normalize(
            {c["code"]: c["fcoop1_main_net_ratio"] for c in filtered}
        )
        coop2_norm = ForceFactors._minmax_normalize(
            {c["code"]: c["fcoop2_main_retail_ratio"] for c in filtered}
        )
        coop3_norm = ForceFactors._minmax_normalize(
            {c["code"]: c["fcoop3_sustained_days"] for c in filtered}
        )
        from zstock.factor_management.force_factors import (
            _INVERT_COOP4_FOR_SCORE,
            _W_COOP1,
            _W_COOP2,
            _W_COOP3,
            _W_COOP4,
        )

        coop4_raw = {c["code"]: c["fcoop4_turnover_quality"] for c in filtered}
        if _INVERT_COOP4_FOR_SCORE:
            coop4_raw = {k: -float(v) for k, v in coop4_raw.items()}
        coop4_norm = ForceFactors._minmax_normalize(coop4_raw)

        w_coop1, w_coop2, w_coop3, w_coop4 = _W_COOP1, _W_COOP2, _W_COOP3, _W_COOP4
        m5_weights = self._cfg_final_weights()
        w_sector = m5_weights.get("sector", 0.4)
        w_dragon = m5_weights.get("dragon", 0.35)
        w_coop = m5_weights.get("cooperative", 0.25)
        total_w = max(w_sector + w_dragon + w_coop, 1e-8)
        sector_rank_map = ForceFactors._normalize_sector_ranks(top_sectors)

        for c in filtered:
            code = c["code"]
            coop_score = (
                w_coop1 * coop1_norm.get(code, 50.0)
                + w_coop2 * coop2_norm.get(code, 50.0)
                + w_coop3 * coop3_norm.get(code, 50.0)
                + w_coop4 * coop4_norm.get(code, 50.0)
            )
            c["coop_score"] = coop_score
            c["final_score"] = (
                w_sector * sector_rank_map.get(c.get("sector_code", ""), 0.0)
                + w_dragon * c.get("dragon_score", 0.0)
                + w_coop * coop_score
            ) / total_w

        return sorted(filtered, key=lambda x: x.get("final_score", 0), reverse=True)

    @staticmethod
    def _empty_signals_df(trade_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "code",
                "sector_code",
                "final_score",
                "dragon_score",
                "rank",
                "signal_type",
                "created_at",
            ]
        )
