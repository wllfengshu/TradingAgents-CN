"""
截面日频策略管道（Pipeline）

职责：纯粹的流程编排，所有因子计算逻辑都委托给各自的因子类（高内聚，低耦合）

流程：M1市场情绪 → M2板块计算 → M3龙头计算 → M4+M5合力+最终合成 → M6最终选股

设计原则：
- 管道层只做流程编排和数据流转，不包含业务逻辑
- 所有因子计算、过滤规则都封装在对应的 Factor 类中
- 避免无意义的包装方法，直接调用底层 API
- 所有选择逻辑（排序、切片）都内联在主流程中
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .dragon_factors import DragonFactors
from .force_factors import ForceFactors
from .market_factors import MarketFactors
from .prefilters import PreFilters
from .sector_factors import SectorFactors

logger = logging.getLogger(__name__)


class CrossSectionStrategyPipeline:
    """
    截面日频策略管道

    职责：
    1. 加载和准备数据（load_real_data）
    2. 编排策略流程（run_pipeline）
    3. 从配置中读取参数（top_k 等）

    注意：所有计算逻辑都委托给 Factor 类，本类不包含业务规则
    """

    def __init__(self, config_path: str = None):
        """
        初始化管道

        Args:
            config_path: 策略配置文件路径，默认使用 common/config/strategy_params.json
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent / "common/config" / "strategy_params.json"
        self.config = self._load_config(str(config_path))
        config_dir = Path(config_path).parent if config_path else Path(__file__).parent.parent / "config"
        self.prefilters = PreFilters(config_dir=str(config_dir))
        logger.info("✅ 策略管道初始化完成")

    def _load_config(self, config_path: str) -> Dict:
        """加载策略配置（JSON 格式）"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            logger.info(f"✅ 加载配置文件: {config_path}")
            return config
        except Exception as e:
            logger.error(f"❌ 加载配置文件失败: {e}")
            raise

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

        # 2. 加载股票信息
        all_stock_docs, _ = await qs.get_all_stocks()
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

        # 3. 加载板块数据
        sector_list, _ = await qs.get_sector_list()
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

        # 4. 加载 OHLCV 数据
        stock_ohlcv = await qs.get_ohlcv_batch(all_stocks, start_date, td)
        logger.info(f"✅ [real-data] OHLCV 数据加载完成: {len(stock_ohlcv)} 只股票")

        # 5. 加载资金流数据（近 5 天，批量查询）
        stock_flow_recent = await qs.get_capital_flow_recent_days(all_stocks, end_date=td, days=5)
        logger.info(f"✅ [real-data] 资金流数据加载完成: {len(stock_flow_recent)} 只股票")

        # 6. 加载指数数据（沪深 300）
        index_ohlcv = await qs.get_ohlcv_batch(['399300'], start_date, td)
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
            index_ohlcv.get('399300'),
            index_name=index_name
        )
        logger.info(f"📊 M1 市场情绪评估完成: score={market_sentiment.get('score', 0):.2f}")

        # ===== M2: 板块筛选 =====
        # 2.1 黑名单过滤
        filtered_sectors = self.prefilters.filter_sectors(sectors)
        logger.info(f"🔍 M2.1 黑名单过滤: {len(sectors)} → {len(filtered_sectors)} 个板块")

        # 2.2 计算板块得分
        m2_scores = SectorFactors.calculate_all_sector_factors(
            filtered_sectors,
            sector_stocks,
            stock_ohlcv,
            stock_flow_recent
        )
        logger.info(f"📊 M2.2 板块得分计算完成: {len(m2_scores)} 个板块")

        # 2.3 选出 top K 板块（按得分降序排序后取前 top_k 个）
        top_k = self.config.get('top_sectors', 5)
        top_sectors = sorted(m2_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        logger.info(f"✅ M2.3 选出 Top {top_k} 板块: {[s[0] for s in top_sectors]}")

        # ===== M3: 龙头股筛选 =====
        # 3.1 主板过滤
        main_board_stocks = await self.prefilters.apply_main_board_filter(all_stocks, stock_infos)
        logger.info(f"🔍 M3.1 主板过滤: {len(all_stocks)} → {len(main_board_stocks)} 只股票")

        # 3.2 个股黑名单过滤
        non_blacklisted_stocks = self.prefilters.filter_stocks(main_board_stocks)
        blacklisted_codes = set(main_board_stocks) - set(non_blacklisted_stocks)
        filtered_stocks_set = set(non_blacklisted_stocks)
        logger.info(f"🔍 M3.2 个股黑名单过滤: {len(main_board_stocks)} → {len(non_blacklisted_stocks)} 只股票")

        # 3.3 计算龙头股得分并选出候选
        all_candidates = []
        top_per_sector = self.config.get('top_per_sector', 5)

        for sector_code, _ in top_sectors:
            sector_stock_list = [s for s in sector_stocks.get(sector_code, []) if s in filtered_stocks_set]
            if not sector_stock_list:
                continue

            # 计算板块内龙头股得分
            m3_scores = DragonFactors.calculate_all_dragon_factors_in_sector(
                sector_stock_list,
                stock_ohlcv
            )

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

        logger.info(f"✅ M3 龙头股筛选完成: {len(all_candidates)} 只候选")

        if not all_candidates:
            logger.error("⚠️ M3 筛选后无候选股票，流程终止")
            return []

        # ===== M4+M5: 合力评分 =====
        # 从配置读取参数
        m4_threshold = self.config.get('cooperative_force', {}).get('threshold_pct', 0.03)
        weights = self.config.get('final_score', {}).get('weights', {
            'sector': 0.3,
            'dragon': 0.4,
            'cooperative': 0.3
        })

        # 计算合力评分
        ranked_candidates = ForceFactors.apply_cooperative_force_and_score(
            all_candidates,
            top_sectors,
            m4_threshold=m4_threshold,
            w_sector=weights['sector'],
            w_dragon=weights['dragon'],
            w_coop=weights['cooperative'],
            stock_flow_recent=stock_flow_recent,
            stock_ohlcv=stock_ohlcv
        )
        logger.info(f"✅ M4+M5 合力评分完成: {len(ranked_candidates)} 只候选")

        if not ranked_candidates:
            logger.error("⚠️ M4+M5 筛选后无候选股票，流程终止")
            return []

        # ===== M6: 最终选股（按 final_score 降序排序后取前 top_k 个）=====
        top_k_final = self.config.get('top_k', 5)
        signals = ranked_candidates[:top_k_final]
        logger.info(f"✅ M6 最终选股完成: {len(signals)} 只股票")

        # 注入市场情绪信息
        for sig in signals:
            sig['market_grade'] = market_sentiment.get('grade', 'neutral')
            sig['position_scale'] = market_sentiment.get('position_scale', 1.0)

        logger.info(f"🎉 策略管道执行完成: 共选出 {len(signals)} 只股票")
        return signals
