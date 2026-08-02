"""
zstock 数据层校验：MongoDB 集合完整性 + query_service 接口冒烟。

要求：
- 本机 MongoDB 已启动
- 查询接口测试可选依赖 miniQMT（库内有缓存时可仅用 MongoDB）

用法：
    python -m zstock.data_management.test.verify
    python zstock/data_management/test/verify.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_DASHED_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# 各集合最低条数门槛（保证“有数据”，因子表按日存，门槛可较低）
_MIN_COUNTS: Dict[str, int] = {
    "zstock_stock_info": 1000,
    "zstock_ohlcv": 10000,
    "zstock_capital_flow": 10000,
    "zstock_sector": 50,
    "zstock_factor_market": 1,
    "zstock_factor_sector": 50,
    "zstock_factor_dragon": 100,
    "zstock_factor_force": 100,
}

# query_service 声明的核心集合（必须全部非空）
_REQUIRED_COLS = (
    "zstock_stock_info",
    "zstock_ohlcv",
    "zstock_capital_flow",
    "zstock_sector",
    "zstock_factor_market",
    "zstock_factor_sector",
    "zstock_factor_dragon",
    "zstock_factor_force",
)


class ZstockDataVerifier:
    """校验所有 zstock_* 集合有数据，并冒烟测试 DataQueryService。"""

    def __init__(self) -> None:
        self.ok = True
        self.db = None
        self.query_service = None
        self.codes: List[str] = []
        self.sectors: List[Dict[str, Any]] = []
        self._failures: List[str] = []

    def _fail(self, msg: str) -> None:
        self.ok = False
        self._failures.append(msg)
        print(f"  ❌ {msg}")

    def _pass(self, msg: str) -> None:
        print(f"  ✅ {msg}")

    def _warn(self, msg: str) -> None:
        print(f"  ⚠️ {msg}")

    async def setup(self) -> None:
        from app.core import database as db_module
        from zstock.data_management.query_service import DataQueryService

        print("📌 初始化 MongoDB...")
        await db_module.db_manager.init_mongodb()
        db_module.mongo_client = db_module.db_manager.mongo_client
        db_module.mongo_db = db_module.db_manager.mongo_db
        self.db = db_module.db_manager.mongo_db
        self.query_service = DataQueryService()
        await self.query_service.ensure_indexes()
        self._pass("MongoDB / 查询服务就绪")

    async def teardown(self) -> None:
        from app.core import database as db_module

        await db_module.db_manager.close_connections()
        print("✅ 已断开 MongoDB")

    # ─────────────────── 集合完整性 ───────────────────

    async def verify_all_zstock_collections(self) -> None:
        """保证所有 zstock_* 表都有数据（含未来新增集合）。"""
        print("\n📌 校验所有 zstock_* 集合有数据 ...")
        assert self.db is not None

        all_names = await self.db.list_collection_names()
        zstock_cols = sorted(n for n in all_names if n.startswith("zstock_"))
        print(f"  发现 zstock_* 集合 {len(zstock_cols)} 个: {zstock_cols}")

        missing_required = [c for c in _REQUIRED_COLS if c not in zstock_cols]
        if missing_required:
            self._fail(f"缺少核心集合: {missing_required}")

        for col_name in zstock_cols:
            await self._verify_one_collection(col_name)

        # 核心集合即便尚未出现在 list 中也要报失败（刚建库等）
        for col_name in _REQUIRED_COLS:
            if col_name not in zstock_cols:
                continue
            # 已在上面遍历；此处补一道门槛
            min_n = _MIN_COUNTS.get(col_name, 1)
            cnt = await self.db[col_name].estimated_document_count()
            if cnt < min_n:
                self._fail(f"{col_name} 条数 {cnt} < 最低要求 {min_n}")

    async def _verify_one_collection(self, col_name: str) -> None:
        assert self.db is not None
        col = self.db[col_name]
        cnt = await col.estimated_document_count()
        min_n = _MIN_COUNTS.get(col_name, 1)
        if cnt <= 0:
            self._fail(f"{col_name}: 空表 (0 条)")
            return
        if cnt < min_n:
            self._fail(f"{col_name}: {cnt} 条 < 最低要求 {min_n}")
            return

        sample = await col.find_one({}, {"_id": 0})
        extra = ""
        if sample and "trade_date" in sample:
            date_ok, date_msg = await self._check_trade_date_format(col_name)
            if not date_ok:
                self._fail(f"{col_name}: {date_msg}")
                return
            rng = await self._trade_date_range(col_name)
            extra = f", trade_date={rng[0]}~{rng[1]}" if rng else f", {date_msg}"
        elif sample:
            keys = list(sample.keys())[:6]
            extra = f", sample_keys={keys}"

        self._pass(f"{col_name}: {cnt} 条{extra}")

    async def _trade_date_range(self, col_name: str) -> Optional[Tuple[str, str]]:
        assert self.db is not None
        dates = await self.db[col_name].distinct("trade_date")
        dates = sorted(d for d in dates if isinstance(d, str) and d)
        if not dates:
            return None
        return dates[0], dates[-1]

    async def _check_trade_date_format(self, col_name: str) -> Tuple[bool, str]:
        """库内 trade_date 必须全是 YYYY-MM-DD，禁止 YYYYMMDD。"""
        assert self.db is not None
        col = self.db[col_name]
        compact = await col.count_documents({"trade_date": {"$regex": r"^\d{8}$"}})
        if compact > 0:
            return False, f"存在 {compact} 条紧凑日期 YYYYMMDD，应统一为 YYYY-MM-DD"
        dashed = await col.count_documents(
            {"trade_date": {"$regex": r"^\d{4}-\d{2}-\d{2}$"}}
        )
        if dashed <= 0:
            return False, "未找到 YYYY-MM-DD 格式的 trade_date"
        return True, f"date_fmt=YYYY-MM-DD({dashed})"

    async def verify_collection_details(self) -> None:
        """补充业务语义检查（主板/成分股/资金流 period 等）。"""
        print("\n📌 补充业务字段检查 ...")
        assert self.db is not None
        from zstock.data_management.query_service import (
            COL_STOCK_INFO,
            COL_SECTOR,
            COL_CAPITAL_FLOW,
            COL_OHLCV,
            PERIOD_L2_DAILY,
        )

        checks = [
            ("stock_info 主板", COL_STOCK_INFO, {"is_mainboard": True}),
            ("sector 含成分股", COL_SECTOR, {"stocks": {"$exists": True, "$not": {"$size": 0}}}),
            ("ohlcv 日线 period=D", COL_OHLCV, {"period": "D"}),
            ("capital_flow L2_daily", COL_CAPITAL_FLOW, {"period": PERIOD_L2_DAILY}),
            ("ohlcv 含换手率>0", COL_OHLCV, {"turnover_rate": {"$gt": 0}}),
            (
                "factor_dragon 多窗口字段",
                "zstock_factor_dragon",
                {"f31_excess_return_10d": {"$exists": True}},
            ),
        ]
        for label, coll, query in checks:
            cnt = await self.db[coll].count_documents(query)
            if cnt > 0:
                self._pass(f"{label}: {cnt}")
            else:
                self._fail(f"{label}: 0 条")

    # ─────────────────── query_service 冒烟 ───────────────────

    async def _latest_trade_date(self, col_name: str) -> str:
        assert self.db is not None
        doc = await self.db[col_name].find_one(
            {}, {"trade_date": 1, "_id": 0}, sort=[("trade_date", -1)]
        )
        if doc and doc.get("trade_date"):
            return str(doc["trade_date"])
        return datetime.now().strftime("%Y-%m-%d")

    async def verify_query_service(self) -> None:
        print("\n📌 测试 DataQueryService 接口 ...")
        assert self.query_service is not None

        # get_all_stocks
        try:
            all_stock_docs, source = await self.query_service.get_all_stocks()
            self.codes = [d["code"] for d in all_stock_docs if d.get("code")]
            if not self.codes:
                self._fail("get_all_stocks 返回空")
            else:
                self._pass(
                    f"get_all_stocks: {len(all_stock_docs)} 只 (来源 {source}), "
                    f"主板={sum(1 for d in all_stock_docs if d.get('is_mainboard'))}"
                )
        except Exception as e:
            self._fail(f"get_all_stocks: {e}")

        # get_stock_info
        try:
            info, source = await self.query_service.get_stock_info("600000")
            if not info.get("code"):
                self._fail("get_stock_info(600000) 无 code")
            else:
                self._pass(
                    f"get_stock_info: {info.get('code')} {info.get('name')} (来源 {source})"
                )
        except Exception as e:
            self._fail(f"get_stock_info: {e}")

        ohlcv_end = await self._latest_trade_date("zstock_ohlcv")
        ohlcv_start = ohlcv_end  # 单日冒烟即可
        # 尽量取近端三天窗口：用库内倒数第 3 个交易日
        try:
            dates = await self.db["zstock_ohlcv"].distinct("trade_date")
            dates = sorted(d for d in dates if isinstance(d, str))
            if len(dates) >= 3:
                ohlcv_start, ohlcv_end = dates[-3], dates[-1]
        except Exception:
            pass

        try:
            df, source = await self.query_service.get_ohlcv(
                "600000", ohlcv_start, ohlcv_end
            )
            if df is None or df.empty:
                self._fail(f"get_ohlcv(600000, {ohlcv_start}~{ohlcv_end}) 空")
            else:
                has_tr = "turnover_rate" in df.columns and float(df["turnover_rate"].fillna(0).sum()) > 0
                self._pass(
                    f"get_ohlcv: {len(df)} 行 (来源 {source}), turnover_rate={'有' if has_tr else '无'}"
                )
        except Exception as e:
            self._fail(f"get_ohlcv: {e}")

        try:
            batch_codes = self.codes[:5] if len(self.codes) >= 5 else self.codes[:2]
            result = await self.query_service.get_ohlcv_batch(
                batch_codes, ohlcv_start, ohlcv_end
            )
            if not result:
                self._fail("get_ohlcv_batch 空")
            else:
                self._pass(f"get_ohlcv_batch: {len(result)} 只有数据")
        except Exception as e:
            self._fail(f"get_ohlcv_batch: {e}")

        flow_date = await self._latest_trade_date("zstock_capital_flow")
        try:
            flow, source = await self.query_service.get_capital_flow(
                "600000", flow_date
            )
            if not flow:
                self._fail(f"get_capital_flow(600000, {flow_date}) 空")
            else:
                self._pass(
                    f"get_capital_flow: trade_date={flow.get('trade_date')} (来源 {source})"
                )
                td = str(flow.get("trade_date") or "")
                if td and not _DASHED_DATE.match(td):
                    self._fail(f"get_capital_flow 返回日期非 YYYY-MM-DD: {td}")
        except Exception as e:
            self._fail(f"get_capital_flow: {e}")

        try:
            batch_codes_flow = self.codes[:10] if len(self.codes) >= 10 else self.codes
            result = await self.query_service.get_capital_flow_recent_days(
                batch_codes_flow, flow_date, days=3
            )
            if not result:
                self._fail("get_capital_flow_recent_days 空")
            else:
                self._pass(f"get_capital_flow_recent_days: {len(result)} 只有数据")
        except Exception as e:
            self._fail(f"get_capital_flow_recent_days: {e}")

        try:
            self.sectors, source = await self.query_service.get_sector_list()
            if not self.sectors:
                self._fail("get_sector_list 空")
            else:
                self._pass(f"get_sector_list: {len(self.sectors)} 个 (来源 {source})")
        except Exception as e:
            self._fail(f"get_sector_list: {e}")

        try:
            test_sector = (
                self.sectors[0]["sector_code"] if self.sectors else "银行"
            )
            rows, source = await self.query_service.get_sector_stocks(test_sector)
            if not rows:
                self._fail(f"get_sector_stocks({test_sector}) 空")
            else:
                self._pass(
                    f"get_sector_stocks({test_sector}): {len(rows)} 只 (来源 {source})"
                )
        except Exception as e:
            self._fail(f"get_sector_stocks: {e}")

        try:
            batch_sectors = [
                s["sector_code"] for s in self.sectors[:5]
            ] if self.sectors else []
            result = await self.query_service.get_sector_stocks_batch(batch_sectors)
            if not result:
                self._fail("get_sector_stocks_batch 空")
            else:
                self._pass(f"get_sector_stocks_batch: {len(result)} 个板块")
        except Exception as e:
            self._fail(f"get_sector_stocks_batch: {e}")

        # 因子预计算读取
        try:
            factor_date = await self._latest_trade_date("zstock_factor_market")
            mkt = await self.query_service.get_factor_market(factor_date)
            sec = await self.query_service.get_factor_sectors(factor_date)
            dragons = await self.query_service.get_factor_dragons(factor_date)
            forces = await self.query_service.get_factor_forces(factor_date)
            if not mkt:
                self._fail(f"get_factor_market({factor_date}) 空")
            else:
                self._pass(f"get_factor_market({factor_date}): ok")
            if not sec:
                self._fail(f"get_factor_sectors({factor_date}) 空")
            else:
                self._pass(f"get_factor_sectors({factor_date}): {len(sec)} 条")
            if not dragons:
                self._fail(f"get_factor_dragons({factor_date}) 空")
            else:
                self._pass(f"get_factor_dragons({factor_date}): {len(dragons)} 条")
            if not forces:
                self._fail(f"get_factor_forces({factor_date}) 空")
            else:
                self._pass(f"get_factor_forces({factor_date}): {len(forces)} 条")
        except Exception as e:
            self._fail(f"factor query: {e}")

    # ─────────────────── 入口 ───────────────────

    async def run(self) -> bool:
        print("\n" + "=" * 70)
        print("✅ zstock 数据层校验（集合完整性 + query_service）")
        print("=" * 70 + "\n")

        await self.setup()
        try:
            await self.verify_all_zstock_collections()
            await self.verify_collection_details()
            await self.verify_query_service()
        finally:
            print("\n📌 清理 ...")
            await self.teardown()

        print("\n" + "=" * 70)
        if self.ok:
            print("🎉 全部测试通过！")
        else:
            print("❌ 部分测试失败：")
            for f in self._failures:
                print(f"   - {f}")
        print("=" * 70 + "\n")
        return self.ok


async def main() -> bool:
    return await ZstockDataVerifier().run()


if __name__ == "__main__":
    # Windows 控制台默认 GBK，避免 emoji/中文打印崩掉
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
