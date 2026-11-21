"""
Book 账户层过夜对照实验。

用法：
  python -m zstock.strategy_management.script.run_book_experiments
  python -m zstock.strategy_management.script.run_book_experiments --workers 2

约束：Mongo 友好（默认 2 进程并行，单回测 OHLCV 子查询并发压到 4）。
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

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("book_experiments")

START = "2025-01-01"
END = "2026-11-13"
BOOK = {
    "511260", "511010", "511090", "511520", "518880",
    "159928", "512010", "512880", "512660", "512400", "512800", "512480",
}
IND = {"159928", "512010", "512880", "512660", "512400", "512800", "512480"}


def _experiments() -> List[Dict[str, Any]]:
    """必跑 + 余力组。book_regime 为相对基线的覆盖字段。"""
    return [
        {"id": "A0_baseline", "book_regime": {}},
        {"id": "A1_book_off", "book_regime": {"enabled": False}},
        {"id": "B1_atk_g1", "book_regime": {"confirm_green_days": 1}},
        {"id": "B2_atk_g2", "book_regime": {"confirm_green_days": 2}},
        {"id": "B3_atk_g1_rc3", "book_regime": {"confirm_green_days": 1, "regime_confirm": 3}},
        {"id": "B5_atk_g1_leave", "book_regime": {
            "confirm_green_days": 1,
            "attack_skip_neutral_on_green": True,
        }},
        {"id": "B6_atk_g1_rc3_leave", "book_regime": {
            "confirm_green_days": 1,
            "regime_confirm": 3,
            "attack_skip_neutral_on_green": True,
        }},
        {"id": "D1_neu_dd6", "book_regime": {"industry_stop_loss_pct": -0.06}},
        {"id": "D2_neu_dd8", "book_regime": {"industry_stop_loss_pct": -0.08}},
        {"id": "D3_neu_dd6_rel", "book_regime": {
            "industry_stop_loss_pct": -0.06,
            "industry_rel_index_gate": -0.03,
        }},
        {"id": "D4_neu_dd6_breadth", "book_regime": {
            "industry_stop_loss_pct": -0.06,
            "industry_min_positive_mom": 2,
            "industry_narrow_max_weight": 0.30,
        }},
        {"id": "D5_neu_dd6_rel_br", "book_regime": {
            "industry_stop_loss_pct": -0.06,
            "industry_rel_index_gate": -0.03,
            "industry_min_positive_mom": 2,
            "industry_narrow_max_weight": 0.30,
        }},
        {"id": "D6_neu_ma20_exit", "book_regime": {"industry_exit_below_ma20": True}},
        {"id": "F1_neu_vol10", "book_regime": {"vol_target": 0.10, "max_weight": 0.25}},
        {"id": "F2_neu_vol12", "book_regime": {"vol_target": 0.12, "max_weight": 0.30}},
        {"id": "E1_combo_mild", "book_regime": {
            "confirm_green_days": 2,
            "industry_stop_loss_pct": -0.06,
            "vol_target": 0.12,
            "max_weight": 0.30,
        }},
        {"id": "E2_combo_mid", "book_regime": {
            "confirm_green_days": 1,
            "regime_confirm": 3,
            "industry_stop_loss_pct": -0.06,
            "industry_rel_index_gate": -0.03,
            "vol_target": 0.12,
            "max_weight": 0.30,
        }},
        {"id": "E3_combo_strong", "book_regime": {
            "confirm_green_days": 1,
            "regime_confirm": 3,
            "attack_skip_neutral_on_green": True,
            "industry_stop_loss_pct": -0.06,
            "industry_rel_index_gate": -0.03,
            "industry_min_positive_mom": 2,
            "industry_narrow_max_weight": 0.30,
            "vol_target": 0.10,
            "max_weight": 0.25,
        }},
        {"id": "E4_combo_atk_neu", "book_regime": {
            "confirm_green_days": 1,
            "attack_skip_neutral_on_green": True,
            "industry_stop_loss_pct": -0.06,
            "industry_rel_index_gate": -0.03,
            "industry_min_positive_mom": 2,
            "industry_narrow_max_weight": 0.30,
        }},
        {"id": "H1_def_gold_lag2", "book_regime": {"gold_ma_confirm_days": 2}},
        {"id": "H3_E2_gold_lag2", "book_regime": {
            "confirm_green_days": 1,
            "regime_confirm": 3,
            "industry_stop_loss_pct": -0.06,
            "industry_rel_index_gate": -0.03,
            "vol_target": 0.12,
            "max_weight": 0.30,
            "gold_ma_confirm_days": 2,
        }},
    ]


def _year_stats(curve: pd.DataFrame, year: int) -> Dict[str, float]:
    sub = curve[curve["date"].dt.year == year]
    if sub.empty:
        return {}
    rets = sub["daily_return"].astype(float)
    total = float((1.0 + rets).prod() - 1.0)
    n = len(sub)
    ann = float((1.0 + total) ** (252.0 / max(n, 1)) - 1.0)
    eq = (1.0 + rets).cumprod()
    mdd = float((eq / eq.cummax() - 1.0).min())
    return {
        f"{year}_ret": total,
        f"{year}_ann": ann,
        f"{year}_mdd": mdd,
        f"{year}_days": float(n),
    }


def _exposure_stats(hold: pd.DataFrame, curve: pd.DataFrame) -> Dict[str, float]:
    hold = hold.copy()
    hold["code"] = hold["code"].astype(str).str.zfill(6)
    hold["trade_date"] = pd.to_datetime(hold["trade_date"])
    curve = curve.copy()
    curve["date"] = pd.to_datetime(curve["date"])
    rows = []
    h = hold.sort_values("trade_date")
    for _, r in curve.iterrows():
        td = r["date"]
        hh = h[h["trade_date"] <= td]
        if hh.empty:
            continue
        g = h[h["trade_date"] == hh["trade_date"].max()]
        w_stock = float(g.loc[~g["code"].isin(BOOK), "weight"].sum())
        w_ind = float(g.loc[g["code"].isin(IND), "weight"].sum())
        if w_stock >= 0.5:
            mode = "attack"
        elif w_ind >= 0.25:
            mode = "neutral"
        else:
            mode = "defense"
        rows.append({"w_stock": w_stock, "w_ind": w_ind, "mode": mode, "ret": float(r["daily_return"])})
    df = pd.DataFrame(rows)
    if df.empty:
        return {}
    out = {
        "avg_w_stock": float(df["w_stock"].mean()),
        "avg_w_ind": float(df["w_ind"].mean()),
        "attack_day_frac": float((df["mode"] == "attack").mean()),
        "neutral_day_frac": float((df["mode"] == "neutral").mean()),
        "defense_day_frac": float((df["mode"] == "defense").mean()),
    }
    # sleeve path contrib approx via daily port on mode days
    for mode in ("attack", "neutral", "defense"):
        sub = df[df["mode"] == mode]["ret"]
        out[f"port_on_{mode}"] = float((1.0 + sub).prod() - 1.0) if len(sub) else 0.0
    return out


async def _run_one_async(exp: Dict[str, Any], output_root: str, log: logging.Logger) -> Dict[str, Any]:
    """同一 event loop 内：连库 → 回测 → 关库（避免 asyncio.run 二次关闭 Mongo）。"""
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester

    exp_id = exp["id"]
    t0 = time.time()
    out_dir = f"output/book_exp/{exp_id}"
    summary: Dict[str, Any] = {
        "id": exp_id,
        "book_regime": exp.get("book_regime") or {},
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        await init_zstock_database()
        pipe = StrategyPipeline(config_override={"book_regime": deepcopy(exp.get("book_regime") or {})})
        fee = float((pipe.config.get("backtest") or {}).get("fee_rate", 0.0015))
        capital = float((pipe.config.get("backtest") or {}).get("initial_capital", 10_000_000))
        bt = Backtester(
            strategy_pipeline=pipe,
            initial_capital=capital,
            fee_rate=fee,
        )
        result = await bt.run_real_data(
            START,
            END,
            use_precomputed_factors=True,
            strategy_config=pipe.config,
            output_dir=out_dir,
            verbose=False,
            save_outputs=True,
            ohlcv_query_concurrency=4,
            ohlcv_batch_size=60,
        )
        m = result.metrics or {}
        curve = pd.DataFrame({
            "date": pd.to_datetime(result.daily_returns.index),
            "equity": result.equity_curve.values,
            "daily_return": result.daily_returns.values,
        })
        flat_hold: List[Dict[str, Any]] = []
        for snap in (result.holdings_log or []):
            for h in snap.get("holdings") or []:
                row = {"trade_date": snap.get("trade_date")}
                row.update(h)
                flat_hold.append(row)
        hold = pd.DataFrame(flat_hold) if flat_hold else pd.DataFrame()
        summary.update({
            "status": "ok",
            "elapsed_sec": round(time.time() - t0, 1),
            "total_return": float(m.get("total_return", 0.0)),
            "annualized_return": float(m.get("annualized_return", 0.0)),
            "max_drawdown": float(m.get("max_drawdown", 0.0)),
            "sharpe": float(m.get("sharpe", 0.0)),
            "rebalance_count": int(m.get("rebalance_count", 0)),
            "total_cost": float(m.get("total_cost", 0.0)),
            "output_dir": out_dir,
        })
        summary.update(_year_stats(curve, 2025))
        summary.update(_year_stats(curve, 2026))
        if not hold.empty:
            summary.update(_exposure_stats(hold, curve))
        log.info("✅ %s done ret=%.2f%% elapsed=%.0fs", exp_id, 100 * summary["total_return"], summary["elapsed_sec"])
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
    """子进程入口：跑单组回测并返回摘要。"""
    os.environ["PYTHONPATH"] = str(PROJECT_ROOT)
    # 压低单进程 Mongo 查询并发，避免双进程打满
    os.environ.setdefault("ZSTOCK_OHLCV_CONCURRENCY", "4")
    os.environ.setdefault("ZSTOCK_OHLCV_BATCH", "60")

    from zstock.common.logging_setup import setup_logging

    exp_id = exp["id"]
    setup_logging(
        f"book_exp_{exp_id}",
        stamped=False,
        file_fmt="%(asctime)s  %(levelname)-7s  %(message)s",
        console_fmt="%(asctime)s  %(levelname)-7s  %(message)s",
    )
    log = logging.getLogger(f"book_exp_{exp_id}")
    return asyncio.run(_run_one_async(exp, output_root, log))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Book 过夜对照实验")
    parser.add_argument("--workers", type=int, default=2, help="并行进程数，默认 2（护 Mongo）")
    parser.add_argument("--only", default="", help="只跑这些 id，逗号分隔")
    args = parser.parse_args(argv)

    from zstock.common.logging_setup import setup_logging

    setup_logging(
        "book_experiments",
        stamped=False,
        file_fmt="%(asctime)s  %(levelname)-7s  %(message)s",
        console_fmt="%(asctime)s  %(levelname)-7s  %(message)s",
    )

    exps = _experiments()
    if args.only.strip():
        allow = {x.strip() for x in args.only.split(",") if x.strip()}
        exps = [e for e in exps if e["id"] in allow]
    workers = max(1, min(int(args.workers), 3))
    out_root = Path(__file__).resolve().parent / "output" / "book_exp"
    out_root.mkdir(parents=True, exist_ok=True)
    meta = {
        "start": START,
        "end": END,
        "workers": workers,
        "n_experiments": len(exps),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "ids": [e["id"] for e in exps],
    }
    with open(out_root / "_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info("过夜实验启动 n=%s workers=%s → %s", len(exps), workers, out_root)
    results: List[Dict[str, Any]] = []
    # ProcessPool：每进程独立 Mongo 连接；workers≤2
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(_run_one, exp, str(out_root)): exp["id"]
            for exp in exps
        }
        for fut in as_completed(futs):
            exp_id = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                logger.error("worker crash %s: %s", exp_id, e)
                results.append({"id": exp_id, "status": "error", "error": str(e)})

    df = pd.DataFrame(results)
    df = df.sort_values("id") if "id" in df.columns else df
    csv_path = out_root / "summary.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    with open(out_root / "summary.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
    meta["ok"] = int((df.get("status") == "ok").sum()) if "status" in df.columns else 0
    meta["error"] = int((df.get("status") == "error").sum()) if "status" in df.columns else 0
    with open(out_root / "_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("全部结束 ok=%s error=%s → %s", meta["ok"], meta["error"], csv_path)
    return 0 if meta["error"] == 0 else 1


if __name__ == "__main__":
    # Windows 进程池需要
    raise SystemExit(main())
