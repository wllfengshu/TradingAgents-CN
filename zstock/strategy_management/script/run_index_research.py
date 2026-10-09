"""
指数增强研究：v1.19 prod vs v1.20 候选（预计算回测）。

验收（防过拟合）：
  - 必看 2020（牛）、2022（熊）、2024/2025（结构与牛）
  - 主指标：相对沪深300 超额；辅指标：最大回撤
  - 仅当 2020 不劣于 prod 且 bull 年超额改善，才考虑替换 prod

    .venv\\Scripts\\python.exe -m zstock.strategy_management.script.run_index_research
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (  # noqa: E402
    _run_one,
    _setup_logging,
)

logger = logging.getLogger(__name__)

CONFIG_DIR = PROJECT_ROOT / "zstock" / "common" / "config"
PARAMS_PROD = CONFIG_DIR / "strategy_params.json"
PARAMS_CANDIDATE = CONFIG_DIR / "strategy_params_v120a_overlay_only.json"
CANDIDATE_LABEL = "v120a"

# 牛/熊/结构混合，避免单年调参
YEAR_RANGES: List[Tuple[int, str, str]] = [
    (2020, "2020-01-01", "2020-12-31"),
    (2022, "2022-01-01", "2022-12-31"),
    (2024, "2024-01-01", "2024-12-31"),
    (2025, "2025-01-01", "2025-12-31"),
]


def _score_row(rows: List[Dict[str, Any]], label: str) -> Dict[str, float]:
    sub = [r for r in rows if r.get("label") == label and not r.get("error")]
    if not sub:
        return {"avg_excess": float("nan"), "avg_mdd": float("nan"), "min_excess": float("nan")}
    excess = [float(r["excess_vs_index"]) for r in sub if r.get("excess_vs_index") is not None]
    mdd = [float(r["max_drawdown"]) for r in sub if r.get("max_drawdown") is not None]
    return {
        "avg_excess": sum(excess) / len(excess) if excess else float("nan"),
        "avg_mdd": sum(mdd) / len(mdd) if mdd else float("nan"),
        "min_excess": min(excess) if excess else float("nan"),
    }


async def async_main() -> int:
    if not PARAMS_CANDIDATE.is_file():
        logger.error("缺少候选配置: %s", PARAMS_CANDIDATE)
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent / "output" / f"index_research_{CANDIDATE_LABEL}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import get_data_query_service
    from zstock.strategy_management.script.backtest_report import write_backtest_report

    await init_zstock_database()
    try:
        await get_data_query_service().ensure_indexes()
    except Exception as e:
        logger.warning("索引: %s", e)

    rows: List[Dict[str, Any]] = []
    jobs = []
    for year, start, end in YEAR_RANGES:
        for label, path in (("v119", PARAMS_PROD), (CANDIDATE_LABEL, PARAMS_CANDIDATE)):
            jobs.append((label, year, start, end, path, out_dir / f"{label}_{year}"))

    t_all = time.time()
    for i, (label, year, start, end, path, seg) in enumerate(jobs, 1):
        logger.info("[%d/%d] %s %s", i, len(jobs), label, year)
        seg.mkdir(parents=True, exist_ok=True)
        try:
            rows.append(await _run_one(label, start, end, path, seg))
        except Exception as e:
            logger.error("失败 %s %s: %s", label, year, e)
            traceback.print_exc()
            rows.append({"label": label, "start": start, "end": end, "error": str(e)})

    write_backtest_report(
        out_dir,
        rows,
        title=f"指数增强研究 v1.19 vs {CANDIDATE_LABEL}（方案A: 仅动量黄灯0.85）",
        subtitle="主指标=超额；辅指标=最大回撤 | OOS 年: 2020/2022/2024/2025",
    )
    s119 = _score_row(rows, "v119")
    sc = _score_row(rows, CANDIDATE_LABEL)
    verdict = []
    verdict.append(f"v119 平均超额={s119['avg_excess']:+.2%} 平均MDD={s119['avg_mdd']:+.2%}")
    verdict.append(
        f"{CANDIDATE_LABEL} 平均超额={sc['avg_excess']:+.2%} 平均MDD={sc['avg_mdd']:+.2%}"
    )
    if sc["min_excess"] >= s119["min_excess"] and sc["avg_excess"] > s119["avg_excess"]:
        verdict.append("→ 候选优于 prod 平均超额且最差年超额不更差（仍须人工看 2020 绝对收益）")
    else:
        verdict.append("→ 候选未通过自动门槛，继续单变量调参")
    (out_dir / "index_verdict.txt").write_text("\n".join(verdict), encoding="utf-8")
    logger.info("完成 %.1f 分钟\n%s", (time.time() - t_all) / 60, "\n".join(verdict))
    await close_zstock_database()
    return 0


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
