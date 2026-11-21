"""
过夜对照回测：冷启动分段 + 回撤锁敏感性（E4 默认参数为基线）。

用法：
  python -m zstock.strategy_management.script.run_night_backtests --workers 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("night_backtests")
END_DEFAULT = "2026-11-13"


def _experiments() -> List[Dict[str, Any]]:
    """E4 已落地为默认；此处只覆盖要测的旋钮/区间。"""
    return [
        # —— 冷启动：对齐「分年公平比较」——
        {"id": "cold_2025_2026", "start": "2025-01-01", "end": END_DEFAULT, "book_regime": {}},
        {"id": "cold_2024_2026", "start": "2024-01-01", "end": END_DEFAULT, "book_regime": {}},
        {"id": "cold_2023_2026", "start": "2023-01-01", "end": END_DEFAULT, "book_regime": {}},
        {"id": "cold_2022_2026", "start": "2022-01-01", "end": END_DEFAULT, "book_regime": {}},
        {"id": "cold_2021_2026", "start": "2021-01-01", "end": END_DEFAULT, "book_regime": {}},
        {"id": "cold_2020_2026", "start": "2020-01-01", "end": END_DEFAULT, "book_regime": {}},
        # —— 单年冷启动 ——
        {"id": "year_2020", "start": "2020-01-01", "end": "2020-12-31", "book_regime": {}},
        {"id": "year_2021", "start": "2021-01-01", "end": "2021-12-31", "book_regime": {}},
        {"id": "year_2022", "start": "2022-01-01", "end": "2022-12-31", "book_regime": {}},
        {"id": "year_2023", "start": "2023-01-01", "end": "2023-12-31", "book_regime": {}},
        {"id": "year_2024", "start": "2024-01-01", "end": "2024-12-31", "book_regime": {}},
        {"id": "year_2025", "start": "2025-01-01", "end": "2025-12-31", "book_regime": {}},
        {"id": "year_2026", "start": "2026-01-01", "end": END_DEFAULT, "book_regime": {}},
        # —— 全样本回撤锁敏感性 ——
        {
            "id": "full_dd15",
            "start": "2020-01-01",
            "end": END_DEFAULT,
            "book_regime": {"dd_stop": 0.15},
        },
        {
            "id": "full_dd20",
            "start": "2020-01-01",
            "end": END_DEFAULT,
            "book_regime": {"dd_stop": 0.20},
        },
        {
            "id": "full_dd_off",
            "start": "2020-01-01",
            "end": END_DEFAULT,
            "book_regime": {"dd_stop": 0.99},
        },
        # —— 进攻确认对比（冷启动 2025）——
        {
            "id": "cold25_confirm3",
            "start": "2025-01-01",
            "end": END_DEFAULT,
            "book_regime": {
                "confirm_green_days": 3,
                "attack_skip_neutral_on_green": False,
            },
        },
        {
            "id": "cold25_b5_only",
            "start": "2025-01-01",
            "end": END_DEFAULT,
            "book_regime": {
                "confirm_green_days": 1,
                "attack_skip_neutral_on_green": True,
                "industry_stop_loss_pct": 0.0,
                "industry_rel_index_gate": 0.0,
                "industry_min_positive_mom": 0,
            },
        },
    ]


def _year_stats(curve: pd.DataFrame, year: int) -> Dict[str, float]:
    sub = curve[curve["date"].dt.year == year]
    if sub.empty:
        return {}
    rets = sub["daily_return"].astype(float)
    total = float((1.0 + rets).prod() - 1.0)
    n = len(sub)
    ann = float((1.0 + total) ** (252.0 / max(n, 1)) - 1.0)
    std = float(rets.std()) if len(rets) > 1 else 0.0
    sharpe = float(rets.mean() / std * np.sqrt(252)) if std > 0 else 0.0
    eq = (1.0 + rets).cumprod()
    mdd = float((eq / eq.cummax() - 1.0).min())
    return {
        f"{year}_ret": total,
        f"{year}_ann": ann,
        f"{year}_sharpe": sharpe,
        f"{year}_mdd": mdd,
        f"{year}_days": float(n),
    }


async def _run_one_async(exp: Dict[str, Any], output_root: str, log: logging.Logger) -> Dict[str, Any]:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester

    exp_id = exp["id"]
    start = exp["start"]
    end = exp["end"]
    t0 = time.time()
    out_dir = f"output/night_bt/{exp_id}"
    summary: Dict[str, Any] = {
        "id": exp_id,
        "start": start,
        "end": end,
        "book_regime": exp.get("book_regime") or {},
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        await init_zstock_database()
        pipe = StrategyPipeline(
            config_override={"book_regime": deepcopy(exp.get("book_regime") or {})}
        )
        fee = float((pipe.config.get("backtest") or {}).get("fee_rate", 0.0015))
        capital = float((pipe.config.get("backtest") or {}).get("initial_capital", 10_000_000))
        bt = Backtester(strategy_pipeline=pipe, initial_capital=capital, fee_rate=fee)
        result = await bt.run_real_data(
            start,
            end,
            use_precomputed_factors=True,
            strategy_config=pipe.config,
            output_dir=out_dir,
            verbose=False,
            save_outputs=True,
            ohlcv_query_concurrency=4,
            ohlcv_batch_size=60,
        )
        m = result.metrics or {}
        curve = pd.DataFrame(
            {
                "date": pd.to_datetime(result.daily_returns.index),
                "equity": result.equity_curve.values,
                "daily_return": result.daily_returns.values,
            }
        )
        summary.update(
            {
                "status": "ok",
                "elapsed_sec": round(time.time() - t0, 1),
                "total_return": float(m.get("total_return", 0.0)),
                "annualized_return": float(m.get("annualized_return", 0.0)),
                "max_drawdown": float(m.get("max_drawdown", 0.0)),
                "sharpe": float(m.get("sharpe", 0.0)),
                "rebalance_count": int(m.get("rebalance_count", 0)),
                "total_cost": float(m.get("total_cost", 0.0)),
                "output_dir": out_dir,
            }
        )
        for y in range(2020, 2027):
            summary.update(_year_stats(curve, y))
        log.info(
            "✅ %s %s~%s ret=%.2f%% sharpe=%.2f elapsed=%.0fs",
            exp_id,
            start,
            end,
            100 * summary["total_return"],
            summary["sharpe"],
            summary["elapsed_sec"],
        )
    except Exception as e:
        summary["status"] = "error"
        summary["error"] = f"{type(e).__name__}: {e}"
        summary["traceback"] = traceback.format_exc()
        summary["elapsed_sec"] = round(time.time() - t0, 1)
        log.error("❌ %s failed: %s", exp_id, e)
    finally:
        try:
            await close_zstock_database()
        except Exception:
            pass

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / f"{exp_id}.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def _run_one(exp: Dict[str, Any], output_root: str) -> Dict[str, Any]:
    os.environ["PYTHONPATH"] = str(PROJECT_ROOT)
    os.environ.setdefault("ZSTOCK_OHLCV_CONCURRENCY", "4")
    os.environ.setdefault("ZSTOCK_OHLCV_BATCH", "60")
    from zstock.common.logging_setup import setup_logging

    exp_id = exp["id"]
    setup_logging(
        f"night_bt_{exp_id}",
        stamped=False,
        file_fmt="%(asctime)s  %(levelname)-7s  %(message)s",
        console_fmt="%(asctime)s  %(levelname)-7s  %(message)s",
    )
    log = logging.getLogger(f"night_bt_{exp_id}")
    return asyncio.run(_run_one_async(exp, output_root, log))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--only", default="", help="逗号分隔 id")
    args = parser.parse_args(argv)

    from zstock.common.logging_setup import setup_logging

    setup_logging(
        "night_backtests",
        stamped=False,
        file_fmt="%(asctime)s  %(levelname)-7s  %(message)s",
        console_fmt="%(asctime)s  %(levelname)-7s  %(message)s",
    )

    exps = _experiments()
    if args.only.strip():
        allow = {x.strip() for x in args.only.split(",") if x.strip()}
        exps = [e for e in exps if e["id"] in allow]
    workers = max(1, min(int(args.workers), 3))
    out_root = Path(__file__).resolve().parent / "output" / "night_bt"
    out_root.mkdir(parents=True, exist_ok=True)
    meta = {
        "workers": workers,
        "n_experiments": len(exps),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "ids": [e["id"] for e in exps],
    }
    with open(out_root / "_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info("过夜回测启动 n=%s workers=%s → %s", len(exps), workers, out_root)
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_run_one, exp, str(out_root)): exp["id"] for exp in exps}
        for fut in as_completed(futs):
            exp_id = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                logger.error("worker crash %s: %s", exp_id, e)
                results.append({"id": exp_id, "status": "error", "error": str(e)})

    df = pd.DataFrame(results)
    if "id" in df.columns:
        df = df.sort_values("id")
    df.to_csv(out_root / "summary.csv", index=False, encoding="utf-8-sig")
    with open(out_root / "summary.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
    meta["ok"] = int((df.get("status") == "ok").sum()) if "status" in df.columns else 0
    meta["error"] = int((df.get("status") == "error").sum()) if "status" in df.columns else 0
    with open(out_root / "_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("全部结束 ok=%s error=%s → %s", meta["ok"], meta["error"], out_root / "summary.csv")
    return 0 if meta["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
