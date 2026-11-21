"""
逐表审计 MongoDB：文档量、自然键重复、日期覆盖缺口。

大表（>50万）只做：估计文档数、日期范围、交易日覆盖、窗口内重复、抽样代码缺口。
小表做全量重复 + 完整日历检查。

用法：
  python -m zstock.data_management.script.audit_db_completeness
  python -m zstock.data_management.script.audit_db_completeness --start 2020-01-01 --end 2026-11-13
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LARGE_N = 500_000

# 自然键；含 period 的表一并写入
KNOWN_KEYS: Dict[str, List[str]] = {
    "zstock_stock_info": ["code"],
    "mainboard_stocks": ["code"],
    "stock_basic_info": ["code", "source"],
    "zstock_ohlcv": ["code", "trade_date", "period"],
    "stock_daily_quotes": ["symbol", "trade_date", "data_source", "period"],
    "zstock_capital_flow": ["code", "trade_date", "period"],
    "capital_flow": ["code", "trade_date"],
    "market_data": ["code", "trade_date"],
    "market_quotes": ["code", "trade_date"],
    "zstock_factor_market": ["trade_date"],
    "zstock_factor_sector": ["sector_code", "trade_date"],
    "zstock_factor_dragon": ["code", "trade_date"],
    "zstock_factor_force": ["code", "trade_date"],
    "zstock_fundamental_holder": ["code", "ann_date", "end_date", "shareholder"],
    "zstock_fundamental_pershare": ["code", "ann_date"],
    "zstock_sector": ["sector_code", "source"],
    "sector_stocks": ["sector_code", "code", "trade_date"],
    "trade_status": ["code", "trade_date"],
    "stock_financial_data": ["code", "report_period", "report_type", "data_source"],
    "stock_news": ["symbol", "url"],
    "zstock_orders": ["order_id"],
    "ai_trading_orders": ["order_id"],
}


def _as_ymd(v: Any) -> Optional[str]:
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if not s:
        return None
    # 20260701 / 2026-07-01
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def _infer_key(doc: Optional[dict], name: str) -> List[str]:
    if name in KNOWN_KEYS:
        return list(KNOWN_KEYS[name])
    if not doc:
        return []
    keys: List[str] = []
    for cand in ("order_id", "task_id", "analysis_id", "job_id", "code", "symbol", "sector_code", "id"):
        if cand in doc:
            keys.append(cand)
            break
    for cand in ("trade_date", "ann_date", "report_period", "date"):
        if cand in doc and cand not in keys:
            keys.append(cand)
            break
    if "period" in doc and "period" not in keys and any(k in keys for k in ("code", "symbol")):
        keys.append("period")
    if "data_source" in doc and "data_source" not in keys and "trade_date" in keys:
        keys.append("data_source")
    if "source" in doc and name.startswith("stock_basic"):
        if "source" not in keys:
            keys.append("source")
    return keys


async def _load_trade_days(db, start: str, end: str) -> List[str]:
    # 用日频市场因子作交易日历（与回测窗口一致）
    if "zstock_factor_market" in await db.list_collection_names():
        dates = await db["zstock_factor_market"].distinct(
            "trade_date", {"trade_date": {"$gte": start, "$lte": end}}
        )
        out = sorted({d for d in (_as_ymd(x) for x in dates) if d})
        if out:
            return out
    if "zstock_ohlcv" in await db.list_collection_names():
        dates = await db["zstock_ohlcv"].distinct(
            "trade_date", {"code": "000001", "trade_date": {"$gte": start, "$lte": end}}
        )
        out = sorted({d for d in (_as_ymd(x) for x in dates) if d})
        if out:
            return out
    return []


async def _date_minmax(col, date_field: str) -> Dict[str, Any]:
    """优先走排序+limit（可吃 trade_date 索引），避免全表 $group。"""
    proj = {date_field: 1, "_id": 0}
    mn = await col.find_one({date_field: {"$exists": True, "$nin": [None, ""]}}, sort=[(date_field, 1)], projection=proj)
    mx = await col.find_one({date_field: {"$exists": True, "$nin": [None, ""]}}, sort=[(date_field, -1)], projection=proj)
    if not mn and not mx:
        return {}
    return {
        "date_min": _as_ymd((mn or {}).get(date_field)),
        "date_max": _as_ymd((mx or {}).get(date_field)),
    }


def _unique_index_covers(indexes: Dict[str, Any], key_fields: Sequence[str]) -> Optional[str]:
    """若存在覆盖 key_fields 的唯一索引，返回索引名。"""
    want = list(key_fields)
    if not want:
        return None
    for name, meta in (indexes or {}).items():
        if not meta.get("unique"):
            continue
        keys = [k for k, _ in (meta.get("key") or [])]
        if keys == want or set(want).issubset(set(keys)):
            # 唯一索引字段集合包含自然键即可（允许多一列如 symbol）
            if all(f in keys for f in want):
                return name
    return None


async def _distinct_dates_in_window(col, date_field: str, start: str, end: str) -> List[str]:
    # 兼容 20260701 与 2026-07-01：两边都查再归一
    starts = [start, start.replace("-", "")]
    ends = [end, end.replace("-", "")]
    have: set = set()
    for s, e in zip(starts, ends):
        pipe = [
            {"$match": {date_field: {"$gte": s, "$lte": e}}},
            {"$group": {"_id": f"${date_field}"}},
        ]
        rows = await col.aggregate(pipe, allowDiskUse=True).to_list(None)
        for r in rows:
            y = _as_ymd(r.get("_id"))
            if y:
                have.add(y)
    return sorted(have)


async def _dup_in_window(
    col,
    key_fields: Sequence[str],
    date_field: Optional[str],
    start: str,
    end: str,
    *,
    large: bool,
    sample_limit: int = 15,
) -> Dict[str, Any]:
    if not key_fields:
        return {"checked": False, "reason": "no_key"}
    match: Dict[str, Any] = {}
    if date_field:
        # 大表只扫窗口；兼容无横杠日期
        match = {
            "$or": [
                {date_field: {"$gte": start, "$lte": end}},
                {date_field: {"$gte": start.replace("-", ""), "$lte": end.replace("-", "")}},
            ]
        }
    group_id = {f: f"${f}" for f in key_fields}
    try:
        t0 = time.time()
        if large and date_field:
            # 大表无唯一索引：只扫窗口末 120 个日历日，避免千万级全窗 group
            from datetime import datetime as _dt, timedelta as _td

            end_d = _dt.strptime(end, "%Y-%m-%d")
            tail_start = (end_d - _td(days=180)).strftime("%Y-%m-%d")
            if tail_start < start:
                tail_start = start
            match_tail = {
                "$or": [
                    {date_field: {"$gte": tail_start, "$lte": end}},
                    {
                        date_field: {
                            "$gte": tail_start.replace("-", ""),
                            "$lte": end.replace("-", ""),
                        }
                    },
                ]
            }
            pipe = [
                {"$match": match_tail},
                {"$group": {"_id": group_id, "n": {"$sum": 1}}},
                {"$match": {"n": {"$gt": 1}}},
                {
                    "$facet": {
                        "stats": [
                            {
                                "$group": {
                                    "_id": None,
                                    "dup_groups": {"$sum": 1},
                                    "extra_docs": {"$sum": {"$subtract": ["$n", 1]}},
                                }
                            }
                        ],
                        "samples": [{"$sort": {"n": -1}}, {"$limit": sample_limit}],
                    }
                },
            ]
            rows = await asyncio.wait_for(
                col.aggregate(pipe, allowDiskUse=True).to_list(1),
                timeout=300.0,
            )
            facet = rows[0] if rows else {}
            stats = (facet.get("stats") or [{}])[0]
            samples = facet.get("samples") or []
            return {
                "checked": True,
                "scope": "window_tail_180d",
                "window": {"start": tail_start, "end": end},
                "key": list(key_fields),
                "dup_groups": int(stats.get("dup_groups") or 0),
                "extra_docs": int(stats.get("extra_docs") or 0),
                "samples": [{"key": s.get("_id"), "n": s.get("n")} for s in samples[:10]],
                "elapsed_sec": round(time.time() - t0, 1),
                "note": "大表仅检查近180日历日窗口内重复",
            }
        # 小表：全表
        pipe = [
            {"$group": {"_id": group_id, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}},
            {
                "$facet": {
                    "stats": [
                        {
                            "$group": {
                                "_id": None,
                                "dup_groups": {"$sum": 1},
                                "extra_docs": {"$sum": {"$subtract": ["$n", 1]}},
                            }
                        }
                    ],
                    "samples": [{"$sort": {"n": -1}}, {"$limit": sample_limit}],
                }
            },
        ]
        rows = await col.aggregate(pipe, allowDiskUse=True).to_list(1)
        facet = rows[0] if rows else {}
        stats = (facet.get("stats") or [{}])[0]
        samples = facet.get("samples") or []
        return {
            "checked": True,
            "scope": "full",
            "key": list(key_fields),
            "dup_groups": int(stats.get("dup_groups") or 0),
            "extra_docs": int(stats.get("extra_docs") or 0),
            "samples": [{"key": s.get("_id"), "n": s.get("n")} for s in samples[:10]],
            "elapsed_sec": round(time.time() - t0, 1),
        }
    except Exception as e:
        return {
            "checked": False,
            "reason": f"{type(e).__name__}: {e}",
            "key": list(key_fields),
        }


async def _code_gaps(
    col,
    code_field: str,
    date_field: str,
    codes: Sequence[str],
    trade_days: Sequence[str],
    start: str,
    end: str,
) -> Dict[str, Any]:
    trade_set = set(trade_days)
    worst = []
    for code in codes:
        # 单边日期格式查询（zstock 主表多为 YYYY-MM-DD），走 code/symbol+date 索引
        pipe = [
            {
                "$match": {
                    code_field: code,
                    date_field: {"$gte": start, "$lte": end},
                }
            },
            {"$group": {"_id": f"${date_field}"}},
        ]
        try:
            rows = await asyncio.wait_for(
                col.aggregate(pipe, allowDiskUse=True).to_list(None),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            worst.append(
                {
                    "code": code,
                    "have": None,
                    "expect": len(trade_set),
                    "missing_n": None,
                    "error": "timeout_60s",
                    "coverage_pct": None,
                }
            )
            continue
        have = {d for d in (_as_ymd(r.get("_id")) for r in rows) if d}
        missing = sorted(trade_set - have)
        worst.append(
            {
                "code": code,
                "have": len(have & trade_set),
                "expect": len(trade_set),
                "missing_n": len(missing),
                "missing_head": missing[:8],
                "missing_tail": missing[-5:] if missing else [],
                "coverage_pct": round(100.0 * len(have & trade_set) / max(len(trade_set), 1), 2),
            }
        )
    worst.sort(key=lambda x: -(x["missing_n"] or -1))
    return {
        "checked": True,
        "sampled_codes": len(codes),
        "trade_days": len(trade_days),
        "codes_with_gaps": sum(1 for x in worst if (x.get("missing_n") or 0) > 0),
        "worst": worst[:20],
        "perfect": [x["code"] for x in worst if x.get("missing_n") == 0][:20],
    }


async def audit_one(
    db,
    name: str,
    start: str,
    end: str,
    trade_days: List[str],
    probe_codes: List[str],
) -> Dict[str, Any]:
    if name.startswith("system."):
        return {"collection": name, "skipped": True, "reason": "system_namespace"}

    col = db[name]
    t0 = time.time()
    try:
        n_est = int(await col.estimated_document_count())
    except Exception as e:
        return {"collection": name, "error": f"estimated_count: {e}"}

    large = n_est >= LARGE_N
    if large:
        n_count = n_est
        count_mode = "estimated"
    else:
        try:
            n_count = int(await col.count_documents({}))
            count_mode = "exact"
        except Exception:
            n_count = n_est
            count_mode = "estimated_fallback"

    sample = await col.find_one({}, projection={"_id": 0})
    fields = sorted(sample.keys()) if sample else []
    key_fields = _infer_key(sample, name)
    date_field = next(
        (f for f in ("trade_date", "ann_date", "report_period", "date", "publish_time") if sample and f in sample),
        None,
    )

    report: Dict[str, Any] = {
        "collection": name,
        "count": n_count,
        "count_mode": count_mode,
        "large": large,
        "sample_fields": fields[:30],
        "key_fields": key_fields,
        "date_field": date_field,
    }
    try:
        idx_info = await col.index_information()
        report["indexes"] = list(idx_info.keys())
    except Exception as e:
        idx_info = {}
        report["indexes_error"] = str(e)

    # null keys
    if key_fields and n_est < LARGE_N:
        nulls = {}
        for f in key_fields:
            try:
                nulls[f] = await col.count_documents(
                    {"$or": [{f: None}, {f: ""}, {f: {"$exists": False}}]}
                )
            except Exception as e:
                nulls[f] = f"err:{e}"
        report["null_key_docs"] = nulls

    # date range + calendar
    if date_field and n_est > 0:
        print(f"    dates...", flush=True)
        try:
            report.update(await _date_minmax(col, date_field))
        except Exception as e:
            report["date_minmax_error"] = f"{type(e).__name__}: {e}"
        try:
            d_in = await _distinct_dates_in_window(col, date_field, start, end)
            expect = set(trade_days)
            have = set(d_in)
            missing = sorted(expect - have)
            report["distinct_dates_in_window"] = len(d_in)
            report["calendar_coverage_pct"] = round(
                100.0 * len(have & expect) / max(len(expect), 1), 2
            )
            report["calendar_missing_days"] = len(missing)
            report["calendar_missing_sample"] = missing[:25]
            report["calendar_extra_days"] = len(have - expect)
        except Exception as e:
            report["calendar_error"] = f"{type(e).__name__}: {e}"

    # duplicates：有唯一索引则免全表扫描
    uniq = _unique_index_covers(idx_info, key_fields)
    if uniq:
        print(f"    duplicates skipped (unique index {uniq})", flush=True)
        report["duplicates"] = {
            "checked": True,
            "scope": "unique_index",
            "unique_index": uniq,
            "key": key_fields,
            "dup_groups": 0,
            "extra_docs": 0,
            "note": "唯一索引保证自然键不重复（同键写入会被拒）",
        }
    else:
        print(f"    duplicates scope={'window' if large else 'full'}...", flush=True)
        report["duplicates"] = await _dup_in_window(
            col, key_fields, date_field, start, end, large=large
        )

    # per-code gaps：仅策略主表；字段优先走有索引的 symbol/code
    gap_tables = {
        "zstock_ohlcv": "code",
        "stock_daily_quotes": "symbol",  # 唯一/复合索引在 symbol 上
        "zstock_capital_flow": "code",
        "zstock_factor_dragon": "code",
        "zstock_factor_force": "code",
    }
    if name in gap_tables and date_field and trade_days:
        code_field = gap_tables[name]
        codes = list(probe_codes)
        print(f"    code gaps via {code_field} ({len(codes)} probes)...", flush=True)
        try:
            report["code_coverage"] = await _code_gaps(
                col, code_field, date_field, codes, trade_days, start, end
            )
        except Exception as e:
            report["code_coverage_error"] = f"{type(e).__name__}: {e}"

    report["elapsed_sec"] = round(time.time() - t0, 1)
    return report


async def main_async(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-11-13")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "output" / "db_completeness_audit.json"),
    )
    args = parser.parse_args(argv)

    from zstock.common.logging_setup import setup_logging
    from zstock.common.utils.db_utils import close_zstock_database, get_database, init_zstock_database
    from zstock.common.book_codes import BOOK_OHLCV_CODES

    setup_logging("db_completeness_audit", stamped=False)
    await init_zstock_database()
    db = get_database()
    names = sorted(n for n in await db.list_collection_names() if not n.startswith("system."))
    trade_days = await _load_trade_days(db, args.start, args.end)

    # 探针代码：Book + 几只主板
    probe = [str(c) for c in BOOK_OHLCV_CODES]
    for extra in ("000001", "600000", "000858", "601318", "300750"):
        if extra not in probe:
            probe.append(extra)

    summary: Dict[str, Any] = {
        "database": db.name,
        "audited_at": datetime.now().isoformat(timespec="seconds"),
        "window": {"start": args.start, "end": args.end},
        "trade_days_calendar": len(trade_days),
        "trade_calendar_source": "zstock_factor_market.trade_date",
        "n_collections": len(names),
        "collections": names,
        "large_threshold": LARGE_N,
        "reports": [],
    }
    print(
        f"DB={db.name} collections={len(names)} trade_days={len(trade_days)} "
        f"window={args.start}~{args.end}",
        flush=True,
    )

    for i, name in enumerate(names, 1):
        print(f"[{i}/{len(names)}] {name}", flush=True)
        try:
            rep = await audit_one(db, name, args.start, args.end, trade_days, probe)
        except Exception as e:
            rep = {"collection": name, "error": f"{type(e).__name__}: {e}"}
        summary["reports"].append(rep)
        dup = (rep.get("duplicates") or {}).get("dup_groups", "-")
        print(
            f"  count={rep.get('count')} large={rep.get('large')} "
            f"dates={rep.get('date_min')}~{rep.get('date_max')} "
            f"cal={rep.get('calendar_coverage_pct')}% "
            f"dup_groups={dup} ({rep.get('elapsed_sec')}s)",
            flush=True,
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    compact = []
    issues = []
    for r in summary["reports"]:
        row = {
            "collection": r.get("collection"),
            "count": r.get("count"),
            "large": r.get("large"),
            "key": r.get("key_fields"),
            "date_min": r.get("date_min"),
            "date_max": r.get("date_max"),
            "cal_cov_pct": r.get("calendar_coverage_pct"),
            "cal_missing_days": r.get("calendar_missing_days"),
            "dup_groups": (r.get("duplicates") or {}).get("dup_groups"),
            "extra_docs": (r.get("duplicates") or {}).get("extra_docs"),
            "codes_with_gaps": ((r.get("code_coverage") or {}).get("codes_with_gaps")),
            "error": r.get("error"),
        }
        compact.append(row)
        problems = []
        if (row.get("dup_groups") or 0) > 0:
            problems.append(f"dup_groups={row['dup_groups']}")
        if row.get("cal_missing_days") and row["cal_missing_days"] > 0 and row.get("cal_cov_pct") is not None:
            # only flag if table claims to be daily market series in window
            if row.get("date_min") and row.get("count", 0) > 100:
                problems.append(f"missing_days={row['cal_missing_days']} cov={row['cal_cov_pct']}%")
        if row.get("codes_with_gaps"):
            problems.append(f"probe_codes_with_gaps={row['codes_with_gaps']}")
        if row.get("error"):
            problems.append(str(row["error"]))
        if problems:
            issues.append({"collection": row["collection"], "problems": problems})

    compact_path = out.with_name(out.stem + "_compact.json")
    issues_path = out.with_name(out.stem + "_issues.json")
    compact_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")
    issues_path.write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    print(f"wrote {compact_path}")
    print(f"wrote {issues_path} ({len(issues)} collections with flags)")

    await close_zstock_database()
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
