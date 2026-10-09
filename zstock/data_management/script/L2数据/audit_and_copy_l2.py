"""审计 work/ 下 L2 导出文件：去重复制到 work_clean/，并记录小号文件与缺失交易日。"""
from __future__ import annotations

import csv
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_FILENAME_RE = re.compile(r"全部[ＡA]股_(\d{8})(?:_\d+)?\.xls$", re.IGNORECASE)


def parse_filename(fname: str) -> str | None:
    m = _FILENAME_RE.search(fname)
    return m.group(1) if m else None

SRC_ROOT = Path(__file__).resolve().parent / "work"
DST_ROOT = Path(__file__).resolve().parent / "work_clean"
SIZE_LIMIT = 1300 * 1024  # 1300 KB


def _kb(n: int) -> float:
    return n / 1024


def load_trade_calendar(start: str, end: str) -> list[str]:
    from app.core.config import settings
    from pymongo import MongoClient

    client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=8000)
    col = client[settings.MONGO_DB]["zstock_ohlcv"]
    start_iso = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    end_iso = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    dates = sorted(
        d
        for d in col.distinct("trade_date", {"code": "399300", "period": "D"})
        if start_iso <= d <= end_iso
    )
    return [d.replace("-", "") for d in dates]


def scan_files() -> dict[str, list[tuple[int, Path]]]:
    by_date: dict[str, list[tuple[int, Path]]] = defaultdict(list)
    for path in SRC_ROOT.rglob("*.xls"):
        date_key = parse_filename(path.name)
        if not date_key:
            continue
        by_date[date_key].append((path.stat().st_size, path))
    for date_key in by_date:
        by_date[date_key].sort(key=lambda x: (-x[0], str(x[1])))
    return dict(by_date)


def main() -> None:
    DST_ROOT.mkdir(parents=True, exist_ok=True)

    by_date = scan_files()
    if not by_date:
        raise SystemExit(f"未扫描到 xls: {SRC_ROOT}")

    all_dates = sorted(by_date)
    calendar = load_trade_calendar(all_dates[0], all_dates[-1])
    file_date_set = set(all_dates)
    missing_dates = [d for d in calendar if d not in file_date_set]
    extra_dates = [d for d in all_dates if d not in set(calendar)]

    chosen: dict[str, tuple[int, Path]] = {d: files[0] for d, files in by_date.items()}
    duplicates = {d: files for d, files in by_date.items() if len(files) > 1}

    small_records: list[dict] = []
    for date_key, files in sorted(by_date.items()):
        for size, path in files:
            if size < SIZE_LIMIT:
                small_records.append(
                    {
                        "trade_date": date_key,
                        "size_kb": f"{_kb(size):.1f}",
                        "size_bytes": size,
                        "path": str(path.relative_to(SRC_ROOT)),
                        "chosen": "Y" if (size, path) == chosen[date_key] else "N",
                    }
                )

    only_small_dates = [
        d for d, (size, _) in chosen.items() if size < SIZE_LIMIT
    ]

    copied = 0
    skipped_small = 0
    for date_key, (size, path) in sorted(chosen.items()):
        if size < SIZE_LIMIT:
            skipped_small += 1
            continue
        dest = DST_ROOT / f"全部Ａ股_{date_key}.xls"
        shutil.copy2(path, dest)
        copied += 1

    report_path = DST_ROOT / "audit_report.txt"
    small_csv = DST_ROOT / "small_files.csv"
    missing_csv = DST_ROOT / "missing_dates.csv"
    dup_csv = DST_ROOT / "duplicates.csv"

    with small_csv.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=["trade_date", "size_kb", "size_bytes", "path", "chosen"],
        )
        writer.writeheader()
        writer.writerows(small_records)

    with missing_csv.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["trade_date", "iso"])
        for d in missing_dates:
            writer.writerow([d, f"{d[:4]}-{d[4:6]}-{d[6:8]}"])

    with dup_csv.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["trade_date", "kept_path", "kept_kb", "dropped_path", "dropped_kb"])
        for d, files in sorted(duplicates.items()):
            kept_size, kept_path = files[0]
            for size, path in files[1:]:
                writer.writerow(
                    [
                        d,
                        str(kept_path.relative_to(SRC_ROOT)),
                        f"{_kb(kept_size):.1f}",
                        str(path.relative_to(SRC_ROOT)),
                        f"{_kb(size):.1f}",
                    ]
                )

    small_by_year: dict[str, int] = defaultdict(int)
    only_small_by_year: dict[str, int] = defaultdict(int)
    copied_by_year: dict[str, int] = defaultdict(int)
    for rec in small_records:
        small_by_year[rec["trade_date"][:4]] += 1
    for d in only_small_dates:
        only_small_by_year[d[:4]] += 1
    for d, (size, _) in chosen.items():
        if size >= SIZE_LIMIT:
            copied_by_year[d[:4]] += 1

    missing_by_year: dict[str, list[str]] = defaultdict(list)
    for d in missing_dates:
        missing_by_year[d[:4]].append(d)

    lines = [
        "L2 导出文件审计报告",
        "=" * 60,
        f"源目录: {SRC_ROOT}",
        f"干净目录: {DST_ROOT}",
        f"规则: 同一交易日只保留体积最大的一份；小于 1300KB 视为有问题，不复制，只记录",
        f"日期窗口: {all_dates[0]} ~ {all_dates[-1]}（对照 399300 交易日历）",
        "",
        "一、总览",
        f"  扫描 xls: {sum(len(v) for v in by_date.values())}",
        f"  覆盖交易日: {len(by_date)}",
        f"  重复交易日: {len(duplicates)}",
        f"  <1300KB 文件: {len(small_records)}",
        f"  仅有小号文件的交易日: {len(only_small_dates)}（未复制）",
        f"  已复制到 work_clean: {copied}",
        f"  缺失交易日: {len(missing_dates)}",
        f"  文件有、日历无（非交易日/日历缺口）: {len(extra_dates)}",
        "",
        "二、按年复制 / 小号文件",
        f"  {'年份':<6}{'复制':>8}{'仅小号未复制':>16}{'小号文件数':>12}",
    ]
    for year in [str(y) for y in range(int(all_dates[0][:4]), int(all_dates[-1][:4]) + 1)]:
        lines.append(
            f"  {year:<6}{copied_by_year.get(year, 0):>8}"
            f"{only_small_by_year.get(year, 0):>16}"
            f"{small_by_year.get(year, 0):>12}"
        )

    lines += ["", "三、缺失交易日（按年）"]
    if not missing_dates:
        lines.append("  无")
    else:
        for year, days in missing_by_year.items():
            preview = ", ".join(f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in days[:12])
            more = "" if len(days) <= 12 else f" ... 共 {len(days)} 天"
            lines.append(f"  {year}: {len(days)} 天  {preview}{more}")

    lines += ["", "四、重复日期（保留体积最大的一份）"]
    for d, files in sorted(duplicates.items()):
        kept = files[0]
        dropped = ", ".join(
            f"{p.relative_to(SRC_ROOT)} ({_kb(s):.1f}KB)" for s, p in files[1:]
        )
        lines.append(
            f"  {d}: 保留 {kept[1].relative_to(SRC_ROOT)} ({_kb(kept[0]):.1f}KB)；丢弃 {dropped}"
        )

    lines += [
        "",
        "五、明细文件",
        f"  {small_csv.name}: 全部 <1300KB 文件",
        f"  {missing_csv.name}: 缺失交易日",
        f"  {dup_csv.name}: 重复日期及取舍",
        "",
        "说明:",
        "  2020-2022 导出文件普遍小于 1300KB，但行数约 5574、字段齐全，体积偏小主要是早期成交数字更短。",
        "  按你给的 1300KB 规则，这些日期没有进入 work_clean，全部记在 small_files.csv。",
        "  2023 年一批约 960-1020KB 的文件行数在，但资金流列大量为空，属于真正的数据不全。",
        "  2026-07-27 的 xiazai2 文件约 935KB（资金流几乎全 0），已用 xiazai3 的 1383KB 完整文件替代。",
        "",
        f"复制 {copied} 个文件，跳过仅有小号文件的 {skipped_small} 个交易日。",
        "源目录文件均未删除。",
    ]

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
