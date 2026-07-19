"""
手动数据同步脚本 — 一个 main 同步 1 张表

    zstock_ohlcv       — 日线 OHLCV

用法：
    python sync_ohlcv.py
"""
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List


import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# ─────────────────── 可调参数 ───────────────────
OHLCV_DAYS = 30          # 同步最近 N 天日线

# 需同步的指数列表（code, name）。
# 注意：沪深300 用 399300（深交所版），不用 000300（后者库里 0 条且与个股 code 易冲突）。
# 000016（上证50）不在此列——to_xt_code 会把 000016 映射成 .SZ，与平安银行撞 code。
INDEX_CODES = [
    ('399300', '沪深300'),
    ('399001', '深证成指'),
    ('399006', '创业板指'),
    ('899050', '北证50'),
]
# ────────────────────────────────────────────────

async def _persist_ohlcv(df: pd.DataFrame, period: str = 'D') -> None:
    if df is None or df.empty:
        return
    try:
        from pymongo import UpdateOne
        from app.core import database as db_module
        await db_module.db_manager.init_mongodb()
        db = db_module.db_manager.mongo_db
        ops = []
        for record in df.to_dict(orient='records'):
            code = record.get('code')
            td   = record.get('trade_date')
            if not code or not td:
                continue
            ops.append(UpdateOne(
                {'code': code, 'trade_date': td, 'period': period},
                {'$set': {**record, 'period': period}},
                upsert=True,
            ))
        if ops:
            from zstock.data_management.query_service import DataQueryService, COL_OHLCV
            await db[COL_OHLCV].bulk_write(ops, ordered=False)
            logger.info(f"💾 落库 {COL_OHLCV}({period}): {len(ops)} 条")
    except Exception as e:
        logger.error(f"ohlcv 落库失败: {e}")


def _enrich_turnover_rate(df: pd.DataFrame, codes: List[str]) -> pd.DataFrame:
    """为 OHLCV DataFrame 补充 turnover_rate 列（换手率，百分数）。

    换手率 = 成交量(手) × 10000 / 流通股本(股)。xtquant 日线 volume 单位为"手"，
    FloatVolume 单位为"股"：×100 把手换算成股、再 ×100 化为百分数，合计 ×10000。
    流通股本取 get_instrument_detail 当前快照，历史区间为近似值。
    结果为百分数（如 2.0 表示换手 2%），与全系统/同花顺口径一致；
    force_factors._score_turnover_quality 的阈值（3.0/5.0/20.0/30.0）即按此口径。
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    from zstock.common.utils import xtquant_data_utils as xtu
    float_map = xtu.fetch_float_shares_map(codes)
    df['turnover_rate'] = 0.0
    if 'volume' in df.columns and float_map:
        for code, fv in float_map.items():
            mask = df['code'] == code
            if mask.any():
                df.loc[mask, 'turnover_rate'] = df.loc[mask, 'volume'].astype(float) * 10000.0 / fv
    return df


async def main():
    # 分批写入，每批 500 行避免单次写入过大
    BATCH_SIZE = 500
    # 1. 连接 MongoDB
    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()
    logger.info("MongoDB 已连接")

    # 2. 初始化服务
    import zstock.data_management.query_service as qs_module
    from zstock.data_management.query_service import DataQueryService
    qs = DataQueryService()
    await qs.ensure_indexes()
    logger.info(f"数据源")

    t0 = datetime.now()

    # 查到所有股票
    all_stocks, source = await qs.get_all_stocks()
    logger.info(f"  ✓ 全市场 {len(all_stocks)} 只 (来源: {source})")

    # ── 表: ohlcv ──
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=OHLCV_DAYS)).strftime('%Y-%m-%d')
    logger.info(f"▶ ohlcv — {len(all_stocks)} 只股票  {start} ~ {end}")

    # 批量获取所有股票的 OHLCV（返回纵向拼接的 DataFrame）
    from zstock.common.utils import xtquant_data_utils as xtu

    # todo 这里可以换数据源
    all_codes = [s['code'] for s in all_stocks]
    df_all = xtu.fetch_ohlcv_batch(all_codes, start, end)
    total_ohlcv = len(df_all) if df_all is not None and not df_all.empty else 0
    logger.info(f"  ✓ 远程拉取 {total_ohlcv} 行")

    # 补充换手率：turnover_rate = 成交量(手)×10000 / 流通股本(股)，百分数口径
    if df_all is not None and not df_all.empty:
        df_all = _enrich_turnover_rate(df_all, all_codes)
        n_tr = int((df_all['turnover_rate'] > 0).sum()) if 'turnover_rate' in df_all.columns else 0
        logger.info(f"  ✓ 换手率已补充: {n_tr}/{total_ohlcv} 行有效")

    # 批量落库 MongoDB
    if total_ohlcv > 0:
        for batch_start in range(0, total_ohlcv, BATCH_SIZE):
            batch_df = df_all.iloc[batch_start:batch_start + BATCH_SIZE]
            await _persist_ohlcv(batch_df, 'D')
        logger.info(f"  ✓ ohlcv 写入 {total_ohlcv} 行")

    # ── 表: 指数 ohlcv ──
    total_index = 0
    logger.info(f"▶ index ohlcv — {len(INDEX_CODES)} 个指数  {start} ~ {end}")
    for idx_code, idx_name in INDEX_CODES:
        try:
            df_idx = xtu.fetch_ohlcv(idx_code, start, end)
        except Exception as e:
            logger.warning(f"  ⚠️ 指数 {idx_name}({idx_code}) 拉取失败: {e}")
            continue
        if df_idx is None or df_idx.empty:
            logger.warning(f"  ⚠️ 指数 {idx_name}({idx_code}) 无数据")
            continue
        # 指数没有 FloatVolume，不补换手率（MarketFactors 只用 volume 不用 turnover_rate）
        n = len(df_idx)
        for batch_start in range(0, n, BATCH_SIZE):
            batch_df = df_idx.iloc[batch_start:batch_start + BATCH_SIZE]
            await _persist_ohlcv(batch_df, 'D')
        total_index += n
        logger.info(f"  ✓ {idx_name}({idx_code}): {n} 行")
    logger.info(f"  ✓ 指数 ohlcv 共写入 {total_index} 行")

    # ── 汇总 ──
    elapsed = (datetime.now() - t0).total_seconds()
    logger.info("=" * 50)
    logger.info(f"同步完成  耗时 {elapsed:.1f}s")
    logger.info(f"  ohlcv      : {total_ohlcv} 行")
    logger.info(f"  index ohlcv: {total_index} 行")
    logger.info("=" * 50)

    await db_module.db_manager.close_connections()




if __name__ == '__main__':
    asyncio.run(main())
