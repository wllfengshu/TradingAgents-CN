"""隔夜对照：验证「252 日峰值熔断 + 降单票集中度」，禁止网格搜索。

预提交 4 组（写死在 VARIANTS，跑完不得加组去贴指标）：
  baseline  当前 v1.30.0
  dd252     回撤看 252 日峰值：-12%×0.5、−20%×0
  size      单票 cap 0.30，持仓 <3 只时不打满
  combo     dd252 + size（建议组合）

主评：连续 2020-01-01 ~ 2026-09-17（对齐因子覆盖，避免 9/23 后缺 M1）
目标：年化 >= 10% 且全程 MDD >= -20%（两条件同时满足才算达标）
过拟合约束：
  - 本脚本不做参数搜索、不按结果改组
  - 2024-2026 只从连续净值切片确认，不当拟合目标
  - 2022 熊市收益若明显差于 baseline，记为警戒（熔断可能砍掉空仓边）

用法：
    python -m zstock.strategy_management.script.overnight_risk_validate
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

START_DEFAULT = "2020-01-01"
END_DEFAULT = "2026-09-17"
TARGET_CAGR = 0.10
TARGET_MDD = -0.20
# 2022 收益相对 baseline 恶化超过该绝对值则报警（预提交，不是搜索阈值）
GUARD_2022_DROP = 0.05

VARIANTS: List[Dict[str, Any]] = [
    {
        "id": "baseline",
        "title": "当前 v1.30.0，不改参数",
        "patch": {},
    },
    {
        "id": "dd252",
        "title": "252 日峰值熔断：-12%×0.5 / -20%×0",
        "patch": {
            "weak_regime_protection": {
                "drawdown_throttle": {
                    "enabled": True,
                    "lookback_days": 252,
                    "tiers": [
                        {"drawdown_threshold": 0.12, "scale_factor": 0.5},
                        {"drawdown_threshold": 0.20, "scale_factor": 0.0},
                    ],
                }
            }
        },
    },
    {
        "id": "size",
        "title": "单票 cap 0.30，持仓<3 不打满",
        "patch": {
            "portfolio_optimization": {
                "max_single_stock_cap": 0.30,
                "fill_weights_min_names": 3,
            }
        },
    },
    {
        "id": "combo",
        "title": "熔断 + 降集中度（建议组合）",
        "patch": {
            "weak_regime_protection": {
                "drawdown_throttle": {
                    "enabled": True,
                    "lookback_days": 252,
                    "tiers": [
                        {"drawdown_threshold": 0.12, "scale_factor": 0.5},
                        {"drawdown_threshold": 0.20, "scale_factor": 0.0},
                    ],
                }
            },
            "portfolio_optimization": {
                "max_single_stock_cap": 0.30,
                "fill_weights_min_names": 3,
            },
        },
    },
    {
        "id": "combo_ath",
        "title": "combo + 历史高点熔断（lookback=0）",
        "patch": {
            "weak_regime_protection": {
                "drawdown_throttle": {
                    "enabled": True,
                    "lookback_days": 0,
                    "tiers": [
                        {"drawdown_threshold": 0.12, "scale_factor": 0.5},
                        {"drawdown_threshold": 0.20, "scale_factor": 0.0},
                    ],
                }
            },
            "portfolio_optimization": {
                "max_single_stock_cap": 0.30,
                "fill_weights_min_names": 3,
            },
        },
    },
    {
        "id": "combo_vol12",
        "title": "集中度 cap0.30 + 波动目标12%（地板0.25，不空仓）",
        "patch": {
            "weak_regime_protection": {
                "vol_target": {
                    "enabled": True,
                    "target_vol": 0.12,
                    "lookback_days": 40,
                    "scale_floor": 0.25,
                    "scale_cap": 1.25,
                    "min_obs": 20,
                }
            },
            "portfolio_optimization": {
                "max_single_stock_cap": 0.30,
                "fill_weights_min_names": 3,
            },
        },
    },
]


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _yearly_from_curve(equity: pd.Series) -> List[Dict[str, Any]]:
    s = equity.copy()
    s.index = pd.to_datetime(s.index)
    rows: List[Dict[str, Any]] = []
    prev = 1.0
    for year, g in s.groupby(s.index.year):
        g = g.sort_index()
        end_nav = float(g.iloc[-1])
        ret = end_nav / prev - 1.0
        peak = g.cummax()
        mdd = float((g / peak - 1.0).min())
        rows.append(
            {
                "year": int(year),
                "return": ret,
                "mdd": mdd,
                "end_nav": end_nav,
            }
        )
        prev = end_nav
    return rows


def _slice_stats(equity: pd.Series, start: str, end: str) -> Dict[str, float]:
    s = equity.copy()
    s.index = pd.to_datetime(s.index)
    part = s.loc[(s.index >= start) & (s.index <= end)]
    if part.empty:
        return {}
    # 切片起点用前一日净值（连续路径），否则用切片首日
    before = s.loc[s.index < start]
    start_nav = float(before.iloc[-1]) if len(before) else float(part.iloc[0])
    end_nav = float(part.iloc[-1])
    ret = end_nav / start_nav - 1.0
    peak = part.cummax()
    mdd = float((part / peak - 1.0).min())
    rets = part.pct_change().dropna()
    std = float(rets.std()) if len(rets) else 0.0
    sharpe = float(rets.mean() / std * np.sqrt(252)) if std > 0 else 0.0
    years = max(len(part) / 252.0, 1e-6)
    cagr = (1.0 + ret) ** (1.0 / years) - 1.0 if ret > -1 else -1.0
    return {
        "return": ret,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "days": float(len(part)),
    }


def _invested_stats(daily_returns: pd.Series) -> Dict[str, float]:
    r = daily_returns.astype(float)
    active = r.abs() > 1e-12
    ra = r[active]
    if ra.empty:
        return {"invested_frac": 0.0, "invested_win_rate": 0.0, "invested_sharpe": 0.0}
    std = float(ra.std())
    return {
        "invested_frac": float(active.mean()),
        "invested_win_rate": float((ra > 0).mean()),
        "invested_sharpe": float(ra.mean() / std * np.sqrt(252)) if std > 0 else 0.0,
        "zero_frac": float((r == 0).mean()),
        "daily_win_rate": float((r > 0).mean()),
    }


def _pass_goal(cagr: float, mdd: float) -> bool:
    return cagr >= TARGET_CAGR and mdd >= TARGET_MDD


def _write_report(path: Path, rows: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
    lines = [
        "隔夜风险对照（预提交 4 组，无网格）",
        f"区间: {meta['start']} ~ {meta['end']}  连续持仓",
        f"目标: 年化 >= {TARGET_CAGR:.0%}  且  MDD >= {TARGET_MDD:.0%}",
        f"完成: {meta.get('finished_at', '')}  耗时 {meta.get('elapsed', '')}",
        "",
        f"{'组':<10} {'年化':>8} {'MDD':>9} {'夏普':>7} {'卡玛':>7} "
        f"{'2022':>8} {'24-26':>8} {'达标':>4}",
    ]
    by_id = {r["id"]: r for r in rows}
    base_2022 = float((by_id.get("baseline") or {}).get("ret_2022") or 0.0)
    for r in rows:
        hit = "YES" if r.get("hit_goal") else "no"
        warn = ""
        if r["id"] != "baseline" and r.get("ret_2022") is not None:
            if r["ret_2022"] < base_2022 - GUARD_2022_DROP:
                warn = "  !2022恶化"
        lines.append(
            f"{r['id']:<10} {r['cagr']:>7.2%} {r['mdd']:>8.2%} {r['sharpe']:>7.3f} "
            f"{r['calmar']:>7.3f} {r.get('ret_2022', 0):>7.2%} "
            f"{r.get('ret_2024_2026', 0):>7.2%} {hit:>4}{warn}"
        )
    lines.extend(["", "分年收益（连续路径，上年年末结转）:"])
    for r in rows:
        bits = [f"{y['year']} {y['return']:+.1%}/{y['mdd']:.1%}" for y in r.get("yearly") or []]
        lines.append(f"  {r['id']:<10} " + "  ".join(bits))
    lines.extend(
        [
            "",
            "说明: 达标=全程年化与全程MDD同时满足。2024-2026 为连续净值切片，不是独立回测。",
            "本脚本不会根据结果增补参数组。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("\n%s", "\n".join(lines))


async def _load_ohlcv(start: str, end: str) -> Dict[str, pd.DataFrame]:
    from zstock.common.utils.common_utils import normalize_date
    from zstock.data_management.query_service import get_data_query_service

    qs = get_data_query_service()
    all_stocks_docs, _ = await qs.get_all_stocks()
    mainboard_codes = [
        d["code"] for d in all_stocks_docs if d.get("is_mainboard") and not d.get("is_st")
    ]
    logger.info("主板非ST: %d 只", len(mainboard_codes))
    ohlcv_data: Dict[str, pd.DataFrame] = {}
    chunk_size = 500
    cpu = os.cpu_count() or 4
    conc = int(os.environ.get("ZSTOCK_OHLCV_CONCURRENCY", str(min(12, max(6, cpu)))))
    total = (len(mainboard_codes) + chunk_size - 1) // chunk_size
    failed = 0
    for ci, i in enumerate(range(0, len(mainboard_codes), chunk_size), 1):
        chunk = mainboard_codes[i : i + chunk_size]
        logger.info("OHLCV [%d/%d] %d 只", ci, total, len(chunk))
        try:
            batch = await qs.get_ohlcv_batch(
                chunk, start, end, batch_size=80, query_concurrency=conc
            )
            if batch:
                ohlcv_data.update(batch)
        except Exception as e:
            logger.warning("OHLCV 批次 %d 失败: %s", ci, e)
            failed += 1
    for df in ohlcv_data.values():
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].apply(normalize_date)
    logger.info("OHLCV 完成: %d 只 (失败批次 %d/%d)", len(ohlcv_data), failed, total)
    if not ohlcv_data:
        raise RuntimeError("无 OHLCV")
    return ohlcv_data


async def _run_one(
    *,
    variant: Dict[str, Any],
    base_params: Dict[str, Any],
    start: str,
    end: str,
    provider,
    factor_pipeline,
    capital: float,
    fee: float,
    rebalance: int,
    out_dir: Path,
) -> Dict[str, Any]:
    from zstock.strategy_management.script.backtester import Backtester

    params = _deep_merge(base_params, variant["patch"])
    bt = Backtester(
        fee_rate=fee, initial_capital=capital, factor_pipeline=factor_pipeline
    )
    t0 = time.time()
    result = await bt.run(
        start_date=start,
        end_date=end,
        ohlcv_provider=provider,
        rebalance_freq=rebalance,
        strategy_config=params,
        verbose=False,
        use_precomputed_factors=True,
    )
    elapsed = time.time() - t0
    seg = out_dir / variant["id"]
    seg.mkdir(parents=True, exist_ok=True)
    result.plot(output_path=str(seg / "equity.png"), title=f"{variant['id']} {start}~{end}")
    result.export_csv(str(seg))
    (seg / "summary.txt").write_text(result.summary(), encoding="utf-8")
    (seg / "variant.json").write_text(
        json.dumps({"id": variant["id"], "title": variant["title"], "patch": variant["patch"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    m = result.metrics
    inv = _invested_stats(result.daily_returns)
    yearly = _yearly_from_curve(result.equity_curve)
    s2022 = _slice_stats(result.equity_curve, "2022-01-01", "2022-12-31")
    s2426 = _slice_stats(result.equity_curve, "2024-01-01", end)
    cagr = float(m.get("annualized_return", 0.0))
    mdd = float(m.get("max_drawdown", 0.0))
    row = {
        "id": variant["id"],
        "title": variant["title"],
        "elapsed_sec": round(elapsed, 1),
        "cagr": cagr,
        "total_return": float(m.get("total_return", 0.0)),
        "mdd": mdd,
        "sharpe": float(m.get("sharpe", 0.0)),
        "calmar": float(m.get("calmar", 0.0)),
        "sortino": float(m.get("sortino", 0.0)),
        "win_rate": float(m.get("win_rate", 0.0)),
        "total_cost": float(m.get("total_cost", 0.0)),
        "hit_goal": _pass_goal(cagr, mdd),
        "ret_2022": s2022.get("return"),
        "mdd_2022": s2022.get("mdd"),
        "ret_2024_2026": s2426.get("return"),
        "cagr_2024_2026": s2426.get("cagr"),
        "mdd_2024_2026": s2426.get("mdd"),
        "yearly": yearly,
        **inv,
        "output_dir": str(seg),
    }
    logger.info(
        "DONE %s  cagr=%.2f%% mdd=%.2f%% sharpe=%.3f calmar=%.3f hit=%s  (%.0fs)",
        variant["id"],
        cagr * 100,
        mdd * 100,
        row["sharpe"],
        row["calmar"],
        row["hit_goal"],
        elapsed,
    )
    return row


async def async_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="隔夜风险对照（4 组预提交）")
    parser.add_argument("--start", default=START_DEFAULT)
    parser.add_argument("--end", default=END_DEFAULT)
    parser.add_argument("--output", default="", help="输出目录，默认 script/output/risk_validate_<ts>")
    parser.add_argument(
        "--ids",
        default="",
        help="只跑指定组，逗号分隔，例如 combo_ath",
    )
    args = parser.parse_args(argv)
    want = {x.strip() for x in args.ids.split(",") if x.strip()}
    variants = [v for v in VARIANTS if (not want or v["id"] in want)]
    if want:
        missing = want - {v["id"] for v in variants}
        if missing:
            logger.error("未知组: %s", ", ".join(sorted(missing)))
            return 2
    if not variants:
        logger.error("没有可跑的组")
        return 2

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output) if args.output else (
        Path(__file__).resolve().parent / "output" / f"risk_validate_{ts}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    fh = logging.FileHandler(out_dir / "run.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(fh)
    logging.getLogger("app").setLevel(logging.WARNING)

    from zstock.common.config.strategy_config import load_strategy_params
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict

    t_all = time.time()
    logger.info("=" * 70)
    logger.info("隔夜风险对照  %s → %s", args.start, args.end)
    logger.info("输出 %s", out_dir)
    logger.info("预提交组: %s", ", ".join(v["id"] for v in variants))
    logger.info("目标 年化>=%.0f%%  MDD>=%.0f%%  无网格", TARGET_CAGR * 100, TARGET_MDD * 100)
    logger.info("=" * 70)

    (out_dir / "precommit.json").write_text(
        json.dumps(
            {
                "start": args.start,
                "end": args.end,
                "target_cagr": TARGET_CAGR,
                "target_mdd": TARGET_MDD,
                "variants": variants,
                "note": "跑完不得按结果加组。生产 strategy_params.json 不会被改写。",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    await init_zstock_database()
    try:
        base_params = load_strategy_params()
        bt_cfg = (base_params or {}).get("backtest") or {}
        capital = float(bt_cfg.get("initial_capital", 1e7))
        fee = float(bt_cfg.get("fee_rate", 0.0015))
        rebalance = int(bt_cfg.get("rebalance_freq", 5))

        ohlcv = await _load_ohlcv(args.start, args.end)
        provider = make_ohlcv_provider_from_dict(ohlcv)

        fp = CrossSectionStrategyPipeline()
        logger.info("预加载因子 %s~%s ...", args.start, args.end)
        await fp.preload_precomputed_factors(args.start, args.end)

        rows: List[Dict[str, Any]] = []
        for i, variant in enumerate(variants, 1):
            logger.info("")
            logger.info("-" * 70)
            logger.info("[%d/%d] %s  %s", i, len(variants), variant["id"], variant["title"])
            logger.info("-" * 70)
            row = await _run_one(
                variant=variant,
                base_params=base_params,
                start=args.start,
                end=args.end,
                provider=provider,
                factor_pipeline=fp,
                capital=capital,
                fee=fee,
                rebalance=rebalance,
                out_dir=out_dir,
            )
            rows.append(row)
            (out_dir / "results.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

        elapsed = time.time() - t_all
        meta = {
            "start": args.start,
            "end": args.end,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed": f"{elapsed/3600:.2f}h",
        }
        _write_report(out_dir / "report.txt", rows, meta)
        (out_dir / "results.json").write_text(
            json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("全部完成 → %s", out_dir)
        return 0
    except Exception as e:
        logger.error("隔夜任务失败: %s", e)
        import traceback

        traceback.print_exc()
        return 1
    finally:
        try:
            await close_zstock_database()
        except Exception:
            pass


def main() -> int:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
