"""
手动数据同步脚本 — 基本面因子数据 (xtquant → MongoDB)

    zstock_fundamental_pershare — Pershareindex 全字段 (EPS/BPS/ROE/营收增长/利润增长/负债率)
    zstock_fundamental_holder   — 股东数 (Holdernum)

一次同步，长期复用。预计算时从 MongoDB 读取，不再依赖 xtdata。

用法：
    python -m zstock.data_management.script.sync_fundamental
    python -m zstock.data_management.script.sync_fundamental --start 2023-01-01 --end 2026-12-31
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# ─── MongoDB 集合名 ───
COL_FUNDAMENTAL_PERSHARE = "zstock_fundamental_pershare"
COL_FUNDAMENTAL_HOLDER = "zstock_fundamental_holder"

# ─── Pershareindex 字段映射 ───
# xtdata 字段名 → MongoDB 字段名 → 含义
PERSHARE_FIELDS = {
    "s_fa_bps": "bps",             # 每股净资产 → PB
    "s_fa_eps_basic": "eps",       # 基本每股收益 → PE
    "du_return_on_equity": "roe",  # 净资产收益率
    "inc_revenue_rate": "rev_growth",     # 营收增长率
    "du_profit_rate": "profit_growth",    # 净利润增长率
    "gear_ratio": "debt_ratio",           # 资产负债率
    "sales_gross_profit": "gross_margin", # 毛利率
}


def _parse_date_str(val) -> str | None:
    """稳健日期解析: 支持 '20240331', '2024-03-31', '20240331000000000' 等
    返回统一格式 'YYYY-MM-DD'"""
    if val is None:
        return None
    s = str(val).strip()
    if not s or len(s) < 8:
        return None
    digits = s.replace("-", "").replace("/", "").replace(" ", "")[:8]
    if len(digits) < 8:
        return None
    try:
        int(digits)  # 验证全是数字
    except ValueError:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _from_xt_code(xt_code: str) -> str:
    return xt_code.split(".")[0]


def _safe_float(val) -> float | None:
    """安全转 float，NaN/Inf/None 返回 None"""
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return v


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步基本面数据 (xtquant -> MongoDB)")
    parser.add_argument("--start", default="2023-01-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument(
        "--end", default=datetime.now().strftime("%Y-%m-%d"),
        help="结束日期 YYYY-MM-DD，默认今天",
    )
    return parser.parse_args()


async def main():
    args = _parse_args()
    start = args.start.replace("-", "")[:8]
    end = args.end.replace("-", "")[:8]

    # 1. 连接 MongoDB
    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()
    db = db_module.db_manager.mongo_db
    logger.info("MongoDB 已连接")

    # 2. 获取 xtdata
    from zstock.common.utils import xtquant_data_utils as xtu
    xtdata = xtu._get_xtdata()
    logger.info("xtdata 就绪")

    # 3. 获取全A股代码
    all_stocks = xtu.fetch_all_stocks()
    all_codes = [s["code"] for s in all_stocks if s.get("code")]
    xt_codes = [xtu.to_xt_code(c) for c in all_codes]
    logger.info(f"全市场 {len(xt_codes)} 只股票")

    # ── 先 drop 旧表 (全量同步, 无需 upsert) ──
    await db[COL_FUNDAMENTAL_PERSHARE].drop()
    await db[COL_FUNDAMENTAL_HOLDER].drop()
    logger.info("已清空旧表 (全量同步模式)")

    t0 = datetime.now()

    # ══════════════════════════════════════════════
    # [1] Pershareindex → 全字段 (EPS/BPS/ROE/增长/负债率/毛利率)
    # ══════════════════════════════════════════════
    logger.info("▶ [1/2] Pershareindex — 每股指标 (EPS/BPS/ROE/增长/负债率/毛利率)")

    xtdata.download_financial_data2(
        xt_codes, table_list=["Pershareindex"],
        start_time=start, end_time=end,
    )
    fin_data = xtdata.get_financial_data(
        xt_codes, table_list=["Pershareindex"],
        start_time=start, end_time=end,
        report_type="announce_time",
    )

    pershare_docs = []
    for xt_code, tables in fin_data.items():
        psh = tables.get("Pershareindex")
        if psh is None or len(psh) == 0:
            continue
        code = _from_xt_code(xt_code)
        for _, row in psh.iterrows():
            ann_date = _parse_date_str(row.get("m_anntime", ""))
            if ann_date is None:
                continue
            # 至少需要一个有效字段
            doc: dict = {"code": code, "ann_date": ann_date}
            has_any = False
            for xt_field, mongo_field in PERSHARE_FIELDS.items():
                val = _safe_float(row.get(xt_field))
                if val is not None:
                    doc[mongo_field] = val
                    has_any = True
            if has_any:
                pershare_docs.append(doc)

    codes_with_data = len(set(d["code"] for d in pershare_docs)) if pershare_docs else 0
    logger.info(f"  Pershareindex: {len(pershare_docs)} 条记录, {codes_with_data} 只股票")

    # 统计各字段覆盖率
    if pershare_docs:
        for mongo_field in PERSHARE_FIELDS.values():
            n = sum(1 for d in pershare_docs if mongo_field in d)
            logger.info(f"    {mongo_field}: {n}/{len(pershare_docs)} ({100*n//max(len(pershare_docs),1)}%)")

    if pershare_docs:
        BATCH = 5000
        for i in range(0, len(pershare_docs), BATCH):
            await db[COL_FUNDAMENTAL_PERSHARE].insert_many(pershare_docs[i:i+BATCH], ordered=False)
        logger.info(f"  ✓ 落库 {COL_FUNDAMENTAL_PERSHARE}: {len(pershare_docs)} 条")

    # ══════════════════════════════════════════════
    # [2] Holdernum → 股东数
    # ══════════════════════════════════════════════
    logger.info("▶ [2/2] Holdernum — 股东数")

    xtdata.download_financial_data2(
        xt_codes, table_list=["Holdernum"],
        start_time=start, end_time=end,
    )
    holder_data = xtdata.get_financial_data(
        xt_codes, table_list=["Holdernum"],
        start_time=start, end_time=end,
        report_type="announce_time",
    )

    holder_docs = []
    for xt_code, tables in holder_data.items():
        hld = tables.get("Holdernum")
        if hld is None or len(hld) == 0:
            continue
        code = _from_xt_code(xt_code)
        for _, row in hld.iterrows():
            ann_date = _parse_date_str(row.get("declareDate", ""))
            end_date = _parse_date_str(row.get("endDate", ""))
            if ann_date is None or end_date is None:
                continue
            sh = _safe_float(row.get("shareholder"))
            if sh is None or sh <= 0:
                continue
            doc = {
                "code": code,
                "ann_date": ann_date,       # 公告日 (PIT-safe) "YYYY-MM-DD"
                "end_date": end_date,       # 报告期 "YYYY-MM-DD"
                "shareholder": sh,
            }
            # 如果有股东数细分 (A股/H股), 也记录
            sh_a = _safe_float(row.get("shareholderA"))
            if sh_a is not None and sh_a > 0:
                doc["shareholder_a"] = sh_a
            holder_docs.append(doc)

    codes_with_holder = len(set(d["code"] for d in holder_docs)) if holder_docs else 0
    logger.info(f"  Holdernum: {len(holder_docs)} 条记录, {codes_with_holder} 只股票")

    if holder_docs:
        BATCH = 5000
        for i in range(0, len(holder_docs), BATCH):
            await db[COL_FUNDAMENTAL_HOLDER].insert_many(holder_docs[i:i+BATCH], ordered=False)
        logger.info(f"  ✓ 落库 {COL_FUNDAMENTAL_HOLDER}: {len(holder_docs)} 条")

    # ── 汇总 ──
    elapsed = (datetime.now() - t0).total_seconds()
    logger.info("=" * 60)
    logger.info(f"同步完成  耗时 {elapsed:.1f}s")
    logger.info(f"  {COL_FUNDAMENTAL_PERSHARE}: {len(pershare_docs)} 条 ({codes_with_data} 只)")
    logger.info(f"  {COL_FUNDAMENTAL_HOLDER}: {len(holder_docs)} 条 ({codes_with_holder} 只)")

    # ── 创建索引 (加速查询) ──
    logger.info("创建索引...")
    await db[COL_FUNDAMENTAL_PERSHARE].create_index([("code", 1), ("ann_date", 1)])
    await db[COL_FUNDAMENTAL_HOLDER].create_index([("code", 1), ("end_date", 1)])
    logger.info("索引创建完成")

    logger.info("=" * 60)

    await db_module.db_manager.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
