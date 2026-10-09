# -*- coding: utf-8 -*-
"""Round 2：在 round1 年特异边之上做灯号切换 / 指数切换 / 生产缩仓。

    python -m zstock.strategy_management.script.explore_v2_round2
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.explore_v2_edge import (
    CAPITAL,
    CURRENT,
    FEE,
    HS300,
    NOM4,
    PERIODS,
    REBALANCE,
    _avg_exposure,
    _clear_caches,
    _consistency_factors,
    _eval_variant,
    _f33_only_factors,
    _fmt,
    _ic_align_factors,
    _naked_overlay,
    _naked_runtime,
    _write_report,
)

logger = logging.getLogger(__name__)
OUTPUT_DIR = (
    Path(__file__).resolve().parent
    / "output"
    / f"explore_v2_round2_{datetime.now().strftime('%Y%m%d')}"
)


def _book(factors: Dict[str, Any], gate: str) -> Dict[str, Any]:
    coop = {"skip_filter": gate == "off", "gate_mode": gate, "threshold_pct": 0.01}
    return {"active_factors": factors, "cooperative_force": coop}


def _prod_overlay() -> Dict[str, Any]:
    return {
        "factor_decay": {"enabled": False},
        "final_score": {"top_k": 5, "by_regime": {"reversal": {"top_k": 3}}},
    }


def _variants() -> List[Dict[str, Any]]:
    naked = _naked_overlay()
    f33 = _f33_only_factors()
    cons = _consistency_factors()
    align = _ic_align_factors()
    return [
        {
            "id": "green_f33_yellow_cons",
            "title": "绿灯淡化连板满仓，黄灯身份溢价+一致性满仓",
            "runtime": "naked",
            "factor": {
                **naked,
                "books_by_grade": {
                    "green": _book(f33, "off"),
                    "yellow": _book(cons, "off"),
                },
                **_book(f33, "off"),
            },
        },
        {
            "id": "green_f33_yellow_align",
            "title": "绿灯f33满仓，黄灯IC对齐+M4小仓",
            "runtime": "naked",
            "factor": {
                **naked,
                "books_by_grade": {
                    "green": _book(f33, "off"),
                    "yellow": _book(align, "inflow"),
                },
                **_book(f33, "off"),
            },
        },
        {
            "id": "green_only_f33",
            "title": "只在绿灯做f33，黄灯空仓",
            "runtime": "naked",
            "factor": {
                **naked,
                "market_overlay": {
                    "position_scale_green": 1.0,
                    "position_scale_yellow": 0.0,
                },
                "active_factors": f33,
                "cooperative_force": {"skip_filter": True, "gate_mode": "off"},
                "final_score": {
                    "top_k": 5,
                    "by_regime": {"reversal": {"top_k": 5}},
                    "weights": {"sector": 0.2, "dragon": 0.8, "cooperative": 0.0},
                },
            },
        },
        {
            "id": "hs300_60_switch",
            "title": "沪深300近60日>0用f33，否则用一致性（关M4）",
            "runtime": "naked",
            "switch": "hs300_60",
            "up": {**naked, **_book(f33, "off"), "final_score": {
                "top_k": 5, "by_regime": {"reversal": {"top_k": 5}},
                "weights": {"sector": 0.2, "dragon": 0.8, "cooperative": 0.0},
            }},
            "down": {**naked, **_book(cons, "off")},
        },
        {
            "id": "f33_prod_overlay",
            "title": "f33关M4 + 生产黄灯0.5/reversal top3",
            "runtime": "prod",
            "factor": {
                **_prod_overlay(),
                "active_factors": f33,
                "cooperative_force": {"skip_filter": True, "gate_mode": "off"},
                "final_score": {
                    "top_k": 5,
                    "by_regime": {"reversal": {"top_k": 3}},
                    "weights": {"sector": 0.2, "dragon": 0.8, "cooperative": 0.0},
                },
            },
        },
        {
            "id": "cons_prod_overlay",
            "title": "一致性关M4 + 生产黄灯0.5",
            "runtime": "prod",
            "factor": {
                **_prod_overlay(),
                "active_factors": cons,
                "cooperative_force": {"skip_filter": True, "gate_mode": "off"},
            },
        },
        {
            "id": "coop0_prod_overlay",
            "title": "合力权重0关M4 + 生产黄灯0.5",
            "runtime": "prod",
            "factor": {
                **_prod_overlay(),
                "cooperative_force": {"skip_filter": True, "gate_mode": "off"},
                "final_score": {
                    "top_k": 5,
                    "by_regime": {"reversal": {"top_k": 3}},
                    "weights": {"sector": 0.45, "dragon": 0.55, "cooperative": 0.0},
                },
            },
        },
    ]


class Hs300SwitchPipeline:
    """按滞后 60 日沪深300收益切换两套 config_override。"""

    def __init__(self, up: Dict[str, Any], down: Dict[str, Any]):
        from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

        self._up_cfg = up
        self._down_cfg = down
        self._up = CrossSectionStrategyPipeline(config_override=up)
        self._down = CrossSectionStrategyPipeline(config_override=down)
        self._closes: Optional[pd.Series] = None
        self._active = self._up

    @property
    def _query_service(self):
        return self._up._query_service

    async def preload_precomputed_factors(self, start: str, end: str) -> None:
        await self._up.preload_precomputed_factors(start, end)
        self._down._pc_market = self._up._pc_market
        self._down._pc_sectors = self._up._pc_sectors
        self._down._pc_dragons = self._up._pc_dragons
        self._down._pc_forces = self._up._pc_forces
        self._down._pc_range = self._up._pc_range
        self._down._all_stocks_cache = self._up._all_stocks_cache
        self._down._sectors_cache = self._up._sectors_cache
        await self._load_hs300()

    async def _load_hs300(self) -> None:
        qs = self._up._query_service
        try:
            df, _src = await qs.get_ohlcv("399300", "2023-01-01", "2026-09-08")
        except ValueError:
            df, _src = await qs.get_ohlcv("000300", "2023-01-01", "2026-09-08")
        if df is None or df.empty:
            raise RuntimeError("无 399300 OHLCV")
        col = "close" if "close" in df.columns else df.columns[-1]
        s = df.set_index("trade_date")[col].astype(float).sort_index()
        self._closes = s

    def _ret60(self, td: str) -> float:
        s = self._closes
        if s is None or td not in s.index:
            prev = s.index[s.index <= td]
            if len(prev) == 0:
                return 0.0
            td = str(prev[-1])
        loc = s.index.get_loc(td)
        if loc < 60:
            return 0.0
        a = float(s.iloc[loc])
        b = float(s.iloc[loc - 60])
        if b == 0:
            return 0.0
        return a / b - 1.0

    async def score_signals(self, trade_date: str):
        ret = self._ret60(trade_date)
        self._active = self._down if ret < 0 else self._up
        return await self._active.score_signals(trade_date)


def _prod_runtime() -> Dict[str, Any]:
    from zstock.common.config.strategy_config import build_runtime_config

    cfg = build_runtime_config()
    cfg["adaptive_rebalance"] = {"enabled": False}
    return cfg


async def _run_year(pipe, provider, start: str, end: str, runtime: str, seg_dir: Path) -> Dict[str, Any]:
    from zstock.strategy_management.script.backtester import Backtester

    _clear_caches()
    seg_dir.mkdir(parents=True, exist_ok=True)
    bt = Backtester(fee_rate=FEE, initial_capital=CAPITAL, factor_pipeline=pipe)
    sc = _naked_runtime() if runtime == "naked" else _prod_runtime()
    result = await bt.run(
        start_date=start,
        end_date=end,
        ohlcv_provider=provider,
        rebalance_freq=REBALANCE,
        strategy_config=sc,
        use_precomputed_factors=True,
        verbose=False,
    )
    (seg_dir / "summary.txt").write_text(result.summary(), encoding="utf-8")
    m = result.metrics
    return {
        "status": "success",
        "total_return": float(m.get("total_return", 0)),
        "sharpe": float(m.get("sharpe", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
        "avg_exposure": _avg_exposure(result.holdings_log),
        "total_cost": float(m.get("total_cost", 0)),
    }


async def _make_pipe(spec: Dict[str, Any]):
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

    if spec.get("switch") == "hs300_60":
        return Hs300SwitchPipeline(spec["up"], spec["down"])
    return CrossSectionStrategyPipeline(config_override=spec["factor"])


async def async_main() -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.script.网格搜索.grid_search_real import load_ohlcv
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("zstock", "zstock.factor_management", "zstock.strategy_management", "app"):
        logging.getLogger(name).setLevel(logging.ERROR)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: Dict[str, Any] = {}
    verdicts: Dict[str, Tuple[str, str]] = {}
    prev = OUTPUT_DIR / "explore_results.json"
    if prev.exists():
        old = json.loads(prev.read_text(encoding="utf-8"))
        all_rows = old.get("results") or {}
        verdicts = {
            k: (v["grade"], v["why"]) for k, v in (old.get("verdicts") or {}).items()
        }
        print(f"续跑，已有 {list(all_rows)}", flush=True)
    found = None

    await init_zstock_database()
    try:
        print("加载 OHLCV", flush=True)
        ohlcv = await load_ohlcv("2024-01-01", "2026-09-08")
        provider = make_ohlcv_provider_from_dict(ohlcv)

        for spec in _variants():
            vid = spec["id"]
            if vid in all_rows:
                print(f"跳过已完成 {vid}", flush=True)
                continue
            print(f"\n=== {vid} · {spec['title']} ===", flush=True)
            _clear_caches()
            pipe = await _make_pipe(spec)
            years: Dict[str, Dict[str, Any]] = {}
            for year, start, end in PERIODS:
                row = await _run_year(
                    pipe, provider, start, end, spec["runtime"],
                    OUTPUT_DIR / "segments" / f"{vid}_{year}",
                )
                years[year] = row
                print(
                    f"  {year} ret={row['total_return']*100:+.2f}% sharpe={row['sharpe']:.2f} "
                    f"mdd={row['max_drawdown']*100:.1f}% exp={row['avg_exposure']*100:.1f}%",
                    flush=True,
                )
            grade, why = _eval_variant(years)
            all_rows[vid] = years
            verdicts[vid] = (grade, why)
            print(f"  评级 {grade}: {why}", flush=True)
            payload = {
                "results": all_rows,
                "verdicts": {k: {"grade": v[0], "why": v[1]} for k, v in verdicts.items()},
            }
            with open(OUTPUT_DIR / "explore_results.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            (OUTPUT_DIR / "explore_report.txt").write_text(
                _write_report(all_rows, verdicts), encoding="utf-8"
            )
            if grade in ("strong",):
                found = vid
                print(f"已证明强边，停止: {vid}", flush=True)
                break

        report = _write_report(all_rows, verdicts)
        print("\n" + report, flush=True)
        return 0
    finally:
        await close_zstock_database()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
