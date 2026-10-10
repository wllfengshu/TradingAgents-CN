"""
截面日频策略管道（Pipeline）

核心策略：市场情绪 → 选主线 → 选龙头 → 选合力

流程编排：M0(数据) → M1(情绪) → M2(主线) → M3(龙头) → M4(合力) → M5(合成) → M6(输出)
因子公式委托给各自 Factor 类（MarketFactors / SectorFactors / DragonFactors / ForceFactors）。

现场与预计算走同一套 _run_funnel（M1→M6），只换 raw 数据源：
- score_signals_live / run_pipeline：OHLCV+资金流现场算因子
- score_signals：Mongo 预计算原始值（改权重无需重算）

M0  数据装载     OHLCV + 资金流 + 板块映射
M1  市场情绪     5因子 → 仓位系数（红/黄/绿）
M2  选主线       8因子 → Top N 板块
M3  选龙头       5因子 → 每板块 Top M 龙头
M4  选合力       4因子复合 + 风格检测动态调权
M5  最终合成     3层加权 → 策略信号分
M6  输出         TopK → buy/watch 信号
"""

from __future__ import annotations

import logging
import asyncio
import copy
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .dragon_factors import DragonFactors
from .force_factors import ForceFactors
from .market_factors import MarketFactors
from .prefilters import PreFilters
from .sector_factors import SectorFactors
from zstock.common.config.strategy_config import (
    get_neutral_style,
    load_strategy_params,
    resolve_active_factors,
)
from zstock.common.utils.common_utils import (
    get_index_ohlcv_df,
    normalize_date,
    select_top_k,
)
from zstock.data_management.query_service import get_data_query_service

logger = logging.getLogger(__name__)


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
    两条入口，同一漏斗
    ═══════════════════════════════════════════════════════════════════

    score_signals_live / run_pipeline  现场 OHLCV → _run_funnel(source=live)
    score_signals                      Mongo raw → _run_funnel(source=precomputed)
    打分、门禁、TopK、M6 输出只写一遍。
    """

    def __init__(
        self,
        config_path: str = None,
        config_override: Optional[Dict] = None,
    ):
        """
        Args:
            config_path: 备用策略参数文件；None 则用 strategy_config 生产 SSOT
            config_override: 覆盖配置（改权重/门槛/topK，供网格搜索）
        """
        if config_path:
            self.config = load_strategy_params(config_path)
        else:
            self.config = copy.deepcopy(load_strategy_params())
        if config_override:
            self._merge_config(config_override)
        self.prefilters = PreFilters()
        self._query_service = get_data_query_service()
        self._reset_precomputed()
        self._all_stocks_cache: Optional[List[Dict[str, Any]]] = None
        self._sectors_cache: Optional[List[Dict[str, Any]]] = None
        self._style_cache: Dict[str, Dict[str, object]] = {}
        self._fund_provider = None
        self._fund_provider_tried = False
        logger.info("✅ 策略管道初始化完成")

    # ----- 配置读取（编排旋钮，不下沉）-----
    # 分层查找 / by_regime 是 pipeline 取「这一层取几个、怎么加权」的入口。
    # 不放进 strategy_config：那边是 JSON→派生结构，这边是当日编排读数。

    def _merge_config(self, override: Dict) -> None:
        """网格搜索用：浅合并一层 dict，不递归。"""
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
        return int(self._cfg_from_layered_config("sector_layer", "top_sectors", 4))

    def _cfg_top_per_sector(self) -> int:
        return int(self._cfg_from_layered_config("dragon_layer", "top_per_sector", 2))

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
        """sector_layer / dragon_layer 的 gates；缺省键则返回 None（走 legacy φ）。"""
        layer = self.config.get(layer_key) or {}
        if "gates" not in layer:
            return None
        gates = layer.get("gates")
        return list(gates) if gates is not None else []

    def _market_selection(self) -> Dict[str, Any]:
        return self.config.get("market_selection") or {}

    def _factor_stops_on_red(self) -> bool:
        ms = self._market_selection()
        if "factor_pipeline_stops_on_red" in ms:
            return bool(ms["factor_pipeline_stops_on_red"])
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
    ) -> Tuple[float, str]:
        # 门禁规则在 ForceFactors；pipeline 只传入当日 cooperative_force 配置。
        return ForceFactors.resolve_gate_settings(coop_cfg, regime, grade)

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

    # ----- 预计算内存索引（回测 I/O，不是因子）-----
    # 不下沉到 Factor 类：因子不算数据、不碰 query_service。
    # 也不单独成类：生命周期就是本 pipeline 实例的一次回测区间。

    def _reset_precomputed(self) -> None:
        """清空 M1~M4 预计算内存索引（未预加载时 score_signals 逐日查库）。"""
        self._pc_market: Dict[str, Dict[str, Any]] = {}
        self._pc_sectors: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._pc_dragons: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._pc_forces: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._pc_dragon_df: Optional[pd.DataFrame] = None
        self._pc_dragon_idx: Dict[str, Any] = {}
        self._pc_force_df: Optional[pd.DataFrame] = None
        self._pc_force_idx: Dict[str, Any] = {}
        self._pc_range: Optional[Tuple[str, str]] = None

    @staticmethod
    def _index_factor_docs_by_date(
        docs: List[Dict[str, Any]],
        sink: Dict[str, Any],
        *,
        one_per_day: bool = False,
    ) -> None:
        for doc in docs or []:
            td = normalize_date(doc.get("trade_date", ""))
            if not td:
                continue
            if one_per_day:
                sink[td] = doc
            else:
                sink[td].append(doc)

    @staticmethod
    def _frames_from_docs(parts: List[pd.DataFrame], leftover: Optional[List[Dict]]) -> Optional[pd.DataFrame]:
        frames = list(parts)
        if leftover:
            frames.append(pd.DataFrame.from_records(leftover))
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True, copy=False)
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].map(normalize_date)
        return df

    async def _load_factor_range_frames(
        self,
        loader,
        start: str,
        end: str,
    ) -> Optional[pd.DataFrame]:
        frames: List[pd.DataFrame] = []

        def _sink(docs: List[Dict[str, Any]]) -> None:
            if docs:
                frames.append(pd.DataFrame.from_records(docs))

        try:
            leftover = await loader(start, end, sink=_sink)
        except TypeError:
            leftover = await loader(start, end)
        return self._frames_from_docs(frames, leftover)

    async def preload_precomputed_factors(
        self, start_date: str, end_date: str
    ) -> None:
        """回测前批量加载 M1~M4 预计算因子，后续 score_signals 走内存。

        M1/M2 体积小，直接按日 dict。M3/M4 六年可达千万级文档，流式拼 DataFrame，
        避免 list+dict 双份驻留把内存打爆。
        """
        qs = self._query_service
        start = normalize_date(start_date)
        end = normalize_date(end_date)
        if (
            self._pc_range == (start, end)
            and self._pc_dragon_df is not None
            and self._pc_force_df is not None
        ):
            logger.info("📦 因子已在内存，跳过预加载 %s~%s", start, end)
            return
        self._reset_precomputed()

        market_docs, sector_docs = await asyncio.gather(
            qs.get_factor_market_range(start, end),
            qs.get_factor_sectors_range(start, end),
        )
        self._index_factor_docs_by_date(market_docs or [], self._pc_market, one_per_day=True)
        self._index_factor_docs_by_date(sector_docs or [], self._pc_sectors)

        logger.info("📦 开始加载 M3 龙头因子（按窗口流式入库）...")
        dragon_df = await self._load_factor_range_frames(
            qs.get_factor_dragons_range, start, end
        )
        if dragon_df is not None and not dragon_df.empty:
            self._pc_dragon_df = dragon_df
            self._pc_dragon_idx = dragon_df.groupby("trade_date", sort=False).indices

        logger.info("📦 开始加载 M4 合力因子（按窗口流式入库）...")
        force_df = await self._load_factor_range_frames(
            qs.get_factor_forces_range, start, end
        )
        if force_df is not None and not force_df.empty:
            self._pc_force_df = force_df
            self._pc_force_idx = force_df.groupby("trade_date", sort=False).indices

        self._pc_range = (start, end)
        m3_days = len(self._pc_dragon_idx) if self._pc_dragon_idx else len(self._pc_dragons)
        m4_days = len(self._pc_force_idx) if self._pc_force_idx else len(self._pc_forces)
        logger.info(
            "📦 因子区间预加载 %s~%s: M1=%d 天, M2=%d 天, M3=%d 天, M4=%d 天",
            start, end,
            len(self._pc_market), len(self._pc_sectors),
            m3_days, m4_days,
        )
        if self._all_stocks_cache is None:
            docs, _ = await qs.get_all_stocks()
            self._all_stocks_cache = docs

    def _cached_factor_market(self, trade_date: str) -> Optional[Dict[str, Any]]:
        return self._pc_market.get(normalize_date(trade_date))

    def _cached_factor_sectors(self, trade_date: str) -> List[Dict[str, Any]]:
        return list(self._pc_sectors.get(normalize_date(trade_date), []))

    def _records_for_date(
        self,
        df: Optional[pd.DataFrame],
        idx_map: Dict[str, Any],
        trade_date: str,
        fallback: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        td = normalize_date(trade_date)
        if df is not None and idx_map:
            pos = idx_map.get(td)
            if pos is None:
                return []
            return df.iloc[pos].to_dict("records")
        return list(fallback)

    def _cached_factor_dragons(
        self,
        trade_date: str,
        sector_codes: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        dragons = self._records_for_date(
            self._pc_dragon_df,
            self._pc_dragon_idx,
            trade_date,
            self._pc_dragons.get(normalize_date(trade_date), []),
        )
        if sector_codes is None:
            return dragons
        sec_set = set(sector_codes)
        return [d for d in dragons if d.get("sector_code") in sec_set]

    def dragon_field_by_code(
        self,
        trade_date: str,
        field: str,
        codes: Optional[Any] = None,
    ) -> Dict[str, float]:
        """预计算龙头文档字段 → {code: float}。未 preload 时返回空（策略层退出用）。"""
        want = {str(c) for c in codes} if codes is not None else None
        out: Dict[str, float] = {}
        df = self._pc_dragon_df
        td = normalize_date(trade_date)
        if df is not None and self._pc_dragon_idx and field in df.columns:
            pos = self._pc_dragon_idx.get(td)
            if pos is None:
                return {}
            day = df.iloc[pos]
            if want is not None and "code" in day.columns:
                day = day[day["code"].astype(str).isin(want)]
            for code, val in zip(day["code"].astype(str), day[field]):
                if not code or (want is not None and code not in want):
                    continue
                try:
                    out[code] = float(val)
                except (TypeError, ValueError):
                    out[code] = float("nan")
            return out
        for d in self._cached_factor_dragons(trade_date):
            code = str(d.get("code") or "")
            if not code or (want is not None and code not in want):
                continue
            try:
                out[code] = float(d.get(field, float("nan")))
            except (TypeError, ValueError):
                out[code] = float("nan")
        return out

    def _cached_factor_forces(self, trade_date: str) -> List[Dict[str, Any]]:
        return self._records_for_date(
            self._pc_force_df,
            self._pc_force_idx,
            trade_date,
            self._pc_forces.get(normalize_date(trade_date), []),
        )

    async def _get_all_stocks_cached(self) -> List[Dict[str, Any]]:
        if self._all_stocks_cache is None:
            docs, _ = await self._query_service.get_all_stocks()
            self._all_stocks_cache = docs
        return self._all_stocks_cache

    async def _get_sectors_cached(self) -> List[Dict[str, Any]]:
        if self._sectors_cache is None:
            sector_list, _ = await self._query_service.get_sector_list()
            self._sectors_cache = sector_list or []
        return self._sectors_cache

    async def _load_precomputed(self, method: str, trade_date: str, **kwargs):
        """优先走区间缓存，否则查 query_service。"""
        if self._pc_range is not None:
            getter = {
                "get_factor_market": self._cached_factor_market,
                "get_factor_sectors": self._cached_factor_sectors,
                "get_factor_dragons": self._cached_factor_dragons,
                "get_factor_forces": self._cached_factor_forces,
            }[method]
            return getter(trade_date, **kwargs)
        return await getattr(self._query_service, method)(trade_date, **kwargs)

    async def _detect_style(
        self,
        trade_date: str,
        index_ohlcv: Optional[pd.DataFrame] = None,
    ) -> Dict[str, object]:
        """编排：取指数行情 → MarketFactors.detect_style。分类公式不在这里。"""
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
            result = MarketFactors.detect_style(df, trade_date)
        except Exception as e:
            logger.warning(f"风格检测失败，使用中性默认: {e}")
            result = get_neutral_style()
        self._style_cache[trade_date] = result
        return result

    @staticmethod
    def _make_candidate(
        code: str,
        sector_code: str,
        dragon_score: float,
        **extra_fields
    ) -> Dict:
        """M3→M4 候选字典（编排胶水，不是因子）。"""
        return {
            'code': code,
            'sector_code': sector_code,
            'dragon_composite_score': dragon_score,
            **extra_fields,
        }

    @staticmethod
    def _dedupe_candidates_by_code(candidates: List[Dict]) -> List[Dict]:
        """同一代码跨板块重复时保留龙头分更高的一条，避免占掉 top_k 名额。"""
        best: Dict[str, Dict] = {}
        order: List[str] = []
        for c in candidates:
            code = c.get("code")
            if not code:
                continue
            score = float(c.get("dragon_composite_score", 0.0) or 0.0)
            prev = best.get(code)
            if prev is None:
                best[code] = c
                order.append(code)
                continue
            prev_score = float(prev.get("dragon_composite_score", 0.0) or 0.0)
            if score > prev_score:
                best[code] = c
        return [best[code] for code in order]

    # ----- M0 现场取数（编排 query_service，因子类禁止查库）-----

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
        qs = self._query_service or get_data_query_service()

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
        active = self._get_active_factors(regime, grade)
        logger.info(
            f"🎨 {td} 市场风格: {regime} | "
            f"autocorr={style_info.get('autocorr', 0):.3f} "
            f"momentum_w={style_info.get('momentum_weight', 0.5):.2f} "
            f"reversal_w={style_info.get('reversal_weight', 0.5):.2f}"
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
            f"🔍 宇宙过滤: 股 {len(all_stocks)}→{len(filtered_stocks)} "
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
        m2_scores = SectorFactors.scores_from_raw(
            sector_raw,
            regime=regime,
            active_factors=active,
            top_n=self._cfg_top_sectors(),
            gates=self._cfg_layer_gates("sector_layer"),
        )
        top_sectors = select_top_k(m2_scores, self._cfg_top_sectors())
        logger.info(f"✅ M2 Top {self._cfg_top_sectors()} 板块: {[s[0] for s in top_sectors]}")

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
            m3_scores = DragonFactors.scores_from_raw(
                dragon_raw,
                regime=regime,
                active_factors=active,
                gates=self._cfg_layer_gates("dragon_layer"),
            )
            if not m3_scores:
                continue
            for code, m3_score in select_top_k(m3_scores, top_per):
                all_candidates.append(self._make_candidate(code, sector_code, m3_score))
        all_candidates = self._dedupe_candidates_by_code(all_candidates)
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
        m4_threshold, gate_mode = self._resolve_m4_gate_settings(coop_cfg, regime, grade)
        ranked = ForceFactors.score_and_compose_final(
            candidates=all_candidates,
            top_sectors=top_sectors,
            m5_weights=self._cfg_final_weights(),
            style_info=style_info,
            active_factors=active,
            force_docs_map=force_map,
            m4_threshold=m4_threshold,
            gate_mode=gate_mode,
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
                "funnel": {
                    "n_m3": len(all_candidates),
                    "n_m4": 0,
                    "n_buy": 0,
                    "stop": "m4_empty",
                    "regime": regime,
                },
            }

        return {
            "early_exit": False,
            "trade_date": td,
            "market_risk_level": grade,
            "position_scale_factor": scale,
            "regime": regime,
            "market_composite_score": float(sentiment.get("market_composite_score", 0.0)),
            "ranked_all": ranked,
            "funnel": {
                "n_m3": len(all_candidates),
                "n_m4": len(ranked),
                "n_buy": 0,
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
            docs = await self._load_precomputed("get_factor_sectors", trade_date)
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
            docs = await self._load_precomputed(
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

        fund_provider = await self._ensure_fund_provider()
        jobs = []
        for sc, _ in top_sectors:
            stocks = [s for s in (sector_stocks or {}).get(sc, []) if s in filtered_stocks]
            if stocks:
                jobs.append((sc, stocks))
        raw_by = (
            DragonFactors.calculate_all_dragon_factors_raw(
                jobs,
                stock_ohlcv or {},
                trade_date=trade_date,
                fund_provider=fund_provider,
            )
            if jobs
            else {}
        )
        return {sc: (raw_by.get(sc) or {}) for sc, _ in jobs}

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
            docs = await self._load_precomputed("get_factor_forces", trade_date)
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

    def _result_to_signals_df(self, result: Dict[str, Any]) -> pd.DataFrame:
        """M6：漏斗结果 → 策略层 signals DataFrame（buy/watch + funnel attrs）。"""
        td = str(result.get("trade_date") or "")
        grade = str(result.get("market_risk_level", "unknown"))
        regime = str(result.get("regime", "neutral"))
        scale = float(result.get("position_scale_factor", 0.0))
        funnel = dict(result.get("funnel") or {})
        if result.get("early_exit"):
            return self._empty_signals_df(
                td, market_risk_level=grade, position_scale_factor=scale, funnel=funnel
            )
        ranked_all = result.get("ranked_all") or []
        if not ranked_all:
            return self._empty_signals_df(
                td, market_risk_level=grade, position_scale_factor=scale, funnel=funnel
            )
        signals = ranked_all[: self._exit_universe_n(regime)]
        allow_new = self._allow_new_open_in_signals(grade)
        df = self._ranked_to_signals_df(
            signals, td, grade, scale, regime=regime, allow_new_open=allow_new
        )
        n_buy = (
            int((df["signal_type"] == "buy").sum())
            if not df.empty and "signal_type" in df.columns
            else 0
        )
        df.attrs["funnel_n_m3"] = int(funnel.get("n_m3", 0))
        df.attrs["funnel_n_m4"] = int(funnel.get("n_m4", 0))
        df.attrs["funnel_n_buy"] = n_buy
        df.attrs["funnel_stop"] = str(funnel.get("stop", "ok"))
        return df

    async def _run_funnel_live(self, data: Dict[str, Any], trade_date: str) -> Dict[str, Any]:
        idx_df = get_index_ohlcv_df(data.get("index_ohlcv") or {})
        sentiment = MarketFactors.calculate_market_sentiment(
            idx_df,
            index_name=data.get("index_name", "沪深 300"),
            trade_date=trade_date,
        )
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
        index_name: str = "沪深 300",
    ) -> List[Dict]:
        """旧入口：现场漏斗，返回 top_k 候选 list（不是 DataFrame）。"""
        data = {
            "trade_date": trade_date,
            "all_stocks": all_stocks,
            "stock_infos": stock_infos,
            "stock_ohlcv": stock_ohlcv,
            "stock_flow_recent": stock_flow_recent,
            "sectors": sectors,
            "sector_stocks": sector_stocks,
            "index_ohlcv": index_ohlcv,
            "index_name": index_name,
        }
        result = await self._run_funnel_live(data, trade_date)
        if result.get("early_exit"):
            return []
        regime = str(result.get("regime") or "neutral")
        grade = str(result.get("market_risk_level") or "unknown")
        scale = float(result.get("position_scale_factor", 1.0))
        ranked = (result.get("ranked_all") or [])[: self._cfg_top_k(regime)]
        for sig in ranked:
            sig["market_risk_level"] = grade
            sig["position_scale_factor"] = scale
        return ranked

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
        td = trade_date
        market_doc = await self._load_precomputed("get_factor_market", td)
        if market_doc is None:
            raise ValueError(f"无预计算 M1 数据: {td}")
        sentiment = MarketFactors.score_from_doc(market_doc)
        # 红灯在漏斗内就会停；这里先判断，避免无 M2 的测试 QS 还去拉股票列表。
        if str(sentiment.get("market_risk_level")) == "red" and self._factor_stops_on_red():
            result = await self._run_funnel(
                td, sentiment, source="precomputed",
                all_stocks=[], stock_infos={}, sectors=[],
            )
            return self._result_to_signals_df(result)
        all_stocks_docs = await self._get_all_stocks_cached()
        result = await self._run_funnel(
            td,
            sentiment,
            source="precomputed",
            all_stocks=[d["code"] for d in all_stocks_docs],
            stock_infos={d["code"]: d for d in all_stocks_docs},
            sectors=await self._get_sectors_cached(),
        )
        return self._result_to_signals_df(result)

    def _ranked_to_signals_df(
        self,
        ranked: List[Dict[str, Any]],
        trade_date: str,
        market_risk_level: str,
        position_scale_factor: float,
        regime: str = "neutral",
        allow_new_open: bool = True,
    ) -> pd.DataFrame:
        """M6 输出契约（策略层 DataFrame schema）。不下沉到因子：这是管道产品，不是因子公式。"""
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
                "f35_bollinger_trend": float(sig.get("f35_bollinger_trend", float("nan"))),
                "rank": i,
                "signal_type": (
                    "buy" if (allow_new_open and i <= top_k_final) else "watch"
                ),
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
        df.attrs["m1_allow_new_open"] = allow_new_open
        logger.info(
            f"✅ {td} 信号 DataFrame: buy={sum(1 for r in rows if r['signal_type']=='buy')} "
            f"universe={len(df)} (grade={grade}, scale={scale:.2f}, allow_new={allow_new_open})"
        )
        return df

    async def _ensure_fund_provider(self):
        """现场 M3 的 PIT 基本面缓存（预计算文档已含 f39/f40，不走这里）。"""
        if self._fund_provider_tried:
            return self._fund_provider
        self._fund_provider_tried = True
        self._fund_provider = None
        qs = self._query_service
        if qs is None or not hasattr(qs, "get_all_fundamental_pershare"):
            return None
        try:
            from zstock.data_management.fundamental_provider import FundamentalDataProvider

            provider = FundamentalDataProvider()
            await provider.load_from_query_service(qs)
            if provider.is_loaded:
                self._fund_provider = provider
                logger.info(
                    "基本面因子就绪: BPS=%d, HolderChange=%d",
                    provider.codes_with_pb(),
                    provider.codes_with_holder(),
                )
        except Exception as e:
            logger.warning("基本面因子加载失败，f39/f40 将为 NaN: %s", e)
        return self._fund_provider

    @staticmethod
    def _empty_signals_df(
        trade_date: str,
        market_risk_level: str = "red",
        position_scale_factor: float = 0.0,
        funnel: Optional[Dict[str, Any]] = None,
    ) -> pd.DataFrame:
        """空信号表，列与 attrs 与 _ranked_to_signals_df 对齐。"""
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
        extra = funnel or {}
        df.attrs["funnel_n_m3"] = int(extra.get("n_m3", 0))
        df.attrs["funnel_n_m4"] = int(extra.get("n_m4", 0))
        df.attrs["funnel_n_buy"] = int(extra.get("n_buy", 0))
        df.attrs["funnel_stop"] = str(extra.get("stop", "empty"))
        if extra.get("regime") is not None:
            df.attrs["regime"] = extra.get("regime")
        return df
