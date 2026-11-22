"""
策略管道（StrategyPipeline）—— 情绪+选股+账户层编排

领域实现：
  sentiment.py   红/黄/绿
  market_book.py 红黄绿账户

流程：取数 → 情绪 → 风格 → 板块 → 龙头 → 合力合成 → 信号表
      → Book(红黄绿) → 组合 → 风控 → 换手

因子公式在 factor_management；本包只消费 raw。
"""

from __future__ import annotations

import logging
import asyncio
import copy
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from zstock.common.prefilters import PreFilters
from zstock.common.config.strategy_config import (
    _deep_merge_dicts,
    build_risk_limits,
    build_runtime_config,
    get_neutral_style,
    load_strategy_params,
    resolve_active_factors,
    resolve_runtime_config,
)
from zstock.common.utils.common_utils import (
    get_index_ohlcv_df,
    normalize_date,
)
from zstock.data_management.query_service import get_data_query_service
from zstock.strategy_management.market_book import (
    MIN_UTILIZATION,
    REBALANCE_BAND,
    BookDecision,
    MarketBook,
    book_enabled,
    book_result,
    holdings_frame,
    needs_book_trade,
    weight_map,
)
from zstock.strategy_management.strategy_section.execute import (
    extract_market_grade,
    run_equity_execution,
)
from zstock.strategy_management.strategy_section.funnel import SignalFunnelMixin
from zstock.strategy_management.strategy_section.optimizer import PortfolioOptimizer
from zstock.strategy_management.strategy_section.selection import resolve_force_gate_settings
from zstock.strategy_management.strategy_section import schema as signal_schema
from zstock.strategy_management.strategy_section.risk import RiskManager
from .turnover_controller import TurnoverController
from zstock.strategy_management.sentiment import (
    detect_style,
    score_market_from_doc,
    score_market_sentiment,
)

logger = logging.getLogger(__name__)


def _state_for_grade(grade: str) -> str:
    return {"green": "attack", "yellow": "neutral", "red": "defense"}.get(
        str(grade or "").lower(), "neutral"
    )


class StrategyPipeline(SignalFunnelMixin):
    """
    编排：情绪+选股 → Book(红黄绿账户) → 组合 → 风控 → 换手。

    因子定义在 factor_management；本类负责「怎么用因子」产出买卖信号并落仓。
    """

    def __init__(
        self,
        config_path: str = None,
        config_override: Optional[Dict] = None,
        portfolio_optimizer: Optional[PortfolioOptimizer] = None,
        risk_manager: Optional[RiskManager] = None,
        turnover_controller: Optional[TurnoverController] = None,
        signals: Optional[Any] = None,
    ):
        """
        Args:
            config_path: 备用策略参数文件；None 则用 strategy_config 生产 SSOT
            config_override: 覆盖配置（改权重/门槛/topK，供网格搜索）
            portfolio_optimizer / risk_manager / turnover_controller: 组合层组件
            signals: 测试用 duck-typed 对象（仅需 score_signals/compute_signals）；
                若设置，_generate_signals 委托给它，否则用 self.compute_signals
        """
        self._signals = signals
        if config_path:
            self.config = load_strategy_params(config_path)
        else:
            self.config = copy.deepcopy(load_strategy_params())
        if config_override:
            self._apply_config_override(config_override)
        self._sync_runtime()
        if signals is None:
            self.prefilters = PreFilters()
            self._query_service = get_data_query_service()
        else:
            # duck-typed signals：跳过 Mongo/query 初始化，供离线测试
            self.prefilters = None
            self._query_service = None
        self._all_stocks_cache: Optional[List[Dict[str, Any]]] = None
        self._all_stocks_cache_asof: Optional[str] = None
        self._sectors_cache: Optional[List[Dict[str, Any]]] = None
        self._sectors_cache_asof: Optional[str] = None
        self._style_cache: Dict[str, Dict[str, object]] = {}
        self._sentiment_cache: Dict[str, Dict[str, Any]] = {}
        self._portfolio_optimizer = portfolio_optimizer or PortfolioOptimizer()
        if risk_manager is None:
            self._risk_manager = RiskManager(build_risk_limits(self.config))
            self._risk_manager_from_config = True
        else:
            self._risk_manager = risk_manager
            self._risk_manager_from_config = False
        if turnover_controller is None:
            tov = self.runtime.get("turnover_control", {})
            turnover_controller = TurnoverController(
                buffer_threshold=float(tov.get("buffer_threshold", 0.25)),
                min_hold_days=int(tov.get("min_hold_days", 3)),
                fee_rate=float(tov.get("fee_rate", 0.0015)),
                stamp_tax=float(tov.get("stamp_tax", 0.0005)),
                fill_open_slots=bool(tov.get("fill_open_slots", True)),
                fee_free_codes=tov.get("fee_free_codes"),
            )
        self._turnover_controller = turnover_controller
        self._pipeline_result: Dict[str, Any] = {}
        book_cfg = self.runtime.get("book_regime") or {}
        self.book = MarketBook(
            confirm_green_days=int(book_cfg.get("confirm_green_days", 2)),
            params=self.config,
        )
        self._dragon_field_cache: Dict[str, Dict[str, Dict[str, float]]] = {}
        logger.info("✅ StrategyPipeline 初始化完成")

    # ----- 配置读取（编排旋钮，不下沉）-----
    # 分层查找 / by_regime 是 pipeline 取「这一层取几个、怎么加权」的入口。
    # 不放进 strategy_config：那边是 JSON→派生结构，这边是当日编排读数。

    def _sync_runtime(self) -> None:
        """self.config（原始 JSON）→ self.runtime（执行态）；信号与执行同源。"""
        self.runtime = build_runtime_config(self.config if self.config else None)

    def _apply_config_override(self, override: Dict) -> None:
        """网格搜索用：与 resolve_runtime_config 一致的深合并。"""
        self.config = _deep_merge_dicts(self.config or {}, override)
        self._sync_runtime()
        # __init__ 里 config_override 早于组件创建，需防御性判断
        if getattr(self, "_risk_manager_from_config", False) and hasattr(
            self, "_risk_manager"
        ):
            self._risk_manager.risk_limits = dict(build_risk_limits(self.config))
        tc = getattr(self, "_turnover_controller", None)
        if tc is not None:
            tov = self.runtime.get("turnover_control") or {}
            if "stamp_tax" in tov:
                tc.stamp_tax = float(tov["stamp_tax"])
            if "fee_rate" in tov:
                tc.fee_rate = float(tov["fee_rate"])
            if "fee_free_codes" in tov:
                tc.fee_free_codes = {
                    str(c).zfill(6) if str(c).isdigit() else str(c)
                    for c in (tov.get("fee_free_codes") or [])
                }

    def _cfg_from_layered_config(self, layer_key: str, config_key: str, default: Any) -> Any:
        """统一的分层配置查询（先查分层，再查全局）。使用 None 检查避免 falsy 值被忽略。"""
        val = self.config.get(layer_key, {}).get(config_key)
        if val is not None:
            return val
        return self.config.get(config_key, default)

    def _cfg_top_sectors(self) -> int:
        return int(self._cfg_from_layered_config("sector_layer", "top_sectors", 4))

    def _cfg_top_per_sector(self) -> int:
        return int(self._cfg_from_layered_config("dragon_layer", "top_per_sector", 2))

    def _universe_mode(self) -> str:
        """full=全市场；prefilter=准入过滤；funnel=策略漏斗（默认）。"""
        mode = str(self.config.get("universe_mode") or "funnel").strip().lower()
        if mode not in ("full", "prefilter", "funnel"):
            logger.warning("未知 universe_mode=%r，回退 funnel", mode)
            return "funnel"
        return mode

    def _cfg_top_k(self, regime: str = "neutral") -> int:
        fs = self.config.get("final_score") or {}
        by_regime = fs.get("by_regime") or {}
        sub = by_regime.get(regime) or by_regime.get(str(regime))
        if isinstance(sub, dict) and sub.get("top_k") is not None:
            return int(sub["top_k"])
        return int(fs.get("top_k", 5))

    def _exit_universe_n(self, regime: str = "neutral") -> int:
        """排名退出用的观察池大小；组合层仍按 top_k 买入。"""
        return max(self._cfg_top_k(regime) * 4, 20)

    def _cfg_final_weights(self) -> Dict[str, float]:
        default = {"sector": 0.4, "dragon": 0.35, "cooperative": 0.25}
        cfg = self._cfg_from_layered_config("final_score", "weights", {}) or {}
        return {**default, **cfg}

    def _cfg_layer_gates(self, layer_key: str) -> Optional[List[Dict[str, Any]]]:
        """sector_layer / dragon_layer 的 gates；缺省键则返回 None（默认门禁回调）。"""
        layer = self.config.get(layer_key) or {}
        if "gates" not in layer:
            return None
        gates = layer.get("gates")
        return list(gates) if gates is not None else []

    def _market_selection(self) -> Dict[str, Any]:
        return self.config.get("market_selection") or {}

    def _factor_stops_on_red(self) -> bool:
        ms = self._market_selection()
        if "stops_on_red" in ms:
            return bool(ms["stops_on_red"])
        return True

    def _allow_new_open_in_signals(self, grade: str) -> bool:
        if str(grade).lower() != "red":
            return True
        ms = self._market_selection()
        if ms.get("strategy_blocks_new_on_red", True):
            return False
        return not self._factor_stops_on_red()

    def _output_position_scale(
        self, grade: str, m1_scale: float, regime: str = "neutral"
    ) -> float:
        """因子层仓位系数。

        策略层还有 target_equity 与红灯强制 0。此处在
        position_scale_applied_in=strategy 时输出 1.0，把仓位交给组合层。
        """
        ms = self._market_selection()
        if str(ms.get("position_scale_applied_in", "factor")).strip().lower() == "strategy":
            return 1.0
        return self._resolve_position_scale(grade, m1_scale, regime)

    def _resolve_m4_gate_settings(
        self,
        coop_cfg: Dict[str, Any],
        regime: str,
        grade: str,
    ) -> Tuple[float, str, int]:
        return resolve_force_gate_settings(coop_cfg, regime, grade)

    def _resolve_position_scale(
        self, grade: str, scale: float, regime: str = "neutral"
    ) -> float:
        """market_overlay 覆盖 M1 默认黄/绿灯仓位；支持 by_regime。"""
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

    def _get_active_factors(
        self, regime: str = "neutral", grade: Optional[str] = None
    ) -> Dict[str, Any]:
        return resolve_active_factors(self.config, regime, grade)

    async def _get_all_stocks_cached(
        self, as_of: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        key = normalize_date(as_of) if as_of else "_default"
        if self._all_stocks_cache is None or self._all_stocks_cache_asof != key:
            docs, _ = await self._query_service.get_all_stocks(as_of=as_of)
            self._all_stocks_cache = docs
            self._all_stocks_cache_asof = key
        return self._all_stocks_cache

    async def _get_sectors_cached(
        self, as_of: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        key = normalize_date(as_of) if as_of else "_default"
        if self._sectors_cache is None or self._sectors_cache_asof != key:
            # 库里的板块成分是当前快照，没有历史时点。as_of 只做缓存键，不能当成当日成分。
            if as_of and not getattr(self, "_sector_pit_warned", False):
                logger.warning(
                    "板块成分没有历史时点，%s 使用的是当前申万名单", as_of
                )
                self._sector_pit_warned = True
            sector_list, _ = await self._query_service.get_sector_list()
            self._sectors_cache = sector_list or []
            self._sectors_cache_asof = key
        return self._sectors_cache

    async def market_sentiment(
        self,
        trade_date: str,
        *,
        index_df: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """当日 M1 信号灯，只算一次。账户状态和选股漏斗都读这份结果。"""
        td = normalize_date(trade_date)
        cached = self._sentiment_cache.get(td)
        if cached is not None:
            return cached
        if index_df is not None:
            sentiment = score_market_sentiment(
                index_df,
                index_name="沪深300",
                trade_date=td,
                params=self.config,
            )
        else:
            market_doc = None
            try:
                market_doc = await self._load_factor_docs("get_factor_market", td)
            except Exception as exc:
                logger.warning("M1 文档读取失败 %s: %s", td, exc)
            if not market_doc:
                from zstock.strategy_management.sentiment import _neutral_market_result

                sentiment = _neutral_market_result(
                    "沪深300",
                    reason="no_market_doc",
                    params=self.config,
                    data_missing=True,
                )
            else:
                sentiment = score_market_from_doc(market_doc, params=self.config)
        self._sentiment_cache[td] = sentiment
        return sentiment

    async def note_market_day(
        self,
        trade_date: str,
        *,
        index_closes: Optional[List[float]] = None,
        asset_closes: Optional[Dict[str, List[float]]] = None,
        held: Optional[List[str]] = None,
        nav: Optional[float] = None,
        confirm_green_days: Optional[int] = None,
        index_df: Optional[pd.DataFrame] = None,
    ) -> BookDecision:
        """当日只走这一处：M1 灯 → 账户状态。回测和执行层都调用它。"""
        td = normalize_date(trade_date)
        sentiment = await self.market_sentiment(td, index_df=index_df)
        grade = str(sentiment.get("market_risk_level") or "yellow").lower()
        if grade not in ("red", "yellow", "green"):
            grade = "yellow"
        book_cfg = self.config.get("book_regime") or {}
        self.book.prepare_for_date(td)
        style = await self._detect_style(td, index_df)
        regime = str(style.get("regime") or "neutral")
        return self.book.step(
            trade_date=td,
            grade=grade,
            index_closes=index_closes,
            nav=nav,
            closes=asset_closes,
            held=held,
            confirm_green_days=(
                int(book_cfg.get("confirm_green_days", self.book.confirm_green_days))
                if confirm_green_days is None
                else int(confirm_green_days)
            ),
            params=self.config,
            style_regime=regime,
        )

    async def _load_factor_docs(self, method: str, trade_date: str, **kwargs):
        """按日从 query_service 读取已落库因子（生产在 factor_management 预计算脚本）。"""
        return await getattr(self._query_service, method)(trade_date, **kwargs)

    async def _detect_style(
        self,
        trade_date: str,
        index_ohlcv: Optional[pd.DataFrame] = None,
    ) -> Dict[str, object]:
        """编排：取指数行情 → sentiment.detect_style。"""
        if not self.config.get("style_switching", True):
            return get_neutral_style()
        if trade_date in self._style_cache:
            return self._style_cache[trade_date]
        try:
            df = index_ohlcv
            if df is None:
                end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
                start_date = (end_dt - timedelta(days=120)).strftime("%Y-%m-%d")
                df, _ = await self._query_service.get_ohlcv(
                    "399300", start_date, trade_date, period="daily"
                )
            result = detect_style(df, trade_date, params=self.config)
        except Exception as e:
            logger.warning(f"风格检测失败，使用中性默认: {e}")
            result = get_neutral_style()
        self._style_cache[trade_date] = result
        return result

    def _remember_dragon_fields(
        self, trade_date: str, by_code: Dict[str, Dict[str, float]]
    ) -> None:
        """漏斗写入当日龙头字段缓存，供回测趋势退出同步读取。"""
        import math

        td = normalize_date(trade_date)
        bucket = self._dragon_field_cache.setdefault(td, {})
        for code, fields in by_code.items():
            cur = bucket.setdefault(str(code), {})
            for k, v in fields.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(fv):
                    cur[str(k)] = fv

    def dragon_field_by_code(
        self,
        trade_date: str,
        field: str,
        codes: Optional[Set[str]] = None,
    ) -> Dict[str, float]:
        """同步读取漏斗缓存的龙头字段（如 f35）；无缓存则空 dict。"""
        td = normalize_date(trade_date)
        bucket = self._dragon_field_cache.get(td) or {}
        out: Dict[str, float] = {}
        wanted = {str(c) for c in codes} if codes is not None else None
        for code, fields in bucket.items():
            if wanted is not None and code not in wanted:
                continue
            if field in fields:
                out[code] = float(fields[field])
        return out

    # ----- M0 现场取数（编排 query_service，因子类禁止查库）-----

    async def load_real_data(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 60,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        从数据库加载真实数据，返回 score_signals_live 所需的入参字典

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
        qs = self._query_service or get_data_query_service()

        # 1. 确定交易日期
        td = trade_date or datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.strptime(td, '%Y-%m-%d') - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        logger.info(f"📥 [real-data] 开始加载数据: trade_date={td}, lookback={lookback_days}天 ({start_date} ~ {td})")

        # 2. 并发加载基础元数据（互不依赖）；股票池按 as_of 做 PIT
        all_stock_result, sector_result = await asyncio.gather(
            qs.get_all_stocks(as_of=td),
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

    def _result_to_signals_df(self, result: Dict[str, Any]) -> pd.DataFrame:
        regime = str(result.get("regime", "neutral"))
        grade = str(result.get("market_risk_level", "unknown"))
        return signal_schema.result_to_signals_df(
            result,
            exit_universe_n=self._exit_universe_n(regime),
            top_k=self._cfg_top_k(regime),
            allow_new_open=self._allow_new_open_in_signals(grade),
        )

    async def _run_funnel_live(self, data: Dict[str, Any], trade_date: str) -> Dict[str, Any]:
        idx_df = get_index_ohlcv_df(data.get("index_ohlcv") or {})
        sentiment = await self.market_sentiment(trade_date, index_df=idx_df)
        return await self._run_funnel(
            trade_date,
            sentiment,
            source="live",
            index_df=idx_df,
            all_stocks=data["all_stocks"],
            stock_infos=data["stock_infos"],
            sectors=data["sectors"],
            stock_ohlcv=data.get("stock_ohlcv"),
            sector_stocks=data.get("sector_stocks"),
            stock_flow_recent=data.get("stock_flow_recent"),
        )

    async def compute_signals(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 60,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
        prebuilt_data: Optional[Dict[str, Any]] = None,
        prefer_precomputed: bool = True,
    ) -> pd.DataFrame:
        """对外推荐入口：预计算优先，失败则现场。两条路径都走 _run_funnel。"""
        td = trade_date or datetime.now().strftime("%Y-%m-%d")
        if prebuilt_data is None and prefer_precomputed:
            try:
                logger.info(f"🔍 {td} 尝试预计算信号（Mongo 快速路径）")
                return await self.score_signals(td)
            except ValueError as e:
                logger.info(f"预计算不可用，降级到实时计算: {e}")
        logger.info(f"📊 {td} 实时计算信号（M0~M5 完整流程）")
        return await self.score_signals_live(
            trade_date=td,
            lookback_days=lookback_days,
            sectors=sectors,
            max_stocks=max_stocks,
            prebuilt_data=prebuilt_data,
        )

    async def score_signals_live(
        self,
        trade_date: str,
        lookback_days: int = 60,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
        prebuilt_data: Optional[Dict[str, Any]] = None,
    ) -> pd.DataFrame:
        """现场路径：M0 OHLCV → _run_funnel(source=live) → signals DataFrame。"""
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
        result = await self._run_funnel_live(data, data.get("trade_date", td))
        return self._result_to_signals_df(result)

    async def score_signals(self, trade_date: str) -> pd.DataFrame:
        """预计算路径：Mongo raw → _run_funnel(source=precomputed) → signals DataFrame。"""
        result = await self._run_funnel_precomputed(trade_date)
        return self._result_to_signals_df(result)

    async def _run_funnel_precomputed(self, trade_date: str) -> Dict[str, Any]:
        """预计算路径漏斗结果（含 ranked_all / funnel）。"""
        td = trade_date
        sentiment = await self.market_sentiment(td)
        if sentiment.get("data_missing"):
            raise ValueError(f"无预计算 M1 数据: {td}")
        if str(sentiment.get("market_risk_level")) == "red" and self._factor_stops_on_red():
            return await self._run_funnel(
                td, sentiment, source="precomputed",
                all_stocks=[], stock_infos={}, sectors=[],
            )
        all_stocks_docs = await self._get_all_stocks_cached(as_of=td)
        return await self._run_funnel(
            td,
            sentiment,
            source="precomputed",
            all_stocks=[d["code"] for d in all_stocks_docs],
            stock_infos={d["code"]: d for d in all_stocks_docs},
            sectors=await self._get_sectors_cached(as_of=td),
        )

    async def m3_universe_codes(self, trade_date: str) -> Set[str]:
        """策略漏斗按日宇宙：M3 候选代码（用于因子测评注入）。"""
        prev = self.config.get("universe_mode")
        self.config["universe_mode"] = "funnel"
        self._sync_runtime()
        try:
            result = await self._run_funnel_precomputed(trade_date)
        finally:
            if prev is None:
                self.config.pop("universe_mode", None)
            else:
                self.config["universe_mode"] = prev
            self._sync_runtime()
        codes = result.get("m3_codes") or set()
        return {str(c) for c in codes if c}

    # ===================== 账户 / 组合 / 风控 / 换手 =====================

    async def _generate_signals(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 60,
        sectors: Optional[list] = None,
        max_stocks: Optional[int] = None,
        prebuilt_data: Optional[Dict] = None,
        prefer_precomputed: bool = True,
    ) -> pd.DataFrame:
        td = trade_date or datetime.now().strftime("%Y-%m-%d")
        logger.info(f"🎯 生成截面信号: trade_date={td}")
        compute = (
            self._signals.compute_signals
            if self._signals is not None
            else self.compute_signals
        )
        df = await compute(
            trade_date=td,
            lookback_days=lookback_days,
            sectors=sectors,
            max_stocks=max_stocks,
            prebuilt_data=prebuilt_data,
            prefer_precomputed=prefer_precomputed,
        )
        logger.info(
            f"✅ 信号生成完成: universe={len(df)} "
            f"grade={df.attrs.get('market_grade', '?')}"
        )
        return df

    async def execute_full_pipeline(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 60,
        sectors: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
        current_positions: Optional[pd.DataFrame] = None,
        total_capital: float = 1e7,
        config: Optional[Dict] = None,
        prebuilt_data: Optional[Dict] = None,
        precomputed_signals: Optional[pd.DataFrame] = None,
        ohlcv_by_code: Optional[Dict[str, pd.DataFrame]] = None,
        prev_f35_by_code: Optional[Dict[str, float]] = None,
        index_closes: Optional[List[float]] = None,
        asset_closes: Optional[Dict[str, List[float]]] = None,
        index_bars: Optional[List[dict]] = None,
        respect_book_rebalance_band: bool = False,
        book_decision: Optional[BookDecision] = None,
        industry_weights: Optional[Dict[str, float]] = None,
        low_vol_weights: Optional[Dict[str, float]] = None,
    ) -> Dict:
        """
        Args:
            trade_date: 交易日，'YYYY-MM-DD'，默认今天。
            lookback_days: OHLCV 回看天数。
            sectors: 关心的板块名列表，None 走默认。
            max_stocks: 限制处理股票数（调试用）。
            current_positions: 当前持仓（code, weight, [score]）。
            total_capital: 总资金（用于换手成本估算）。
            config: 完整 strategy_params 或运行态覆盖；None 则用本实例 self.runtime。
            prebuilt_data: 离线测试时直接注入信号计算的入参，
                跳过 query_service 真实拉数。
            ohlcv_by_code: 趋势退出用的个股日线；缺省则「未创新高」支路为 False。
            prev_f35_by_code: 上一交易日 f35，供 bollinger_require_declining。
            index_bars: 指数收盘序列，只给账户的均线/动量用，不另算灯色。
            respect_book_rebalance_band: 中性/防守日在偏离带内时保留当前持仓。
            book_decision: 回测层已 step 过的 Book 决策；传入则不再二次 step。
        """
        cfg = self._resolve_exec_config(config)
        td = trade_date or datetime.now().strftime('%Y-%m-%d')

        logger.info("=" * 60)
        logger.info(f"🚀 策略层管道启动 trade_date={td}")
        logger.info("=" * 60)

        results: Dict[str, Any] = {}

        # 1. 信号
        if precomputed_signals is not None:
            signals_df = precomputed_signals
            logger.info(f"✅ 使用预计算信号: {len(signals_df)} 只候选")
        else:
            signals_df = await self._generate_signals(
                trade_date=td,
                lookback_days=lookback_days,
                sectors=sectors,
                max_stocks=max_stocks,
                prebuilt_data=prebuilt_data,
            )
        results['signals'] = signals_df
        style_regime = "neutral"
        if hasattr(signals_df, "attrs") and signals_df.attrs.get("regime") is not None:
            style_regime = str(signals_df.attrs.get("regime"))
        results["style_regime"] = style_regime
        if book_enabled(cfg) and index_bars and index_closes is None:
            index_closes = [float(r["close"]) for r in index_bars]
        decision = None
        if book_enabled(cfg):
            book_cfg = cfg.get("book_regime") or {}
            if book_decision is not None:
                decision = book_decision
            else:
                held: List[str] = []
                if (
                    current_positions is not None
                    and not current_positions.empty
                    and "code" in current_positions.columns
                ):
                    held = [str(code) for code in current_positions["code"].tolist()]
                decision = await self.note_market_day(
                    td,
                    index_closes=index_closes,
                    asset_closes=asset_closes,
                    held=held,
                    nav=float(total_capital) if total_capital else None,
                    confirm_green_days=int(
                        book_cfg.get("confirm_green_days", self.book.confirm_green_days)
                    ),
                )
            results["market_grade"] = str(decision.grade).lower()
            results["book_state"] = _state_for_grade(results["market_grade"])
            self._stamp_grade(signals_df, results["market_grade"])
            return await self._targets_by_light(
                td,
                grade=results["market_grade"],
                state=results["book_state"],
                signals_df=signals_df,
                current_positions=current_positions,
                total_capital=total_capital,
                cfg=cfg,
                results=results,
                ohlcv_by_code=ohlcv_by_code,
                prev_f35_by_code=prev_f35_by_code,
                respect_band=respect_book_rebalance_band,
                industry_weights=industry_weights,
                low_vol_weights=low_vol_weights,
            )
        else:
            results["market_grade"] = str(
                extract_market_grade(signals_df) or "yellow"
            ).lower()
        if signals_df.empty:
            logger.warning("⚠️ 无信号，管道终止（由回测层决定减仓/清仓）")
            self._pipeline_result = {
                'status': 'no_signals',
                'trade_date': td,
                'results': results,
                'market_grade': results['market_grade'],
            }
            return self._pipeline_result

        summary = run_equity_execution(
            trade_date=td,
            signals_df=signals_df,
            current_positions=current_positions,
            total_capital=total_capital,
            cfg=cfg,
            results=results,
            portfolio_optimizer=self._portfolio_optimizer,
            risk_manager=self._risk_manager,
            turnover_controller=self._turnover_controller,
            ohlcv_by_code=ohlcv_by_code,
            prev_f35_by_code=prev_f35_by_code,
            decision=decision,
            asset_closes=asset_closes,
        )
        self._pipeline_result = summary
        return summary

    async def _targets_by_light(
        self,
        trade_date: str,
        *,
        grade: str,
        state: str,
        signals_df: pd.DataFrame,
        current_positions: Optional[pd.DataFrame],
        total_capital: float,
        cfg: Dict[str, Any],
        results: Dict[str, Any],
        ohlcv_by_code,
        prev_f35_by_code,
        respect_band: bool,
        industry_weights: Optional[Dict[str, float]] = None,
        low_vol_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """三个策略都跑，再按灯和当前持仓合成目标仓。

        股票每天按排名更新。industry_weights / low_vol_weights 有值时沿用上一天的行业 ETF 和低波。
        """
        from zstock.strategy_management.sleeve_combine import combine_by_light, is_stock
        from zstock.strategy_management.strategy_industry_etf import IndustryEtfStrategy
        from zstock.strategy_management.strategy_low_volatility import LowVolatilityStrategy
        from zstock.strategy_management.strategy_section.execute import resolve_effective_top_k
        from zstock.strategy_management.strategy_section.rank_hold import rank_stock_weights

        regime = str(results.get("style_regime") or "neutral")
        if hasattr(signals_df, "attrs") and signals_df.attrs.get("regime"):
            regime = str(signals_df.attrs.get("regime"))
        n = resolve_effective_top_k(cfg, regime)
        buffer = int((cfg.get("final_score") or {}).get("rank_hold_buffer", 5))
        cap = float(
            (cfg.get("portfolio_optimization") or {}).get(
                "max_weight_per_stock",
                (cfg.get("portfolio") or {}).get("max_weight_per_stock", 0.2),
            )
        )
        held_stocks = [
            code for code in weight_map(current_positions) if is_stock(code)
        ]
        stocks = rank_stock_weights(
            signals_df, held_stocks, n=n, buffer=buffer, cap=cap
        )
        qs = self._query_service
        if industry_weights is None:
            industry = await IndustryEtfStrategy(query_service=qs).select(trade_date)
        else:
            industry = dict(industry_weights)
        if low_vol_weights is None:
            low_vol = await LowVolatilityStrategy(query_service=qs).select(trade_date)
        else:
            low_vol = dict(low_vol_weights)
        current = weight_map(current_positions)
        target = combine_by_light(grade, current, stocks, industry, low_vol)
        results["industry_weights"] = industry
        results["low_vol_weights"] = low_vol
        book_cfg = cfg.get("book_regime") or {}
        if respect_band and current:
            band = float(book_cfg.get("rebalance_band", REBALANCE_BAND))
            min_util = float(book_cfg.get("min_utilization", MIN_UTILIZATION))
            if not needs_book_trade(current, target, band=band, min_utilization=min_util):
                keep = current_positions.copy() if current_positions is not None else holdings_frame(current)
                if "weight" not in keep.columns:
                    keep["weight"] = 0.0
                return self._book_result(
                    trade_date,
                    results,
                    state,
                    keep,
                    total_capital,
                    current_positions=current_positions,
                    rebalance_skipped=True,
                )
        return self._book_result(
            trade_date,
            results,
            state,
            holdings_frame(target),
            total_capital,
            current_positions=current_positions,
        )

    @staticmethod
    def _stamp_grade(signals_df: pd.DataFrame, grade: str) -> None:
        """信号表上的灯与账户用的 M1 保持同一份。"""
        if signals_df is None or not hasattr(signals_df, "attrs"):
            return
        stamped = str(signals_df.attrs.get("market_grade") or "")
        if stamped and stamped != grade:
            logger.error("信号灯 %s 与当日 M1 %s 不一致，改为 %s", stamped, grade, grade)
        signals_df.attrs["market_grade"] = grade
        if "market_grade" in getattr(signals_df, "columns", []):
            signals_df["market_grade"] = grade

    def _book_result(
        self,
        trade_date: str,
        results: Dict[str, Any],
        state: str,
        holdings: pd.DataFrame,
        total_capital: float,
        *,
        current_positions: Optional[pd.DataFrame] = None,
        rebalance_skipped: bool = False,
    ) -> Dict[str, Any]:
        if rebalance_skipped:
            trading_costs = {
                "turnover": 0.0,
                "cost_pct": 0.0,
                "cost_amount": 0.0,
                "fee_rate": float(getattr(self._turnover_controller, "fee_rate", 0.0)),
                "stamp_tax": float(getattr(self._turnover_controller, "stamp_tax", 0.0)),
            }
        else:
            trading_costs = self._turnover_controller.estimate_trading_costs(
                current_holdings=current_positions,
                new_holdings=holdings,
                total_capital=total_capital,
            )
        summary = book_result(
            trade_date,
            results,
            state,
            holdings,
            total_capital,
            rebalance_skipped=rebalance_skipped,
            trading_costs=trading_costs,
        )
        self._pipeline_result = summary
        logger.info("✅ 三状态账户 %s: %d 个目标", state, len(holdings))
        return summary

    def _resolve_exec_config(self, override: Optional[Dict] = None) -> Dict[str, Dict]:
        """执行态配置：与 self.config 同源，禁止再回落到另一份生产 cache。"""
        base_params = self.config if self.config else None
        return resolve_runtime_config(override, base_params=base_params)
