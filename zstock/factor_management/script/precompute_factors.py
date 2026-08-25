"""
因子预计算脚本（手动批处理，不属于在线策略流程）

职责：
1. 从 MongoDB 加载 OHLCV / 资金流
2. 使用 FactorComputeEngine 计算全市场因子原始值
3. 写入 zstock_factor_* 集合

用法：
    python -m zstock.factor_management.script.precompute_factors --start 2026-01-01 --end 2026-06-30
    python -m zstock.factor_management.script.precompute_factors --date 2026-05-15 --lookback 90
    python -m zstock.factor_management.script.precompute_factors --start 2024-01-01 --end 2024-12-31 --workers 16 --load-workers 8

并发（默认最多占用约一半 CPU/内存，见 ZSTOCK_RESOURCE_FRACTION）：
    环境变量 ZSTOCK_RESOURCE_FRACTION=0.5（默认）
    ZSTOCK_PRECOMPUTE_WORKERS / ZSTOCK_PRECOMPUTE_LOAD_WORKERS / ZSTOCK_PRECOMPUTE_QUERY_WORKERS

打分 / 回测请用 CrossSectionStrategyPipeline.score_signals，不要走本脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from bisect import bisect_left, bisect_right
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
import numpy as np

from zstock.common.utils.common_utils import ensure_ohlcv_sorted
from zstock.common.utils.resource_budget import (
    cap_worker_count,
    compute_resource_budget,
)
from zstock.common.utils.xtquant_data_utils import to_xt_code
from zstock.factor_management.dragon_factors import DragonFactors
from zstock.factor_management.force_factors import ForceFactors
from zstock.factor_management.fundamental_factors import FundamentalDataProvider
from zstock.factor_management.market_factors import MarketFactors
from zstock.factor_management.sector_factors import SectorFactors
from zstock.factor_management.prefilters import PreFilters

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


def _resolve_worker_count(
    env_key: str,
    budget_value: int,
    *,
    name: str,
) -> int:
    raw = os.environ.get(env_key)
    if raw:
        try:
            requested = max(1, int(raw))
            return cap_worker_count(requested, budget_value, name=name)
        except ValueError:
            logger.warning("忽略无效环境变量 %s=%r", env_key, raw)
    return budget_value


def _apply_resource_budget(
    *,
    resource_fraction: Optional[float],
    compute_workers: Optional[int],
    load_workers: Optional[int],
    query_workers: Optional[int],
) -> Tuple[int, int, int, "ResourceBudget"]:
    from zstock.common.utils.resource_budget import ResourceBudget

    if resource_fraction is not None:
        os.environ["ZSTOCK_RESOURCE_FRACTION"] = str(resource_fraction)
    budget = compute_resource_budget(
        fraction=resource_fraction if resource_fraction is not None else None
    )

    if compute_workers is not None:
        compute = cap_worker_count(
            compute_workers, budget.compute_workers, name="workers"
        )
    else:
        compute = _resolve_worker_count(
            "ZSTOCK_PRECOMPUTE_WORKERS", budget.compute_workers, name="workers"
        )

    if load_workers is not None:
        load = cap_worker_count(
            load_workers, budget.load_workers, name="load-workers"
        )
    else:
        load = _resolve_worker_count(
            "ZSTOCK_PRECOMPUTE_LOAD_WORKERS", budget.load_workers, name="load-workers"
        )

    if query_workers is not None:
        query = cap_worker_count(
            query_workers, budget.query_workers, name="query-workers"
        )
    else:
        query = _resolve_worker_count(
            "ZSTOCK_PRECOMPUTE_QUERY_WORKERS",
            budget.query_workers,
            name="query-workers",
        )

    return compute, load, query, budget


def _sort_ohlcv_frame(code: str, df: pd.DataFrame) -> Tuple[str, pd.DataFrame]:
    if df is None or df.empty:
        return code, df
    if "trade_date" not in df.columns:
        return code, df
    df = ensure_ohlcv_sorted(df)
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    return code, df


def _sort_ohlcv_item(item: Tuple[str, pd.DataFrame]) -> Tuple[str, pd.DataFrame]:
    code, df = item
    return _sort_ohlcv_frame(code, df)


def _compute_sliced_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """进程池 worker：接收当日切片（可 pickle），绕过 GIL 真正多核计算。"""
    return FactorComputeEngine.compute_all_factors_raw_sync(
        **payload["data"],
        assume_sorted=True,
        filtered_sectors=payload["filtered_sectors"],
        filtered_stocks_set=payload["filtered_stocks_set"],
        quiet=True,
        fund_provider=payload.get("fund_provider"),
    )


def _compute_day_sync(
    preload: Dict[str, Any],
    trade_date: str,
    lookback_days: int,
    *,
    quiet: bool = True,
) -> Dict[str, Any]:
    data = FactorPrecomputeService.slice_preloaded_data(
        preload, trade_date, lookback_days
    )
    return FactorComputeEngine.compute_all_factors_raw_sync(
        **data,
        assume_sorted=True,
        filtered_sectors=preload["filtered_sectors"],
        filtered_stocks_set=preload["filtered_stocks_set"],
        quiet=quiet,
        fund_provider=preload.get("fund_provider"),
    )


@contextlib.contextmanager
def _error_handler(context: str, critical: bool = False):
    """统一异常处理上下文管理器"""
    try:
        yield
    except Exception as e:
        if critical:
            logger.error(f"[CRITICAL] {context}: {type(e).__name__}: {e}", exc_info=True)
            raise
        else:
            logger.warning(f"[WARNING] {context}: {type(e).__name__}: {e}", exc_info=True)


# ===================== 因子计算引擎 =====================

class FactorComputeEngine:
    """全市场因子计算引擎（纯计算逻辑，不涉及数据库）

    这是从 CrossSectionStrategyPipeline.compute_all_factor_raw() 提取的业务逻辑，
    专门用于预计算流程。
    """

    @staticmethod
    def compute_all_factors_raw_sync(
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
        stock_lhb_recent: Optional[Dict[str, List[Dict]]] = None,
        assume_sorted: bool = False,
        filtered_sectors: Optional[List[Dict]] = None,
        filtered_stocks_set: Optional[Set[str]] = None,
        sector_ohlcv: Optional[Dict[str, pd.DataFrame]] = None,
        quiet: bool = False,
        fund_provider: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        全市场因子原始值计算（供预计算入库，不做 topK 选股裁剪）。

        与 run_pipeline_raw 的差异：
        - M2：全部非黑名单板块
        - M3：全部过滤后板块 × 主板非黑名单成分股
        - M4：上述全部股票（按 code 去重）

        Args:
            trade_date: 交易日期
            all_stocks: 全市场股票代码列表
            stock_infos: 股票信息字典
            stock_ohlcv: 个股 OHLCV 数据
            stock_flow_recent: 个股最近资金流向
            sectors: 板块列表
            sector_stocks: 板块包含的股票
            index_ohlcv: 指数 OHLCV 数据
            index_name: 指数名称
            assume_sorted: OHLCV 是否已排序
            filtered_sectors: 预过滤的板块
            filtered_stocks_set: 预过滤的股票集合
            sector_ohlcv: 预聚合的板块 OHLCV
            fund_provider: 基本面数据提供者；静态方法不能用 self.fund_provider

        Returns:
            {market_raw, sector_raw, dragon_raw_by_sector, force_raw, ...} 字典（仅原始因子值，无得分）
        """

        log = logger.debug if quiet else logger.info

        log(f"🚀 全市场因子预计算: trade_date={trade_date}")

        if not assume_sorted:
            stock_ohlcv = {
                c: ensure_ohlcv_sorted(df) for c, df in (stock_ohlcv or {}).items()
            }
            index_ohlcv = {
                c: ensure_ohlcv_sorted(df) for c, df in (index_ohlcv or {}).items()
            }
            assume_sorted = True

        # ===== M1: 市场情绪 =====
        from zstock.common.utils.common_utils import get_index_code_from_ohlcv_dict
        idx_df = list(index_ohlcv.values())[0] if index_ohlcv else None
        index_code = get_index_code_from_ohlcv_dict(index_ohlcv)
        market_sentiment = MarketFactors.calculate_market_sentiment(
            idx_df, index_name=index_name, trade_date=trade_date
        )
        market_raw = market_sentiment.get("detail", {})
        log(
            f"📊 M1 完成: market_sentiment={market_sentiment.get('market_composite_score', 0):.2f}"
        )

        if filtered_sectors is None or filtered_stocks_set is None:
            raise ValueError(
                "filtered_sectors 和 filtered_stocks_set 必须由调用方提供。"
                "请先执行宇宙过滤（技术过滤 + 黑名单过滤）再传入。"
            )

        log(f"🔍 个股过滤后可用: {len(filtered_stocks_set)} 只")

        # 计算全市场板块OHLCV（用于F2.8排名计算）
        if sector_ohlcv is not None:
            market_sector_ohlcv = sector_ohlcv
        else:
            market_sector_ohlcv, _ = SectorFactors._aggregate_sectors_from_stocks(
                sector_stocks,
                stock_ohlcv,
                stock_flow_recent or {},
            )

        sector_raw = SectorFactors.calculate_all_sector_factors_raw(
            filtered_sectors,
            sector_stocks,
            stock_ohlcv,
            stock_flow_recent,
            sector_ohlcv=sector_ohlcv,
            market_sector_ohlcv=market_sector_ohlcv,
            trade_date=trade_date,
            eligible_codes=filtered_stocks_set,
        )
        log(f"📊 M2 完成: {len(sector_raw.get('f21_rps', {}))} 个板块")

        # ===== M3: 龙头因子计算 =====
        sector_jobs = []
        all_m3_codes: Set[str] = set()
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

        # M3 龙头因子计算：全市场 features 一次计算，逐板块复用
        # _precompute_stock_features 中所有计算都是个股独立的，
        # 对同一只股票同一份 OHLCV，无论放在哪个板块算结果都一样
        all_m3_codes_list = list(all_m3_codes)
        m3_features = DragonFactors._precompute_stock_features(
            stock_ohlcv,
            all_m3_codes_list,
            assume_sorted=True,
            trade_date=trade_date,
        )
        dragon_raw_by_sector: Dict[str, Dict] = {}
        force_candidates: List[Dict] = []
        for sector_code, codes in sector_jobs:
            dragon_raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw(
                codes,
                stock_ohlcv,
                assume_sorted=True,
                trade_date=trade_date,
                features=m3_features,
                fund_provider=fund_provider,
            )
            if not dragon_raw:
                continue
            dragon_raw_by_sector[sector_code] = dragon_raw
            for code in dragon_raw:
                force_candidates.append(
                    {
                        "code": code,
                        "sector_code": sector_code,
                    }
                )

        log(
            f"📊 M3 完成: {len(dragon_raw_by_sector)} 个板块, "
            f"{sum(len(v) for v in dragon_raw_by_sector.values())} 条原始因子 "
            f"(M4候选 {len(force_candidates)} 条，支持跨板块)"
        )

        # ===== M4: 合力因子 =====
        force_raw = ForceFactors.apply_cooperative_force_raw(
            force_candidates,
            stock_flow_recent=stock_flow_recent,
            stock_ohlcv=stock_ohlcv,
            stock_lhb_recent=stock_lhb_recent or {},
            assume_sorted=assume_sorted,
            trade_date=trade_date,
        )
        log(f"📊 M4 完成: {len(force_raw)} 只")

        # 提取每只股票的最新收盘价 (供基本面因子 PB 计算)
        stock_last_close: Dict[str, float] = {}
        for code, df in stock_ohlcv.items():
            if df is not None and len(df) > 0:
                try:
                    last_close = float(df["close"].iloc[-1])
                    if np.isfinite(last_close) and last_close > 0:
                        stock_last_close[code] = last_close
                except (IndexError, TypeError, ValueError):
                    pass

        return {
            "trade_date": trade_date,
            "market_raw": market_raw,
            "index_code": index_code,
            "index_name": index_name,
            "sector_raw": sector_raw,
            "filtered_sectors": filtered_sectors,
            "dragon_raw_by_sector": dragon_raw_by_sector,
            "all_candidates": force_candidates,
            "force_raw": force_raw,
            "market_risk_level": market_sentiment.get("market_risk_level", "neutral"),
            "position_scale_factor": market_sentiment.get("position_scale_factor", 1.0),
            "stock_infos": stock_infos,
            "stock_last_close": stock_last_close,
        }

    @staticmethod
    async def compute_all_factors_raw(**kwargs) -> Dict[str, Any]:
        """异步入口（兼容旧调用）；计算本身为 CPU 密集型同步逻辑。"""
        return FactorComputeEngine.compute_all_factors_raw_sync(**kwargs)


class FactorPrecomputeService:
    """因子预计算：全市场原始值入库（仅供本脚本使用）。"""

    def __init__(
        self,
        load_fundamentals: bool = True,
        *,
        compute_workers: Optional[int] = None,
        load_workers: Optional[int] = None,
        query_workers: Optional[int] = None,
        resource_fraction: Optional[float] = None,
    ) -> None:
        from zstock.data_management.database_service import get_database_service
        from zstock.data_management.query_service import get_data_query_service
        from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

        self.pipeline = CrossSectionStrategyPipeline()
        self.database_service = get_database_service()
        self.query_service = get_data_query_service()
        (
            self.compute_workers,
            self.load_workers,
            self.query_workers,
            budget,
        ) = _apply_resource_budget(
            resource_fraction=resource_fraction,
            compute_workers=compute_workers,
            load_workers=load_workers,
            query_workers=query_workers,
        )
        self.resource_budget = budget
        self.fund_provider: Optional[FundamentalDataProvider] = None
        if load_fundamentals:
            self._init_fundamental_provider()
        logger.info(
            "FactorPrecomputeService 初始化完成 "
            f"(资源预算={budget.resource_fraction:.0%}, "
            f"内存={budget.total_memory_gb}GB, CPU={budget.cpu_cores}核, "
            f"compute={self.compute_workers}, load={self.load_workers}, "
            f"query={self.query_workers})"
        )

    def _init_fundamental_provider(self):
        """从 MongoDB 加载基本面数据 (需先运行 sync_fundamental.py)"""
        try:
            self.fund_provider = FundamentalDataProvider()
            self.fund_provider.load_from_mongodb()
            if self.fund_provider.is_loaded:
                logger.info(
                    f"FundamentalDataProvider: 加载完成 "
                    f"(BPS={self.fund_provider.codes_with_pb()}, "
                    f"HolderChange={self.fund_provider.codes_with_holder()})"
                )
            else:
                logger.warning("FundamentalDataProvider: MongoDB 中无基本面数据, 请先运行 sync_fundamental.py")
                self.fund_provider = None
        except Exception as e:
            logger.warning(f"FundamentalDataProvider: 加载失败 - {e}, 基本面因子将不可用")
            import traceback
            logger.debug(traceback.format_exc())
            self.fund_provider = None

    async def precompute_single_date(
        self,
        trade_date: str,
        lookback_days: int = 120,
    ) -> Dict[str, int]:
        from zstock.common.utils.common_utils import normalize_date

        td = normalize_date(trade_date)
        logger.info(f"预计算 {td}")
        t0 = perf_counter()
        data = await self.pipeline.load_real_data(
            trade_date=td,
            lookback_days=lookback_days,
        )
        t1 = perf_counter()
        # 单日模式：需自行执行宇宙过滤（区间模式由 _preload_range_data 预过滤）
        prefilters = PreFilters()
        tech_filtered = prefilters.apply_technical_filters(
            data["all_stocks"], data["stock_ohlcv"], data["stock_infos"],
            apply_main_board=True, apply_bollinger=False,
        )
        bl_result = prefilters.apply_blacklist_filters(tech_filtered, sectors=data["sectors"])
        data["filtered_sectors"] = bl_result.get("sectors", data["sectors"])
        data["filtered_stocks_set"] = set(bl_result["stocks"])
        # 使用 FactorComputeEngine 计算原始因子值
        raw_result = FactorComputeEngine.compute_all_factors_raw_sync(
            **data,
            fund_provider=self.fund_provider,
        )
        t2 = perf_counter()
        stock_infos = data.get("stock_infos", {})
        counts = await self._store_all(td, raw_result, stock_infos)
        t3 = perf_counter()
        logger.info(
            f"{td} 完成: {counts} | "
            f"load={t1 - t0:.2f}s compute={t2 - t1:.2f}s store={t3 - t2:.2f}s"
        )
        return counts


    async def precompute_date_range(
        self,
        start_date: str,
        end_date: str,
        lookback_days: int = 120,
    ) -> Dict[str, int]:
        t_range0 = perf_counter()
        logger.info("筛选交易日（MongoDB 聚合，请稍候）...")
        trade_dates = await self._gen_trade_dates(start_date, end_date)
        if not trade_dates:
            raise ValueError(f"{start_date} ~ {end_date} 无可用交易日")

        preload = await self._preload_range_data(start_date, end_date, lookback_days)

        total = {"market": 0, "sector": 0, "dragon": 0, "force": 0}
        success = 0
        failed = 0
        workers = self.compute_workers
        logger.info(
            f"多进程并行预计算 {len(trade_dates)} 个交易日 "
            f"(processes={workers}, load={self.load_workers}, query={self.query_workers})"
        )

        sem = asyncio.Semaphore(workers)
        loop = asyncio.get_running_loop()
        compute_executor = ProcessPoolExecutor(max_workers=workers)

        async def _process_day(td: str) -> Tuple[str, Optional[Dict[str, int]], Optional[BaseException]]:
            async with sem:
                try:
                    t0 = perf_counter()
                    payload = {
                        "data": self.slice_preloaded_data(
                            preload, td, lookback_days
                        ),
                        "filtered_sectors": preload["filtered_sectors"],
                        "filtered_stocks_set": preload["filtered_stocks_set"],
                        "fund_provider": self.fund_provider,
                    }
                    raw_result = await loop.run_in_executor(
                        compute_executor,
                        _compute_sliced_payload,
                        payload,
                    )
                    t1 = perf_counter()
                    counts = await self._store_all(
                        td,
                        raw_result,
                        preload["stock_infos"],
                    )
                    t2 = perf_counter()
                    logger.info(
                        f"{td} 完成: {counts} | "
                        f"compute={t1 - t0:.2f}s store={t2 - t1:.2f}s"
                    )
                    return td, counts, None
                except Exception as e:
                    logger.warning(
                        f"[WARNING] precompute {td}: {type(e).__name__}: {e}",
                        exc_info=True,
                    )
                    return td, None, e

        try:
            results = await asyncio.gather(*[_process_day(td) for td in trade_dates])
        finally:
            compute_executor.shutdown(wait=True)

        for _td, counts, err in results:
            if err is not None or counts is None:
                failed += 1
                continue
            success += 1
            for k in total:
                total[k] += counts.get(k, 0)

        logger.info(
            f"预计算完成: 成功={success}, 失败={failed}, "
            f"总条数 market={total['market']} sector={total['sector']} "
            f"dragon={total['dragon']} force={total['force']} | "
            f"total={perf_counter() - t_range0:.2f}s"
        )
        return total

    async def _preload_range_data(
        self,
        start_date: str,
        end_date: str,
        lookback_days: int,
    ) -> Dict[str, Any]:
        from zstock.common.utils.common_utils import normalize_date

        start = normalize_date(start_date)
        end = normalize_date(end_date)
        window_start = (
            datetime.strptime(start, "%Y-%m-%d") - timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")

        t0 = perf_counter()
        all_stock_docs, _ = await self.query_service.get_all_stocks()
        all_stocks = [d["code"] for d in all_stock_docs]
        stock_infos: Dict[str, Dict] = {
            d["code"]: {
                "code": d["code"],
                "name": d.get("name", ""),
                "is_st": d.get("is_st", False),
                "is_mainboard": d.get("is_mainboard", False),
            }
            for d in all_stock_docs
        }

        sector_list, _ = await self.query_service.get_sector_list()
        sector_list = sector_list or []
        all_stock_set = set(all_stocks)
        sector_stocks_map: Dict[str, List[str]] = {}
        for sector in sector_list:
            sector_code = sector.get("sector_code")
            if not sector_code:
                continue
            stocks = [s for s in sector.get("stocks", []) if s in all_stock_set]
            if stocks:
                sector_stocks_map[sector_code] = stocks

        flow_days = max(15, lookback_days // 4)
        # 只加载资金流中实际使用的字段，减少内存占用 ~70%
        flow_projection = {
            "_id": 0, "code": 1, "trade_date": 1,
            "main_net": 1, "m_net": 1, "s_net": 1, "xl_net": 1,
            "turnover": 1,
            "xl_buy_amount": 1, "l_buy_amount": 1,
            "xl_sell_amount": 1, "l_sell_amount": 1,
        }

        # 分批顺序加载 MongoDB（小批次 × 日期窗口），全量合并到内存
        BATCH_SIZE = 120
        OHLCV_CODE_BATCH = 50
        DATE_CHUNK_DAYS = 90

        async def _load_batch(batch_codes: List[str]):
            qc = self.query_workers
            ohlcv, flow = await asyncio.gather(
                self.query_service.get_ohlcv_batch(
                    batch_codes,
                    window_start,
                    end,
                    batch_size=OHLCV_CODE_BATCH,
                    date_chunk_days=DATE_CHUNK_DAYS,
                    query_concurrency=qc,
                ),
                self.query_service.get_capital_flow_range(
                    batch_codes,
                    window_start,
                    end,
                    projection=flow_projection,
                    batch_size=OHLCV_CODE_BATCH,
                    date_chunk_days=DATE_CHUNK_DAYS,
                    query_concurrency=qc,
                ),
            )
            return ohlcv, flow

        batches = [
            all_stocks[i : i + BATCH_SIZE]
            for i in range(0, len(all_stocks), BATCH_SIZE)
        ]
        load_workers = self.load_workers
        load_sem = asyncio.Semaphore(load_workers)
        logger.info(
            f"分 {len(batches)} 批并发加载 OHLCV + 资金流 "
            f"(每批 {BATCH_SIZE} 只, 批并发 {load_workers}, 子查询并发 {self.query_workers}, "
            f"子查询 {OHLCV_CODE_BATCH} 只 × {DATE_CHUNK_DAYS} 天窗口)..."
        )
        stock_ohlcv_full: Dict[str, pd.DataFrame] = {}
        stock_flow_full: Dict[str, List[Dict]] = {}

        async def _load_one(bi: int, batch_codes: List[str]):
            async with load_sem:
                logger.info(
                    f"  加载批次 [{bi}/{len(batches)}] {len(batch_codes)} 只..."
                )
                return await _load_batch(batch_codes)

        batch_results = await asyncio.gather(
            *[_load_one(bi, bc) for bi, bc in enumerate(batches, 1)]
        )
        for ohlcv, flow in batch_results:
            stock_ohlcv_full.update(ohlcv)
            stock_flow_full.update(flow)

        # 指数数据单独查
        index_ohlcv_full = await self.query_service.get_ohlcv_batch(
            ["399300"],
            window_start,
            end,
            batch_size=1,
            date_chunk_days=DATE_CHUNK_DAYS,
            query_concurrency=self.query_workers,
        )

        # 多线程排序，后续每日 assume_sorted=True
        from zstock.common.utils.common_utils import ensure_ohlcv_sorted

        t_sort = perf_counter()
        sort_workers = min(self.compute_workers, max(1, len(stock_ohlcv_full)))
        with ThreadPoolExecutor(
            max_workers=sort_workers,
            thread_name_prefix="zstock_sort",
        ) as sort_pool:
            for code, df in sort_pool.map(
                _sort_ohlcv_item,
                list(stock_ohlcv_full.items()),
            ):
                stock_ohlcv_full[code] = df
            for code, df in sort_pool.map(
                _sort_ohlcv_item,
                list(index_ohlcv_full.items()),
            ):
                index_ohlcv_full[code] = df
        logger.info(f"OHLCV 预排序完成: {perf_counter() - t_sort:.2f}s")

        # 过滤结果跨日不变，只算一次
        # 使用公开接口 apply_blacklist_filters() 来同时过滤板块和个股
        tech_filtered = self.pipeline.prefilters.apply_technical_filters(
            all_stocks, stock_ohlcv_full, stock_infos, apply_main_board=True, apply_bollinger=False
        )
        blacklist_result = self.pipeline.prefilters.apply_blacklist_filters(tech_filtered, sectors=sector_list)
        filtered_sectors = blacklist_result.get("sectors", sector_list)
        filtered_stocks_set = set(blacklist_result.get("stocks", tech_filtered))

        needed_codes: set = set()
        for s in filtered_sectors:
            sc = s.get("sector_code")
            if sc:
                needed_codes.update(sector_stocks_map.get(sc, []))
        # 丢掉用不到的全市场 OHLCV / 资金流，显著降低每日切片成本
        stock_ohlcv_full = {
            c: df for c, df in stock_ohlcv_full.items() if c in needed_codes
        }
        stock_flow_full = {
            c: rows for c, rows in stock_flow_full.items() if c in needed_codes
        }

        from collections import defaultdict
        from zstock.data_management.query_service import COL_LHB

        stock_lhb_full: Dict[str, List[Dict]] = defaultdict(list)
        try:
            lhb_docs = await self.database_service.query(
                COL_LHB,
                {
                    "code": {"$in": list(needed_codes)},
                    "trade_date": {"$gte": window_start, "$lte": end},
                },
                projection={"_id": 0},
            )
            for doc in lhb_docs or []:
                code = doc.get("code")
                if code:
                    stock_lhb_full[code].append(doc)
            for code in stock_lhb_full:
                stock_lhb_full[code].sort(
                    key=lambda d: str(d.get("trade_date", ""))
                )
            logger.info(
                f"龙虎榜预加载: {len(lhb_docs or [])} 条, "
                f"{len(stock_lhb_full)} 只股票"
            )
        except Exception as e:
            logger.warning(
                "龙虎榜预加载失败（longhu_board_bonus 将为 0）: %s", e
            )

        flow_dates_index = {
            code: [str(d.get("trade_date", "")) for d in rows]
            for code, rows in stock_flow_full.items()
        }
        lhb_dates_index = {
            code: [str(d.get("trade_date", "")) for d in rows]
            for code, rows in stock_lhb_full.items()
        }
        ohlcv_dates_index = {
            code: df["trade_date"].tolist()
            for code, df in stock_ohlcv_full.items()
            if df is not None and not df.empty and "trade_date" in df.columns
        }
        index_dates_index = {
            code: df["trade_date"].tolist()
            for code, df in index_ohlcv_full.items()
            if df is not None and not df.empty and "trade_date" in df.columns
        }
        # 区间内板块 OHLCV 只聚合一次，每日切片复用（M2 最大耗时点）
        from zstock.factor_management.sector_factors import SectorFactors

        t_sec = perf_counter()
        need_sector_codes = {
            s.get("sector_code") for s in filtered_sectors if s.get("sector_code")
        }
        sector_stocks_use = {
            k: v for k, v in sector_stocks_map.items() if k in need_sector_codes
        }
        sector_ohlcv_full, _ = SectorFactors._aggregate_sectors_from_stocks(
            sector_stocks_use,
            stock_ohlcv_full,
            {},
        )
        for code, df in sector_ohlcv_full.items():
            if df is None or df.empty or "trade_date" not in df.columns:
                continue
            # 与个股切片一致：用字符串日期做 bisect
            df = df.copy()
            df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime(
                "%Y-%m-%d"
            )
            sector_ohlcv_full[code] = df
        sector_dates_index = {
            code: df["trade_date"].tolist()
            for code, df in sector_ohlcv_full.items()
            if df is not None and not df.empty
        }
        logger.info(
            f"板块 OHLCV 预聚合完成: {len(sector_ohlcv_full)} 个, "
            f"{perf_counter() - t_sec:.2f}s"
        )

        logger.info(
            f"区间预加载完成: stocks={len(all_stocks)} keep_ohlcv={len(stock_ohlcv_full)} "
            f"sectors={len(filtered_sectors)} flow={len(stock_flow_full)} "
            f"filtered_stocks={len(filtered_stocks_set)} "
            f"window={window_start}~{end} cost={perf_counter() - t0:.2f}s"
        )
        return {
            "all_stocks": all_stocks,
            "stock_infos": stock_infos,
            "sectors": sector_list,
            "filtered_sectors": filtered_sectors,
            "filtered_stocks_set": filtered_stocks_set,
            "sector_stocks": sector_stocks_map,
            "stock_ohlcv_full": stock_ohlcv_full,
            "stock_flow_full": stock_flow_full,
            "stock_lhb_full": dict(stock_lhb_full),
            "flow_dates_index": flow_dates_index,
            "lhb_dates_index": lhb_dates_index,
            "ohlcv_dates_index": ohlcv_dates_index,
            "index_ohlcv_full": index_ohlcv_full,
            "index_dates_index": index_dates_index,
            "sector_ohlcv_full": sector_ohlcv_full,
            "sector_dates_index": sector_dates_index,
            "flow_days": flow_days,
            "index_name": "沪深 300",
            "fund_provider": self.fund_provider,
        }

    @staticmethod
    def slice_preloaded_data(
        preload: Dict[str, Any],
        trade_date: str,
        lookback_days: int,
    ) -> Dict[str, Any]:
        td = trade_date
        start = (
            datetime.strptime(td, "%Y-%m-%d") - timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")
        flow_days = int(preload["flow_days"])

        stock_ohlcv: Dict[str, Any] = {}
        for code, df in preload["stock_ohlcv_full"].items():
            dates = preload["ohlcv_dates_index"].get(code, [])
            if df is None or df.empty or not dates:
                continue
            left = bisect_left(dates, start)
            right = bisect_right(dates, td)
            if right > left:
                stock_ohlcv[code] = df.iloc[left:right]

        index_ohlcv: Dict[str, Any] = {}
        for code, df in preload["index_ohlcv_full"].items():
            dates = preload["index_dates_index"].get(code, [])
            if df is None or df.empty or not dates:
                continue
            left = bisect_left(dates, start)
            right = bisect_right(dates, td)
            if right > left:
                index_ohlcv[code] = df.iloc[left:right]

        stock_flow_recent: Dict[str, List[Dict]] = {}
        for code, rows in preload["stock_flow_full"].items():
            if not rows:
                continue
            dates = preload["flow_dates_index"].get(code, [])
            if not dates:
                continue
            right = bisect_right(dates, td)
            if right <= 0:
                continue
            tail = rows[max(0, right - flow_days):right]
            if tail:
                stock_flow_recent[code] = tail

        stock_lhb_recent: Dict[str, List[Dict]] = {}
        lhb_days = 10
        for code, rows in preload.get("stock_lhb_full", {}).items():
            if not rows:
                continue
            dates = preload.get("lhb_dates_index", {}).get(code, [])
            if not dates:
                continue
            right = bisect_right(dates, td)
            if right <= 0:
                continue
            tail = rows[max(0, right - lhb_days):right]
            if tail:
                stock_lhb_recent[code] = tail

        sector_ohlcv: Dict[str, Any] = {}
        for code, df in preload.get("sector_ohlcv_full", {}).items():
            dates = preload.get("sector_dates_index", {}).get(code, [])
            if df is None or df.empty or not dates:
                continue
            left = bisect_left(dates, start)
            right = bisect_right(dates, td)
            if right > left:
                sector_ohlcv[code] = df.iloc[left:right]

        return {
            "trade_date": td,
            "all_stocks": preload["all_stocks"],
            "stock_infos": preload["stock_infos"],
            "stock_ohlcv": stock_ohlcv,
            "stock_flow_recent": stock_flow_recent,
            "stock_lhb_recent": stock_lhb_recent,
            "sectors": preload["sectors"],
            "sector_stocks": preload["sector_stocks"],
            "index_ohlcv": index_ohlcv,
            "index_name": preload["index_name"],
            "sector_ohlcv": sector_ohlcv,
        }

    # ─────────────────── 存储 ───────────────────

    async def _store_all(
        self,
        trade_date: str,
        raw_result: Dict[str, Any],
        stock_infos: Dict[str, Dict],
    ) -> Dict[str, int]:
        market, sector, dragon, force = await asyncio.gather(
            self._store_market(trade_date, raw_result),
            self._store_sector(trade_date, raw_result),
            self._store_dragon(trade_date, raw_result, stock_infos),
            self._store_force(trade_date, raw_result, stock_infos),
        )
        return {
            "market": market,
            "sector": sector,
            "dragon": dragon,
            "force": force,
        }

    async def _store_market(self, trade_date: str, raw_result: Dict) -> int:
        from zstock.data_management.query_service import COL_FACTOR_MARKET

        market_raw = raw_result.get("market_raw", {})
        if not market_raw:
            logger.warning(f"{trade_date} 无 M1 原始值，跳过")
            return 0

        doc = {
            "trade_date": trade_date,
            "index_code": raw_result.get("index_code", ""),
            "index_name": raw_result.get("index_name", "沪深 300"),
            # MF1 趋势强度
            "mf1_slope_pct": market_raw.get("mf1_slope_pct", float("nan")),
            "mf1_slope_pct_5d": market_raw.get("mf1_slope_pct_5d", float("nan")),
            "mf1_slope_pct_10d": market_raw.get("mf1_slope_pct_10d", float("nan")),
            "mf1_slope_pct_20d": market_raw.get("mf1_slope_pct_20d", float("nan")),
            # MF2 布林位置
            "mf2_boll_pct": market_raw.get("mf2_boll_pct", float("nan")),
            "mf2_boll_pct_10d": market_raw.get("mf2_boll_pct_10d", float("nan")),
            "mf2_boll_pct_20d": market_raw.get("mf2_boll_pct_20d", float("nan")),
            "mf2_boll_pct_30d": market_raw.get("mf2_boll_pct_30d", float("nan")),
            # MF3 成交量
            "mf3_vol_ratio": market_raw.get("mf3_vol_ratio", float("nan")),
            "mf3_vol_ratio_5d": market_raw.get("mf3_vol_ratio_5d", float("nan")),
            "mf3_vol_ratio_10d": market_raw.get("mf3_vol_ratio_10d", float("nan")),
            "mf3_vol_ratio_20d": market_raw.get("mf3_vol_ratio_20d", float("nan")),
            # MF4 动量
            "mf4_momentum_3d": market_raw.get("mf4_momentum_3d", float("nan")),
            "mf4_momentum_5d": market_raw.get("mf4_momentum_5d", float("nan")),
            "mf4_momentum_10d": market_raw.get("mf4_momentum_10d", float("nan")),
            # MF5 波动率（多窗口格式 {w}d_{w}d，与 MarketFactors 产出一致）
            "mf5_atr_ratio": market_raw.get("mf5_atr_ratio", float("nan")),
            "mf5_atr_ratio_10d_10d": market_raw.get("mf5_atr_ratio_10d_10d", float("nan")),
            "mf5_atr_ratio_20d_20d": market_raw.get("mf5_atr_ratio_20d_20d", float("nan")),
            "mf5_atr_ratio_30d_30d": market_raw.get("mf5_atr_ratio_30d_30d", float("nan")),
        }
        await self.database_service.replace_one(
            COL_FACTOR_MARKET, {"trade_date": trade_date}, doc, upsert=True
        )
        return 1

    async def _store_sector(self, trade_date: str, raw_result: Dict) -> int:
        from zstock.data_management.query_service import COL_FACTOR_SECTOR

        sector_raw = raw_result.get("sector_raw", {})
        if not sector_raw:
            logger.warning(f"{trade_date} 无 M2 原始值，跳过")
            return 0

        sector_names = sector_raw.get("sector_names", {})
        f21 = sector_raw.get("f21_rps", {})
        f21_10 = sector_raw.get("f21_rps_10d", {})
        f21_20 = sector_raw.get("f21_rps_20d", f21)
        f21_60 = sector_raw.get("f21_rps_60d", {})
        f22 = sector_raw.get("f22_main_flow", {})
        f23 = sector_raw.get("f23_limit_up_density", {})
        f24 = sector_raw.get("f24_max_consecutive", {})
        f25 = sector_raw.get("f25_volume_slope", {})
        f25_3 = sector_raw.get("f25_volume_slope_3d", {})
        f25_5 = sector_raw.get("f25_volume_slope_5d", f25)
        f25_10 = sector_raw.get("f25_volume_slope_10d", {})
        f26 = sector_raw.get("f26_volume_growth", {})
        f26_5 = sector_raw.get("f26_volume_growth_5d", f26)
        f26_20 = sector_raw.get("f26_volume_growth_20d", {})
        f28 = sector_raw.get("f28_consistency", {})
        f27 = sector_raw.get("f27_new_high_ratio", {})
        f29 = sector_raw.get("f29_sector_breadth", {})
        f30 = sector_raw.get("f30_sector_concentration", {})

        all_sector_codes = (
            set(f21)
            | set(f21_10)
            | set(f21_20)
            | set(f21_60)
            | set(f22)
            | set(f23)
            | set(f24)
            | set(f25)
            | set(f25_3)
            | set(f25_5)
            | set(f25_10)
            | set(f26)
            | set(f26_5)
            | set(f26_20)
            | set(f28)
            | set(f27)
            | set(f29)
            | set(f30)
        )
        docs = []
        for sc in all_sector_codes:
            docs.append(
                {
                    "trade_date": trade_date,
                    "sector_code": sc,
                    "sector_name": sector_names.get(sc, ""),
                    "f21_rps_10d": f21_10.get(sc, float("nan")),
                    "f21_rps_20d": f21_20.get(sc, float("nan")),
                    "f21_rps_60d": f21_60.get(sc, float("nan")),
                    "f22_main_flow": f22.get(sc, 0.0),
                    "f23_limit_up_density": f23.get(sc, 0.0),
                    "f24_max_consecutive": f24.get(sc, 0),
                    "f25_volume_slope_3d": f25_3.get(sc, float("nan")),
                    "f25_volume_slope_5d": f25_5.get(sc, float("nan")),
                    "f25_volume_slope_10d": f25_10.get(sc, float("nan")),
                    "f26_volume_growth_5d": f26_5.get(sc, float("nan")),
                    "f26_volume_growth_20d": f26_20.get(sc, float("nan")),
                    "f28_consistency": f28.get(sc, 0),
                    "f27_new_high_ratio": f27.get(sc, float("nan")),
                    "f29_sector_breadth": f29.get(sc, float("nan")),
                    "f30_sector_concentration": f30.get(sc, float("nan")),
                }
            )

        if docs:
            from pymongo import ReplaceOne
            ops = [
                ReplaceOne(
                    {"trade_date": trade_date, "sector_code": doc["sector_code"]},
                    doc,
                    upsert=True,
                )
                for doc in docs
            ]
            await self.database_service.db[COL_FACTOR_SECTOR].bulk_write(
                ops, ordered=False
            )
        return len(docs)

    async def _store_dragon(
        self,
        trade_date: str,
        raw_result: Dict,
        stock_infos: Dict[str, Dict],
    ) -> int:
        from zstock.data_management.query_service import COL_FACTOR_DRAGON

        dragon_raw_by_sector = raw_result.get("dragon_raw_by_sector", {})
        if not dragon_raw_by_sector:
            logger.warning(f"{trade_date} 无 M3 原始值，跳过")
            return 0

        # 基本面因子 f39/f40 已在 DragonFactors 组装时写入 dragon_raw
        docs = []
        for sector_code, dragon_raw in dragon_raw_by_sector.items():
            for code, raw_dict in dragon_raw.items():
                stock_name = stock_infos.get(code, {}).get("name", "")

                docs.append(
                    {
                        "trade_date": trade_date,
                        "code": code,
                        "stock_name": stock_name,
                        "sector_code": sector_code,
                        # F3.1b RPS分位
                        "f31b_rps_percentile": raw_dict.get(
                            "f31b_rps_percentile", float("nan")
                        ),
                        # F3.1 超额收益（多窗口）
                        "f31_excess_return": raw_dict.get(
                            "f31_excess_return", float("nan")
                        ),
                        "f31_excess_return_5d": raw_dict.get(
                            "f31_excess_return_5d", float("nan")
                        ),
                        "f31_excess_return_10d": raw_dict.get(
                            "f31_excess_return_10d", float("nan")
                        ),
                        "f31_excess_return_15d": raw_dict.get(
                            "f31_excess_return_15d", float("nan")
                        ),
                        "f31_excess_return_20d": raw_dict.get(
                            "f31_excess_return_20d", float("nan")
                        ),
                        # F3.2 成交额
                        "f32_amount": raw_dict.get("f32_amount", float("nan")),
                        # F3.3 连板基因
                        "f33_consecutive_boards": raw_dict.get(
                            "f33_consecutive_boards", 0
                        ),
                        # F3.4 量价共振（多窗口）
                        "f34_resonance_pct": raw_dict.get(
                            "f34_resonance_pct", float("nan")
                        ),
                        "f34_resonance_pct_3d": raw_dict.get(
                            "f34_resonance_pct_3d", float("nan")
                        ),
                        "f34_resonance_pct_5d": raw_dict.get(
                            "f34_resonance_pct_5d", float("nan")
                        ),
                        "f34_resonance_pct_10d": raw_dict.get(
                            "f34_resonance_pct_10d", float("nan")
                        ),
                        # F3.5 布林趋势
                        "f35_bollinger_trend": raw_dict.get(
                            "f35_bollinger_trend", float("nan")
                        ),
                        "f35_bollinger_pass": raw_dict.get(
                            "f35_bollinger_pass", 0.0
                        ),
                        # P3 F3.6 辨识度溢价 + F3.7 相对强度
                        "f36_identity_premium": raw_dict.get(
                            "f36_identity_premium", float("nan")
                        ),
                        "f37_relative_strength": raw_dict.get(
                            "f37_relative_strength", float("nan")
                        ),
                        # F3.8 换手率异动
                        "f38_turnover_anomaly": raw_dict.get(
                            "f38_turnover_anomaly", float("nan")
                        ),
                        # F3.9 PB (市净率, 负极性)
                        "f39_pb": raw_dict.get("f39_pb", float("nan")),
                        # F3.10 HolderChange (股东数变化率, 负极性)
                        "f40_holder_change": raw_dict.get(
                            "f40_holder_change", float("nan")
                        ),
                    }
                )

        if docs:
            from pymongo import ReplaceOne
            ops = [
                ReplaceOne(
                    {"trade_date": trade_date, "code": doc["code"], "sector_code": doc["sector_code"]},
                    doc,
                    upsert=True,
                )
                for doc in docs
            ]
            await self.database_service.db[COL_FACTOR_DRAGON].bulk_write(
                ops, ordered=False
            )
        return len(docs)

    async def _store_force(
        self,
        trade_date: str,
        raw_result: Dict,
        stock_infos: Dict[str, Dict],
    ) -> int:
        from zstock.data_management.query_service import COL_FACTOR_FORCE

        force_raw = raw_result.get("force_raw", [])
        if not force_raw:
            logger.warning(f"{trade_date} 无 M4 原始值，跳过")
            return 0

        docs = []
        for c in force_raw:
            code = c.get("code", "")
            stock_name = stock_infos.get(code, {}).get("name", "")
            docs.append(
                {
                    "trade_date": trade_date,
                    "code": code,
                    "stock_name": stock_name,
                    "sector_code": c.get("sector_code", ""),
                    # F_coop1 主力净流入占比
                    "fcoop1_main_net_ratio": c.get("fcoop1_main_net_ratio", 0.0),
                    # F_coop2 主散比
                    "fcoop2_main_retail_ratio": c.get(
                        "fcoop2_main_retail_ratio", 0.0
                    ),
                    # F_coop3 持续性天数（多窗口）
                    "fcoop3_sustained_days": c.get("fcoop3_sustained_days", 0.0),
                    "fcoop3_sustained_days_3d": c.get(
                        "fcoop3_sustained_days_3d", float("nan")
                    ),
                    "fcoop3_sustained_days_5d": c.get(
                        "fcoop3_sustained_days_5d", float("nan")
                    ),
                    "fcoop3_sustained_days_10d": c.get(
                        "fcoop3_sustained_days_10d", float("nan")
                    ),
                    # F_coop4 换手率质量
                    "fcoop4_turnover_quality": c.get(
                        "fcoop4_turnover_quality", 0.0
                    ),
                    # 【新因子】F_coop5 主力流入加速度
                    "fcoop5_main_flow_acceleration": c.get(
                        "fcoop5_main_flow_acceleration", float("nan")
                    ),
                    # 【P0新因子】fcoop6 主力进攻强度
                    "fcoop6_main_force_aggression": c.get(
                        "fcoop6_main_force_aggression", float("nan")
                    ),
                    # 【P0新因子】fcoop7 超大单净占比
                    "fcoop7_super_large_net_ratio": c.get(
                        "fcoop7_super_large_net_ratio", float("nan")
                    ),
                    # 【P0新因子】fcoop8 主力净流入5日趋势
                    "fcoop8_main_flow_trend_5d": c.get(
                        "fcoop8_main_flow_trend_5d", float("nan")
                    ),
                    # 【新因子】龙头持续性：近5日未跌停天数
                    "dragon_consistency_5d": c.get(
                        "dragon_consistency_5d", float("nan")
                    ),
                    # 【A级因子1】资金价格背离度
                    "f_power_divergence": c.get(
                        "f_power_divergence", float("nan")
                    ),
                    # 【A级因子2】主力执着度
                    "f_main_force_persistence": c.get(
                        "f_main_force_persistence", float("nan")
                    ),
                    # 【A级因子3】超涨反转信号
                    "f_mean_reversion_signal": c.get(
                        "f_mean_reversion_signal", float("nan")
                    ),
                    # P1a 龙虎榜加分
                    "longhu_board_bonus": c.get("longhu_board_bonus", 0.0),
                }
            )

        if docs:
            from pymongo import ReplaceOne
            ops = [
                ReplaceOne(
                    {"trade_date": trade_date, "code": doc["code"]},
                    doc,
                    upsert=True,
                )
                for doc in docs
            ]
            await self.database_service.db[COL_FACTOR_FORCE].bulk_write(
                ops, ordered=False
            )
        return len(docs)

    async def _gen_trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """OHLCV∩L2 且当日 OHLCV 股票数 >= 1000。"""
        from zstock.common.utils.common_utils import normalize_date
        from zstock.data_management.query_service import (
            COL_CAPITAL_FLOW,
            COL_OHLCV,
            PERIOD_L2_DAILY,
        )

        start = normalize_date(start_date)
        end = normalize_date(end_date)
        db = self.database_service.db

        pipe = [
            {
                "$match": {
                    "period": "D",
                    "trade_date": {"$gte": start, "$lte": end},
                }
            },
            {"$group": {"_id": "$trade_date", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gte": 1000}}},
            {"$sort": {"_id": 1}},
        ]
        oh_dates = {
            r["_id"]
            async for r in db[COL_OHLCV].aggregate(pipe, allowDiskUse=True)
        }
        cf_dates = set(
            await db[COL_CAPITAL_FLOW].distinct(
                "trade_date",
                {
                    "period": PERIOD_L2_DAILY,
                    "trade_date": {"$gte": start, "$lte": end},
                },
            )
        )
        dates = sorted(oh_dates & cf_dates)
        logger.info(
            f"交易日筛选: ohlcv合格={len(oh_dates)}, L2={len(cf_dates)}, "
            f"交集={len(dates)} ({dates[0] if dates else '-'} ~ "
            f"{dates[-1] if dates else '-'})"
        )
        return dates


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="预计算因子原始值并存储到 MongoDB（手动批处理脚本）"
    )
    parser.add_argument("--start", required=False, help="开始日期 YYYY-MM-DD（区间模式）")
    parser.add_argument("--end", required=False, help="结束日期 YYYY-MM-DD（区间模式）")
    parser.add_argument("--date", required=False, help="单日日期 YYYY-MM-DD（单日模式）")
    parser.add_argument(
        "--lookback",
        type=int,
        default=120,
        help="数据回看日历天数（默认 120，覆盖 RPS60）",
    )
    parser.add_argument(
        "--resource-fraction",
        type=float,
        default=None,
        help="资源占用比例 0.1~1.0（默认 0.5=最多占一半 CPU/内存）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="因子计算进程数（默认按资源预算，会被 --resource-fraction 封顶）",
    )
    parser.add_argument(
        "--load-workers",
        type=int,
        default=None,
        help="MongoDB 预加载并发数（默认按资源预算）",
    )
    parser.add_argument(
        "--query-workers",
        type=int,
        default=None,
        help="MongoDB 子查询并发数（默认按资源预算）",
    )
    args = parser.parse_args()

    if not args.date and not (args.start and args.end):
        parser.error("必须指定 --date（单日）或 --start + --end（区间）")

    try:
        from app.core import database as db_module

        await db_module.db_manager.init_mongodb()
    except Exception as e:
        logging.error(f"数据库初始化失败: {e}")
        return 1

    try:
        from zstock.data_management.query_service import get_data_query_service

        await get_data_query_service().ensure_indexes()
    except Exception as e:
        logging.warning(f"索引创建失败（非致命）: {e}")

    service = FactorPrecomputeService(
        compute_workers=args.workers,
        load_workers=args.load_workers,
        query_workers=args.query_workers,
        resource_fraction=args.resource_fraction,
    )
    try:
        if args.date:
            logging.info(f"单日预计算: {args.date}")
            result = await service.precompute_single_date(args.date, args.lookback)
        else:
            logging.info(f"区间预计算: {args.start} ~ {args.end}")
            result = await service.precompute_date_range(
                args.start, args.end, args.lookback
            )
        logging.info(f"完成: {result}")
        return 0
    except Exception as e:
        logging.error(f"预计算失败: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    sys.exit(asyncio.run(main()))
