"""
清理 zstock_fundamental_pershare 重复：同一 (code, ann_date) 只留一条。

保留规则：字段更全优先，其次 _id 更大（更晚写入）。
清理后确保唯一索引 (code, ann_date)。

用法：
  python -m zstock.data_management.script.dedupe_fundamental_pershare
  python -m zstock.data_management.script.dedupe_fundamental_pershare --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _richness(doc: Dict[str, Any]) -> int:
    skip = {"_id", "code", "ann_date"}
    n = 0
    for k, v in doc.items():
        if k in skip:
            continue
        if v is None or v == "":
            continue
        n += 1
    return n


async def main_async(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from zstock.common.logging_setup import setup_logging
    from zstock.common.utils.db_utils import close_zstock_database, get_database, init_zstock_database
    from zstock.data_management.query_service import COL_FUNDAMENTAL_PERSHARE

    setup_logging("dedupe_fundamental_pershare", stamped=False)
    import logging

    log = logging.getLogger("dedupe_fundamental_pershare")

    await init_zstock_database()
    db = get_database()
    col = db[COL_FUNDAMENTAL_PERSHARE]

    pipe = [
        {
            "$group": {
                "_id": {"code": "$code", "ann_date": "$ann_date"},
                "n": {"$sum": 1},
                "ids": {"$push": "$_id"},
            }
        },
        {"$match": {"n": {"$gt": 1}}},
    ]
    groups = await col.aggregate(pipe, allowDiskUse=True).to_list(None)
    log.info("重复组数: %s", len(groups))
    if not groups:
        # 仍确保索引
        if not args.dry_run:
            await col.create_index(
                [("code", 1), ("ann_date", 1)],
                unique=True,
                name="code_ann_date_unique",
            )
            log.info("已确保唯一索引 code_ann_date_unique")
        await close_zstock_database()
        return 0

    delete_ids: List[Any] = []
    for g in groups:
        ids = g.get("ids") or []
        docs = await col.find({"_id": {"$in": ids}}).to_list(None)
        if len(docs) <= 1:
            continue
        docs.sort(key=lambda d: (_richness(d), str(d.get("_id"))), reverse=True)
        keep = docs[0]["_id"]
        for d in docs[1:]:
            delete_ids.append(d["_id"])
        if len(delete_ids) <= 5 or len(groups) < 5:
            log.info(
                "keep %s code=%s ann=%s richness=%s drop=%s",
                keep,
                g["_id"].get("code"),
                g["_id"].get("ann_date"),
                _richness(docs[0]),
                len(docs) - 1,
            )

    log.info("将删除重复文档: %s", len(delete_ids))
    if args.dry_run:
        log.info("dry-run，不写入")
        await close_zstock_database()
        return 0

    deleted = 0
    batch = 2000
    for i in range(0, len(delete_ids), batch):
        chunk = delete_ids[i : i + batch]
        res = await col.delete_many({"_id": {"$in": chunk}})
        deleted += int(res.deleted_count or 0)
        log.info("  已删 %s / %s", deleted, len(delete_ids))

    # 再建唯一索引（若已有冲突会失败）
    try:
        await col.create_index(
            [("code", 1), ("ann_date", 1)],
            unique=True,
            name="code_ann_date_unique",
        )
        log.info("已创建唯一索引 code_ann_date_unique")
    except Exception as e:
        log.error("创建唯一索引失败: %s", e)
        await close_zstock_database()
        return 1

    left = await col.aggregate(
        [
            {"$group": {"_id": {"code": "$code", "ann_date": "$ann_date"}, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}},
            {"$count": "n"},
        ],
        allowDiskUse=True,
    ).to_list(1)
    log.info("清理后剩余重复组: %s  当前文档数≈%s", (left[0]["n"] if left else 0), await col.estimated_document_count())
    await close_zstock_database()
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
