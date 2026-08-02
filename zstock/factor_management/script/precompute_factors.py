"""
因子预计算脚本（手动批处理，不属于在线策略流程）

职责：
1. 从 MongoDB 加载 OHLCV / 资金流
2. 调用 pipeline.compute_all_factor_raw 计算全市场因子原始值
3. 写入 zstock_factor_* 集合

用法：
    python -m zstock.factor_management.script.precompute_factors --start 2026-01-01 --end 2026-06-30
    python -m zstock.factor_management.script.precompute_factors --date 2026-05-15 --lookback 90

打分 / 回测请用 CrossSectionStrategyPipeline.score_signals，不要走本脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for h in list(logging.getLogger().handlers):
    logging.getLogger().removeHandler(h)
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


class FactorPrecomputeService:
    """因子预计算：全市场原始值入库（仅供本脚本使用）。"""

    def __init__(self) -> None:
        from zstock.data_management.database_service import get_database_service
        from zstock.data_management.query_service import get_data_query_service
        from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

        self.pipeline = CrossSectionStrategyPipeline()
        self.database_service = get_database_service()
        self.query_service = get_data_query_service()
        logger.info("FactorPrecomputeService 初始化完成")

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
        raw_result = await self.pipeline.compute_all_factor_raw(**data)
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
        trade_dates = await self._gen_trade_dates(start_date, end_date)
        if not trade_dates:
            raise ValueError(f"{start_date} ~ {end_date} 无可用交易日")

        preload = await self._preload_range_data(start_date, end_date, lookback_days)

        total = {"market": 0, "sector": 0, "dragon": 0, "force": 0}
        success = 0
        failed = 0

        for i, td in enumerate(trade_dates, 1):
            logger.info(f"[{i}/{len(trade_dates)}] 预计算 {td}")
            try:
                t0 = perf_counter()
                data = self._slice_preloaded_data(preload, td, lookback_days)
                t1 = perf_counter()
                raw_result = await self.pipeline.compute_all_factor_raw(
                    **data,
                    assume_sorted=True,
                    skip_scores=True,
                    filtered_sectors=preload["filtered_sectors"],
                    filtered_stocks_set=preload["filtered_stocks_set"],
                )
                t2 = perf_counter()
                counts = await self._store_all(
                    td,
                    raw_result,
                    preload["stock_infos"],
                )
                t3 = perf_counter()
                logger.info(
                    f"{td} 完成: {counts} | "
                    f"slice={t1 - t0:.2f}s compute={t2 - t1:.2f}s store={t3 - t2:.2f}s"
                )
                for k in total:
                    total[k] += counts.get(k, 0)
                success += 1
            except Exception as e:
                logger.error(f"{td} 预计算失败: {e}")
                import traceback

                logger.error(traceback.format_exc())
                failed += 1

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
        stock_ohlcv_full, stock_flow_full, index_ohlcv_full = await asyncio.gather(
            self.query_service.get_ohlcv_batch(all_stocks, window_start, end),
            self.query_service.get_capital_flow_range(all_stocks, window_start, end),
            self.query_service.get_ohlcv_batch(["399300"], window_start, end),
        )

        # 一次性排序，后续每日 assume_sorted=True
        from zstock.common.utils.common_utils import ensure_ohlcv_sorted

        t_sort = perf_counter()
        for code, df in list(stock_ohlcv_full.items()):
            if df is None or df.empty:
                continue
            if "trade_date" in df.columns:
                df = ensure_ohlcv_sorted(df)
                df["trade_date"] = df["trade_date"].astype(str)
                stock_ohlcv_full[code] = df
        for code, df in list(index_ohlcv_full.items()):
            if df is None or df.empty:
                continue
            if "trade_date" in df.columns:
                df = ensure_ohlcv_sorted(df)
                df["trade_date"] = df["trade_date"].astype(str)
                index_ohlcv_full[code] = df
        logger.info(f"OHLCV 预排序完成: {perf_counter() - t_sort:.2f}s")

        # 过滤结果跨日不变，只算一次
        filtered_sectors = self.pipeline.prefilters.filter_sectors(sector_list)
        main_board = await self.pipeline.prefilters.apply_main_board_filter(
            all_stocks, stock_infos
        )
        filtered_stocks_set = set(self.pipeline.prefilters.filter_stocks(main_board))

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

        flow_dates_index = {
            code: [str(d.get("trade_date", "")) for d in rows]
            for code, rows in stock_flow_full.items()
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
            ohlcv_only=True,
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
            "flow_dates_index": flow_dates_index,
            "ohlcv_dates_index": ohlcv_dates_index,
            "index_ohlcv_full": index_ohlcv_full,
            "index_dates_index": index_dates_index,
            "sector_ohlcv_full": sector_ohlcv_full,
            "sector_dates_index": sector_dates_index,
            "flow_days": flow_days,
            "index_name": "沪深 300",
        }

    def _slice_preloaded_data(
        self,
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
            "mf1_slope_pct": market_raw.get("mf1_slope_pct", float("nan")),
            "mf2_boll_pct": market_raw.get("mf2_boll_pct", float("nan")),
            "mf3_vol_ratio": market_raw.get("mf3_vol_ratio", float("nan")),
            "mf4_momentum_3d": market_raw.get("mf4_momentum_3d", float("nan")),
            "mf4_momentum_5d": market_raw.get("mf4_momentum_5d", float("nan")),
            "mf4_momentum_10d": market_raw.get("mf4_momentum_10d", float("nan")),
            "mf5_atr_ratio": market_raw.get("mf5_atr_ratio", float("nan")),
        }
        await self.database_service.delete_many(
            COL_FACTOR_MARKET, {"trade_date": trade_date}
        )
        await self.database_service.insert_one(COL_FACTOR_MARKET, doc)
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
        )
        docs = []
        for sc in all_sector_codes:
            docs.append(
                {
                    "trade_date": trade_date,
                    "sector_code": sc,
                    "sector_name": sector_names.get(sc, ""),
                    "f21_rps": f21_20.get(sc, f21.get(sc, float("nan"))),
                    "f21_rps_10d": f21_10.get(sc, float("nan")),
                    "f21_rps_20d": f21_20.get(sc, float("nan")),
                    "f21_rps_60d": f21_60.get(sc, float("nan")),
                    "f22_main_flow": f22.get(sc, 0.0),
                    "f23_limit_up_density": f23.get(sc, 0.0),
                    "f24_max_consecutive": f24.get(sc, 0),
                    "f25_volume_slope": f25_5.get(sc, f25.get(sc, float("nan"))),
                    "f25_volume_slope_3d": f25_3.get(sc, float("nan")),
                    "f25_volume_slope_5d": f25_5.get(sc, float("nan")),
                    "f25_volume_slope_10d": f25_10.get(sc, float("nan")),
                }
            )

        await self.database_service.delete_many(
            COL_FACTOR_SECTOR, {"trade_date": trade_date}
        )
        if docs:
            await self.database_service.insert_many(COL_FACTOR_SECTOR, docs)
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
                        "f32_amount": raw_dict.get("f32_amount", float("nan")),
                        "f33_consecutive_boards": raw_dict.get(
                            "f33_consecutive_boards", 0
                        ),
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
                        "f35_bollinger_trend": raw_dict.get(
                            "f35_bollinger_trend", float("nan")
                        ),
                        "f35_bollinger_pass": raw_dict.get(
                            "f35_bollinger_pass", 0.0
                        ),
                    }
                )

        await self.database_service.delete_many(
            COL_FACTOR_DRAGON, {"trade_date": trade_date}
        )
        if docs:
            await self.database_service.insert_many(COL_FACTOR_DRAGON, docs)
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
                    "dragon_score": c.get("dragon_score", 0.0),
                    "fcoop1_main_net_ratio": c.get("fcoop1_main_net_ratio", 0.0),
                    "fcoop2_main_retail_ratio": c.get(
                        "fcoop2_main_retail_ratio", 0.0
                    ),
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
                    "fcoop4_turnover_quality": c.get(
                        "fcoop4_turnover_quality", 0.0
                    ),
                }
            )

        await self.database_service.delete_many(
            COL_FACTOR_FORCE, {"trade_date": trade_date}
        )
        if docs:
            await self.database_service.insert_many(COL_FACTOR_FORCE, docs)
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

    service = FactorPrecomputeService()
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
        logging.error(f"预计算失败: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(asyncio.run(main()))
