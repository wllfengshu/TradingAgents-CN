"""
清理 zstock_sector 中非 A 股板块数据

删除规则：
1. sector_type == 'exchange'  (期货交易所：上期所/中金所/大商所/郑商所/能源中心等)
2. sector_type == 'board_non_a' (非 A 股 board 类型)
3. sector_name 包含非 A 股关键词 (B股/期权/债券/基金/ETF/转债/期货/港股/美股/新三板/回购)
4. 指数成分×行业交叉板块 (如 300SW2贵金属 = 沪深300中的申万二级贵金属子集)
5. 加权指数变体 (如 SW2贵金属加权)
6. stocks 里全是非 A 股代码的板块 (如成分股为期货合约)

保留的有效板块示例：
  - SW2贵金属 (申万二级行业 — 券商 APP 标准分类)
  - THY2贵金属 (同花顺二级行业)
  - THY3贵金属 (同花顺三级行业)

使用方式:
    python -m zstock.data_management.script.cleanup_invalid_sectors          # dry-run 预览
    python -m zstock.data_management.script.cleanup_invalid_sectors --apply  # 实际删除
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── 删除规则 ──
EXCLUDE_SECTOR_TYPES = {'exchange', 'board_non_a'}

EXCLUDE_NAME_KEYWORDS = (
    '上期所', '中金所', '大商所', '郑商所', '能源中心', '广期所', '上金所', '上期能源',
    'B股', '期权', '债券', '基金', 'ETF', '转债',
    '期货', '港股', '美股', '新三板', '回购',
)

# 指数成分×行业交叉板块前缀（如 300SW2贵金属 = 沪深300中的申万二级贵金属子集）
# 这些是量化辅助板块，券商 APP 不使用
_INDEX_CROSS_PREFIX_RE = re.compile(r'^\d+(SW|THY)\d')

# "加权"后缀的行业指数板块（如 SW2贵金属加权），属于加权指数变体
_WEIGHTED_SUFFIX = '加权'

# A 股代码正则：6位数字（沪深北三市）
_A_STOCK_RE = re.compile(r'^\d{6}$')


def _is_invalid_sector(doc: dict) -> str | None:
    """
    判断一个 sector 文档是否应该被删除。
    返回 None 表示保留，返回字符串表示删除原因。
    """
    name = doc.get('sector_name', '')
    stype = doc.get('sector_type', '')

    # 规则 1: sector_type 黑名单
    if stype in EXCLUDE_SECTOR_TYPES:
        return f"sector_type={stype}"

    # 规则 2: 名称关键词匹配
    for kw in EXCLUDE_NAME_KEYWORDS:
        if kw in name:
            return f"名称含'{kw}'"

    # 规则 3: 指数成分×行业交叉板块 (如 300SW2xxx, 500SW2xxx, 1000SW2xxx, 1000THY2xxx)
    if _INDEX_CROSS_PREFIX_RE.match(name):
        return f"指数交叉板块(前缀匹配)"

    # 规则 4: 加权指数变体 (如 SW2贵金属加权)
    if name.endswith(_WEIGHTED_SUFFIX):
        return f"加权指数变体"

    # 规则 5: 成分股全为非 A 股代码
    stocks = doc.get('stocks', [])
    if stocks:
        a_count = sum(1 for s in stocks if _A_STOCK_RE.match(s))
        if a_count == 0:
            return f"成分股 {len(stocks)} 只，无 A 股代码"

    return None  # 保留


async def run(apply: bool = False):
    from app.core import database as db_module
    await db_module.db_manager.init_mongodb()
    db = db_module.db_manager.mongo_db
    from zstock.data_management.query_service import COL_SECTOR

    col = db[COL_SECTOR]
    total = await col.count_documents({'source': 'xtquant'})
    logger.info(f"zstock_sector (source=xtquant) 共 {total} 条记录")

    cursor = col.find({'source': 'xtquant'})
    to_delete = []  # (sector_code, reason)

    async for doc in cursor:
        reason = _is_invalid_sector(doc)
        if reason:
            to_delete.append((doc['sector_code'], reason))

    if not to_delete:
        logger.info("✅ 未发现需要删除的无效板块")
        return

    # 打印待删除列表
    print(f"\n{'='*70}")
    print(f"  待删除板块: {len(to_delete)} / {total}")
    print(f"{'='*70}")
    for i, (code, reason) in enumerate(to_delete, 1):
        print(f"  {i:4d}. {code:<30s}  ← {reason}")
    print(f"{'='*70}\n")

    if not apply:
        print("⚠️  以上为 DRY-RUN 预览，未实际删除。")
        print("   添加 --apply 参数执行删除：")
        print("   python -m zstock.data_management.script.cleanup_invalid_sectors --apply")
        return

    # 执行删除
    codes = [c for c, _ in to_delete]
    result = await col.delete_many({
        'source': 'xtquant',
        'sector_code': {'$in': codes},
    })
    logger.info(f"🗑️  已删除 {result.deleted_count} 个无效板块")
    print(f"\n🗑️  已删除 {result.deleted_count} 个无效板块")

    remaining = await col.count_documents({'source': 'xtquant'})
    print(f"   剩余有效板块: {remaining}")


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    apply = '--apply' in sys.argv
    asyncio.run(run(apply=apply))


if __name__ == '__main__':
    main()
