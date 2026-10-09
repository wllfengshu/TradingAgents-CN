"""回测结果汇总表（历史 / OOS 脚本共用）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def fmt_pct(x: Any) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return "n/a"
    return f"{float(x):+.2%}"


def write_backtest_report(
    out_dir: Path,
    rows: List[Dict[str, Any]],
    *,
    title: str,
    subtitle: str = "",
) -> None:
    lines = [title, f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    if subtitle:
        lines.append(subtitle)
    lines.append("")
    header = (
        f"{'年份':<6} {'配置':<10} {'策略收益':>10} {'沪深300':>10} "
        f"{'超额':>10} {'最大回撤':>10} {'Sharpe':>8}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        if r.get("error"):
            lines.append(f"{r['start'][:4]:<6} {r['label']:<10} ERROR: {r['error']}")
            continue
        y = r["start"][:4]
        sh = r.get("sharpe")
        sh_s = f"{float(sh):8.2f}" if sh is not None and sh == sh else "     n/a"
        lines.append(
            f"{y:<6} {r['label']:<10} {fmt_pct(r.get('total_return')):>10} "
            f"{fmt_pct(r.get('index_return_hs300')):>10} "
            f"{fmt_pct(r.get('excess_vs_index')):>10} "
            f"{fmt_pct(r.get('max_drawdown')):>10} {sh_s:>8}"
        )
    report = "\n".join(lines)
    (out_dir / "backtest_report.txt").write_text(report, encoding="utf-8")
    (out_dir / "backtest_results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
