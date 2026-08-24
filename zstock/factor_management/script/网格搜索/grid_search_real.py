"""
真实参数网格搜索（预计算因子 + 含成本组合回测）

相对旧版改动：
- 不再用假数据 / 单日信号质量代理
- 对接 Backtester + CrossSectionStrategyPipeline.score_signals
- 搜索空间对齐当前 strategy_params（含 P1 风控/换手）
- 不写坏正式配置；每组用内存覆盖 + 临时 Risk/Turnover 实例
- 目标函数：卡玛 + 夏普，并对过大回撤惩罚

推荐流程（勿用 2024）：
    1) 2026 调参：
       python -m zstock.factor_management.script.grid_search_real \\
           --start 2026-01-05 --end 2026-07-27 \\
           --max-combinations 40 --space wide --fee 0.0015
    2) 把 best_strategy_params.json 拷到 strategy_params.json（或 --apply）
    3) 2025 样本外回测：
       python -m zstock.strategy_management.script.backtester \\
           --start 2025-01-02 --end 2025-12-31 --precomputed --fee 0.0015
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import pickle
import random
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.common.utils.resource_budget import cap_worker_count, compute_resource_budget

logger = logging.getLogger(__name__)

# ProcessPool 子进程共享（initializer 注入）
_POOL_OHLCV: Optional[dict] = None
_POOL_SCORING_METHOD: str = "linear"

_BASE_PARAMS_PATH = (
    PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"
)


def _quiet_loggers() -> None:
    for name in (
        "zstock",
        "zstock.factor_management",
        "zstock.strategy_management",
        "zstock.data_management",
        "app",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def _clear_strategy_caches() -> None:
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.risk_manager import _load_risk_limits_from_config

    StrategyPipeline._config_cache = None
    _load_risk_limits_from_config._cache = None


class RealGridSearchOptimizer:
    """预计算因子路径上的真实网格搜索。"""

    def __init__(
        self, output_dir: Optional[Path] = None, scoring_method: str = "linear"
    ) -> None:
        self.output_dir = Path(
            output_dir
            or (
                Path(__file__).resolve().parent
                / "output"
                / datetime.now().strftime("%Y%m%d_%H%M%S")
            )
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.scoring_method = scoring_method
        self.results: List[Dict[str, Any]] = []
        with open(_BASE_PARAMS_PATH, "r", encoding="utf-8") as f:
            self.base_params: Dict[str, Any] = json.load(f)

    @staticmethod
    def get_parameter_space(space: str = "wide") -> Dict[str, List[Any]]:
        """
        core: 精简
        wide: 加大范围（默认推荐，配合随机采样）
        full: 最细，需更大 max-combinations
        """
        if space == "core":
            return {
                "top_sectors": [2, 3, 4],
                "top_per_sector": [2, 3],
                "top_k": [3, 5, 7],
                "coop_threshold": [0.02, 0.03, 0.05],
                "weight_sector": [0.30, 0.40, 0.50],
                "weight_dragon": [0.30, 0.35, 0.40],
                "rebalance_freq": [3, 5, 8],
                "buffer_threshold": [0.20, 0.30, 0.40],
                "min_hold_days": [3, 5],
                "hard_stop_loss_pct": [-0.05, -0.06, -0.08],
                "max_weight_per_stock": [0.10, 0.12, 0.15],
                "max_sector_exposure": [0.25, 0.30, 0.35],
            }
        if space == "full":
            return {
                "top_sectors": [1, 2, 3, 4, 5],
                "top_per_sector": [1, 2, 3, 4, 5],
                "top_k": [3, 5, 7, 10],
                "coop_threshold": [0.01, 0.02, 0.03, 0.05, 0.08],
                "weight_sector": [0.20, 0.30, 0.40, 0.50, 0.60],
                "weight_dragon": [0.20, 0.30, 0.35, 0.40, 0.50],
                "rebalance_freq": [1, 3, 5, 8, 10],
                "buffer_threshold": [0.10, 0.20, 0.30, 0.40, 0.50],
                "min_hold_days": [1, 3, 5, 8],
                "hard_stop_loss_pct": [-0.04, -0.05, -0.06, -0.08, -0.10],
                "max_weight_per_stock": [0.08, 0.10, 0.12, 0.15, 0.20],
                "max_sector_exposure": [0.20, 0.25, 0.30, 0.40, 0.50],
            }
        # wide（默认）：进攻/防守都覆盖，范围明显大于旧版
        return {
            "top_sectors": [1, 2, 3, 4, 5],
            "top_per_sector": [1, 2, 3, 4],
            "top_k": [3, 5, 7, 10],
            "coop_threshold": [0.01, 0.02, 0.03, 0.05, 0.08],
            "weight_sector": [0.20, 0.30, 0.40, 0.50, 0.60],
            "weight_dragon": [0.20, 0.30, 0.35, 0.40, 0.50],
            "rebalance_freq": [1, 3, 5, 8, 10],
            "buffer_threshold": [0.10, 0.20, 0.30, 0.40, 0.50],
            "min_hold_days": [1, 3, 5, 8],
            "hard_stop_loss_pct": [-0.04, -0.05, -0.06, -0.08, -0.10],
            "max_weight_per_stock": [0.08, 0.10, 0.12, 0.15, 0.20],
            "max_sector_exposure": [0.20, 0.25, 0.30, 0.40, 0.50],
        }

    @staticmethod
    def baseline_params() -> Dict[str, Any]:
        """当前正式配置（对照点，与 strategy_params.json v1.11.0 同步）。"""
        return {
            "top_sectors": 4,
            "top_per_sector": 2,
            "top_k": 3,
            "coop_threshold": 0.03,
            "weight_sector": 0.40,
            "weight_dragon": 0.35,
            "rebalance_freq": 8,
            "buffer_threshold": 0.40,
            "min_hold_days": 3,
            "hard_stop_loss_pct": -0.08,
            "max_weight_per_stock": 0.12,
            "max_sector_exposure": 0.25,
        }

    @staticmethod
    def validate_parameters(params: Dict[str, Any]) -> Tuple[bool, str]:
        ws = float(params["weight_sector"])
        wd = float(params["weight_dragon"])
        wc = 1.0 - ws - wd
        if wc < 0.05 or wc > 0.70:
            return False, f"weight_coop={wc:.3f} 不合理"
        if float(params["max_weight_per_stock"]) > float(params["max_sector_exposure"]) + 1e-9:
            return False, "单股上限不应大于板块上限"
        # top_k 可小于 top_sectors*top_per_sector（最终截断），但不宜小于单板块名额
        if int(params["top_k"]) < int(params["top_per_sector"]):
            return False, "top_k < top_per_sector 无意义"
        if int(params["min_hold_days"]) > int(params["rebalance_freq"]) * 3:
            # 持有期过长相对调仓频率会几乎锁死换手，允许但不推荐；仅拦截极端
            pass
        return True, ""

    def _build_factor_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        cfg = copy.deepcopy(self.base_params)
        ws = float(params["weight_sector"])
        wd = float(params["weight_dragon"])
        cfg["sector_layer"]["top_sectors"] = int(params["top_sectors"])
        cfg["dragon_layer"]["top_per_sector"] = int(params["top_per_sector"])
        cfg["cooperative_force"]["threshold_pct"] = float(params["coop_threshold"])
        cfg["final_score"]["top_k"] = int(params["top_k"])
        cfg["final_score"]["weights"] = {
            "sector": ws,
            "dragon": wd,
            "cooperative": 1.0 - ws - wd,
        }
        cfg["portfolio"] = {
            "max_weight_per_stock": float(params["max_weight_per_stock"]),
            "max_sector_exposure": float(params["max_sector_exposure"]),
        }
        cfg["turnover_control"] = {
            "buffer_threshold": float(params["buffer_threshold"]),
            "min_hold_days": int(params["min_hold_days"]),
        }
        cfg["exit_rules"]["hard_stop_loss_pct"] = float(params["hard_stop_loss_pct"])
        cfg["backtest"]["rebalance_freq"] = int(params["rebalance_freq"])
        cfg["scoring_method"] = self.scoring_method
        return cfg

    def _strategy_runtime_config(self, params: Dict[str, Any]) -> Dict[str, Dict]:
        top_k = int(params["top_k"])
        return {
            "portfolio_optimization": {
                "min_holdings": max(1, top_k - 2),
                "max_holdings": top_k,
                "max_weight_per_stock": float(params["max_weight_per_stock"]),
                "weighting": "score",
            },
            "risk_management": {
                "hard_stop_loss_pct": float(params["hard_stop_loss_pct"]),
                "max_sector_exposure": float(params["max_sector_exposure"]),
            },
            "turnover_control": {
                "buffer_threshold": float(params["buffer_threshold"]),
                "min_hold_days": int(params["min_hold_days"]),
            },
        }

    @staticmethod
    def objective(metrics: Dict[str, float]) -> float:
        """
        综合分：夏普 + 0.8*卡玛 - 回撤惩罚。
        负收益额外惩罚，避免「空仓躺平」虚高夏普。
        """
        sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
        calmar = float(metrics.get("calmar", 0.0) or 0.0)
        mdd = abs(float(metrics.get("max_drawdown", 0.0) or 0.0))
        total_ret = float(metrics.get("total_return", 0.0) or 0.0)
        ann = float(metrics.get("annualized_return", 0.0) or 0.0)

        score = sharpe + 0.8 * calmar
        if mdd > 0.15:
            score -= (mdd - 0.15) * 20.0
        if mdd > 0.25:
            score -= (mdd - 0.25) * 40.0
        if total_ret < 0:
            score -= 2.0 + abs(total_ret) * 5.0
        # 轻微偏好正收益
        score += min(max(ann, -0.5), 0.8) * 0.5
        return float(score)

    async def run_one(
        self,
        params: Dict[str, Any],
        ohlcv_provider,
        start: str,
        end: str,
        capital: float,
        fee: float,
    ) -> Dict[str, Any]:
        from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
        from zstock.strategy_management.pipeline import StrategyPipeline
        from zstock.strategy_management.portfolio_optimizer import PortfolioOptimizer
        from zstock.strategy_management.risk_manager import RiskManager
        from zstock.strategy_management.signal_generator import SignalGenerator
        from zstock.strategy_management.script.backtester import Backtester
        from zstock.strategy_management.turnover_controller import TurnoverController

        valid, err = self.validate_parameters(params)
        if not valid:
            return {**params, "status": f"invalid:{err}", "objective": -1e9}

        _clear_strategy_caches()
        factor_cfg = self._build_factor_config(params)

        # 临时配置文件：供 FactorPipeline / StrategyPipeline 默认加载同源参数
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            json.dump(factor_cfg, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name

        try:
            # 临时把 StrategyPipeline 的默认路径切到本组合配置
            from zstock.strategy_management import pipeline as sp_mod
            from zstock.strategy_management import risk_manager as rm_mod

            old_sp = sp_mod._STRATEGY_PARAMS_PATH
            old_rm = rm_mod._STRATEGY_PARAMS_PATH
            sp_mod._STRATEGY_PARAMS_PATH = Path(tmp_path)
            rm_mod._STRATEGY_PARAMS_PATH = Path(tmp_path)
            _clear_strategy_caches()

            factor_pipeline = CrossSectionStrategyPipeline(config_path=tmp_path)
            top_k = int(params["top_k"])
            risk = RiskManager(
                risk_limits={
                    "top_k": top_k,
                    "min_holdings": max(1, top_k - 2),
                    "max_holdings": top_k * 2,
                    "max_weight_per_stock": float(params["max_weight_per_stock"]),
                    "max_sector_exposure": float(params["max_sector_exposure"]),
                    "max_top5_concentration": 1.0,
                    "weight_sum_tolerance": 1e-3,
                    "allow_cash": True,
                }
            )
            tov = TurnoverController(
                buffer_threshold=float(params["buffer_threshold"]),
                fee_rate=fee,
                min_hold_days=int(params["min_hold_days"]),
            )
            strategy = StrategyPipeline(
                signal_generator=SignalGenerator(),
                portfolio_optimizer=PortfolioOptimizer(),
                risk_manager=risk,
                turnover_controller=tov,
            )
            bt = Backtester(
                strategy_pipeline=strategy,
                fee_rate=fee,
                initial_capital=capital,
                factor_pipeline=factor_pipeline,
            )
            result = await bt.run(
                start_date=start,
                end_date=end,
                ohlcv_provider=ohlcv_provider,
                rebalance_freq=int(params["rebalance_freq"]),
                strategy_config=self._strategy_runtime_config(params),
                use_precomputed_factors=True,
                verbose=False,
            )
            m = result.metrics
            row = {
                **params,
                "weight_coop": 1.0
                - float(params["weight_sector"])
                - float(params["weight_dragon"]),
                "total_return": float(m.get("total_return", 0.0) or 0.0),
                "annualized_return": float(m.get("annualized_return", 0.0) or 0.0),
                "sharpe": float(m.get("sharpe", 0.0) or 0.0),
                "calmar": float(m.get("calmar", 0.0) or 0.0),
                "max_drawdown": float(m.get("max_drawdown", 0.0) or 0.0),
                "annualized_vol": float(m.get("annualized_vol", 0.0) or 0.0),
                "avg_turnover": float(m.get("avg_turnover", 0.0) or 0.0),
                "total_cost": float(m.get("total_cost", 0.0) or 0.0),
                "rebalance_count": int(m.get("rebalance_count", 0) or 0),
                "objective": self.objective(m),
                "status": "success",
            }
            return row
        except Exception as e:
            logger.exception("组合回测失败: %s", e)
            return {
                **params,
                "status": f"failed:{type(e).__name__}:{str(e)[:80]}",
                "objective": -1e9,
            }
        finally:
            try:
                sp_mod._STRATEGY_PARAMS_PATH = old_sp
                rm_mod._STRATEGY_PARAMS_PATH = old_rm
            except Exception:
                pass
            _clear_strategy_caches()
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    def sample_combinations(
        self,
        space: Dict[str, List[Any]],
        max_combinations: int,
        seed: int,
        include_baseline: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        随机采样参数组合。wide/full 空间笛卡尔积可达千万级，禁止 list(product(...))。
        """
        names = list(space.keys())
        rng = random.Random(seed)

        picked: List[Dict[str, Any]] = []
        seen = set()

        if include_baseline:
            base = self.baseline_params()
            # baseline 可能不在 enlarged space 的离散点上，仍强制纳入对照
            key = tuple(sorted(base.items()))
            seen.add(key)
            picked.append(base)

        # 估计空间大小，仅用于日志；不物化
        space_size = 1
        for k in names:
            space_size *= max(len(space[k]), 1)
        logger.info("参数空间理论组合数≈%s（随机采样，不枚举）", f"{space_size:,}")

        max_tries = max_combinations * 50
        tries = 0
        while len(picked) < max_combinations and tries < max_tries:
            tries += 1
            params = {k: rng.choice(space[k]) for k in names}
            ok, _ = self.validate_parameters(params)
            if not ok:
                continue
            key = tuple(sorted(params.items()))
            if key in seen:
                continue
            seen.add(key)
            picked.append(params)
        return picked

    async def run_grid_search(
        self,
        ohlcv_provider,
        start: str,
        end: str,
        capital: float,
        fee: float,
        max_combinations: int = 16,
        space_name: str = "core",
        seed: int = 42,
        workers: int = 1,
        ohlcv_data: Optional[dict] = None,
    ) -> pd.DataFrame:
        space = self.get_parameter_space(space_name)
        combos = self.sample_combinations(space, max_combinations, seed)
        logger.info(
            "网格搜索: %d 组 | 区间 %s→%s | space=%s | workers=%d",
            len(combos),
            start,
            end,
            space_name,
            workers,
        )

        jobs = [
            {
                "combination_id": i,
                "params": params,
                "start": start,
                "end": end,
                "capital": capital,
                "fee": fee,
            }
            for i, params in enumerate(combos, 1)
        ]

        if workers <= 1 or ohlcv_data is None:
            self.results = []
            for job in jobs:
                i, params = job["combination_id"], job["params"]
                logger.info("── 组合 %d/%d ── %s", i, len(jobs), params)
                row = await self.run_one(
                    params, ohlcv_provider, start, end, capital, fee
                )
                row["combination_id"] = i
                self.results.append(row)
                self._log_row(row)
        else:
            try:
                self.results = self._run_parallel(jobs, ohlcv_data, workers)
            except Exception as e:
                logger.warning(
                    "进程池失败 (%s: %s)，回退单进程顺序执行",
                    type(e).__name__,
                    e,
                )
                self.results = []
                for job in jobs:
                    i, params = job["combination_id"], job["params"]
                    logger.info("── 组合 %d/%d ── %s", i, len(jobs), params)
                    row = await self.run_one(
                        params, ohlcv_provider, start, end, capital, fee
                    )
                    row["combination_id"] = i
                    self.results.append(row)
                    self._log_row(row)

        df = pd.DataFrame(self.results)
        if not df.empty and "objective" in df.columns:
            df = df.sort_values("objective", ascending=False).reset_index(drop=True)
        return df

    @staticmethod
    def _log_row(row: Dict[str, Any]) -> None:
        if row.get("status") == "success":
            logger.info(
                "   obj=%.3f ret=%.2f%% mdd=%.2f%% sharpe=%.2f calmar=%.2f",
                row["objective"],
                100 * row["total_return"],
                100 * row["max_drawdown"],
                row["sharpe"],
                row["calmar"],
            )
        else:
            logger.warning("   %s", row.get("status"))

    def _run_parallel(
        self,
        jobs: List[Dict[str, Any]],
        ohlcv_data: dict,
        workers: int,
    ) -> List[Dict[str, Any]]:
        total = len(jobs)
        results: List[Optional[Dict[str, Any]]] = [None] * total
        cache_path = self._write_ohlcv_worker_cache(ohlcv_data)
        logger.info("进程池并发: %d workers (OHLCV 缓存 %s)", workers, cache_path.name)
        try:
            pool_kwargs: Dict[str, Any] = {
                "max_workers": workers,
                "initializer": _init_worker_pool,
                "initargs": (str(cache_path), self.scoring_method),
            }
            # Windows 必须 spawn；勿设 max_tasks_per_child=1，否则每组参数都重启进程并重复加载 OHLCV
            if sys.platform == "win32":
                import multiprocessing as mp

                pool_kwargs["mp_context"] = mp.get_context("spawn")
            with ProcessPoolExecutor(**pool_kwargs) as pool:
                future_map = {
                    pool.submit(_run_combination_worker, job): job for job in jobs
                }
                done_n = 0
                for fut in as_completed(future_map):
                    job = future_map[fut]
                    done_n += 1
                    try:
                        row = fut.result()
                    except Exception as e:
                        logger.exception("组合 %d 进程失败: %s", job["combination_id"], e)
                        row = {
                            **job["params"],
                            "combination_id": job["combination_id"],
                            "status": f"failed:{type(e).__name__}:{str(e)[:80]}",
                            "objective": -1e9,
                        }
                    results[job["combination_id"] - 1] = row
                    logger.info(
                        "── 完成 %d/%d | 组合 %d ── %s",
                        done_n,
                        total,
                        job["combination_id"],
                        job["params"],
                    )
                    self._log_row(row)
        finally:
            try:
                cache_path.unlink(missing_ok=True)
            except OSError:
                pass
        return [r for r in results if r is not None]

    @staticmethod
    def _write_ohlcv_worker_cache(ohlcv_data: dict) -> Path:
        """将 OHLCV 写入磁盘，子进程只接收路径（Windows spawn 不宜 pickle 整表）。"""
        cache_path = Path(tempfile.gettempdir()) / f"zstock_ohlcv_{os.getpid()}.pkl"
        with open(cache_path, "wb") as f:
            pickle.dump(ohlcv_data, f, protocol=pickle.HIGHEST_PROTOCOL)
        size_mb = cache_path.stat().st_size / (1024 * 1024)
        logger.info("OHLCV worker 缓存 %.1f MB → %s", size_mb, cache_path)
        return cache_path

    def save_results(self, df: pd.DataFrame) -> Path:
        csv_path = self.output_dir / "grid_search_results.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info("结果 CSV: %s", csv_path)

        success = df[df["status"] == "success"] if "status" in df.columns else df
        if not success.empty:
            best = success.iloc[0].to_dict()
            best_cfg = self._build_factor_config(best)
            best_path = self.output_dir / "best_strategy_params.json"
            with open(best_path, "w", encoding="utf-8") as f:
                json.dump(best_cfg, f, ensure_ascii=False, indent=2)
            logger.info("最优配置: %s", best_path)

        report = self.generate_report(df)
        report_path = self.output_dir / "report.txt"
        report_path.write_text(report, encoding="utf-8")
        print(report)
        return csv_path

    def generate_report(self, df: pd.DataFrame) -> str:
        lines = [
            "",
            "=" * 72,
            "参数网格搜索报告（预计算 + 含成本回测）",
            "=" * 72,
            f"组合数: {len(df)}",
        ]
        if df.empty:
            lines.append("无结果")
            return "\n".join(lines)

        n_ok = int((df["status"] == "success").sum()) if "status" in df.columns else 0
        lines.append(f"成功: {n_ok}  失败/无效: {len(df) - n_ok}")
        ok = df[df["status"] == "success"] if "status" in df.columns else df
        if ok.empty:
            lines.append("无成功回测")
            return "\n".join(lines)

        best = ok.iloc[0]
        lines += [
            "",
            "最优（按 objective）:",
            f"  objective     = {best['objective']:.4f}",
            f"  total_return  = {100 * best['total_return']:.2f}%",
            f"  ann_return    = {100 * best['annualized_return']:.2f}%",
            f"  max_drawdown  = {100 * best['max_drawdown']:.2f}%",
            f"  sharpe        = {best['sharpe']:.3f}",
            f"  calmar        = {best['calmar']:.3f}",
            f"  avg_turnover  = {100 * best['avg_turnover']:.2f}%",
            f"  total_cost    = {100 * best['total_cost']:.2f}%",
            "",
            "  参数:",
        ]
        param_cols = [
            c
            for c in self.baseline_params().keys()
            if c in best.index
        ]
        for c in param_cols:
            lines.append(f"    {c}: {best[c]}")
        lines.append(f"    weight_coop: {best.get('weight_coop', float('nan'))}")

        lines += ["", "Top 5:"]
        show_cols = [
            c
            for c in [
                "objective",
                "total_return",
                "max_drawdown",
                "sharpe",
                "calmar",
                "rebalance_freq",
                "top_k",
                "hard_stop_loss_pct",
                "max_weight_per_stock",
            ]
            if c in ok.columns
        ]
        for i, (_, r) in enumerate(ok.head(5).iterrows(), 1):
            bits = [f"{c}={r[c]:.4f}" if isinstance(r[c], float) else f"{c}={r[c]}" for c in show_cols]
            lines.append(f"  {i}. " + " | ".join(bits))

        # baseline 对照
        base = self.baseline_params()
        mask = pd.Series(True, index=ok.index)
        for k, v in base.items():
            if k in ok.columns:
                mask &= ok[k] == v
        if mask.any():
            b = ok.loc[mask].iloc[0]
            lines += [
                "",
                "当前基线对照:",
                f"  objective={b['objective']:.4f} ret={100*b['total_return']:.2f}% "
                f"mdd={100*b['max_drawdown']:.2f}% sharpe={b['sharpe']:.3f}",
            ]
        lines += ["", "=" * 72, ""]
        return "\n".join(lines)


def _init_worker_pool(ohlcv_cache_path: str, scoring_method: str) -> None:
    global _POOL_OHLCV, _POOL_SCORING_METHOD

    # 子进程独立连接池：限制 min/max，避免 N×workers 打满 MongoDB
    os.environ.setdefault("MONGO_MIN_CONNECTIONS", "2")
    os.environ.setdefault("MONGO_MAX_CONNECTIONS", "8")
    with open(ohlcv_cache_path, "rb") as f:
        _POOL_OHLCV = pickle.load(f)
    _POOL_SCORING_METHOD = scoring_method
    _quiet_loggers()


def _reset_worker_singletons() -> None:
    """子进程内重置 DB / 查询服务单例，避免 Motor 绑定已关闭 event loop。"""
    import app.core.database as db_module
    from app.core.database import db_manager

    db_manager.mongo_client = None
    db_manager.mongo_db = None
    db_module.mongo_client = None
    db_module.mongo_db = None

    import zstock.data_management.query_service as qs_mod

    qs_mod._data_query_service = None


def _run_combination_worker(job: Dict[str, Any]) -> Dict[str, Any]:
    """ProcessPool 子进程入口：独立全局状态，避免配置路径竞态。"""
    if _POOL_OHLCV is None:
        raise RuntimeError("worker pool 未初始化 OHLCV")

    async def _inner() -> Dict[str, Any]:
        from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
        from zstock.strategy_management.script.backtester import (
            make_ohlcv_provider_from_dict,
        )

        await init_zstock_database()
        try:
            provider = make_ohlcv_provider_from_dict(_POOL_OHLCV)
            opt = RealGridSearchOptimizer(scoring_method=_POOL_SCORING_METHOD)
            row = await opt.run_one(
                job["params"],
                provider,
                job["start"],
                job["end"],
                job["capital"],
                job["fee"],
            )
            row["combination_id"] = job["combination_id"]
            return row
        finally:
            await close_zstock_database()
            _reset_worker_singletons()

    return asyncio.run(_inner())


async def load_ohlcv(start: str, end: str) -> dict:
    from zstock.common.utils.common_utils import normalize_date
    from zstock.data_management.query_service import get_data_query_service

    qs = get_data_query_service()
    all_stocks_docs, _ = await qs.get_all_stocks()
    mainboard_codes = [
        d["code"]
        for d in all_stocks_docs
        if d.get("is_mainboard") and not d.get("is_st")
    ]
    logger.info("主板非ST: %d，开始加载 OHLCV...", len(mainboard_codes))
    ohlcv_data: dict = {}
    chunk_size = 500
    ohlcv_batch_size = 80
    ohlcv_query_concurrency = 6
    for i in range(0, len(mainboard_codes), chunk_size):
        chunk = mainboard_codes[i : i + chunk_size]
        try:
            batch = await qs.get_ohlcv_batch(
                chunk,
                start,
                end,
                batch_size=ohlcv_batch_size,
                query_concurrency=ohlcv_query_concurrency,
            )
            if batch:
                ohlcv_data.update(batch)
        except Exception as e:
            logger.warning("OHLCV 批次失败: %s", e)
    for code, df in ohlcv_data.items():
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].apply(normalize_date)
    logger.info("OHLCV 就绪: %d 只", len(ohlcv_data))
    return ohlcv_data


async def async_main(args: argparse.Namespace) -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.strategy_management.script.backtester import (
        make_ohlcv_provider_from_dict,
    )

    try:
        await init_zstock_database()
    except Exception as e:
        logger.error(f"MongoDB 初始化失败: {e}")
        raise
    try:
        ohlcv = await load_ohlcv(args.start, args.end)
        if not ohlcv:
            logger.error("无 OHLCV，退出")
            return 1
        provider = make_ohlcv_provider_from_dict(ohlcv)
        budget = compute_resource_budget()
        workers = cap_worker_count(args.workers, budget.compute_workers, name="workers")
        logger.info(
            "资源预算: CPU=%d 内存=%.1fGB → workers=%d",
            budget.cpu_cores,
            budget.total_memory_gb,
            workers,
        )
        opt = RealGridSearchOptimizer(
            output_dir=Path(args.output) if args.output else None,
            scoring_method=args.scoring_method,
        )
        df = await opt.run_grid_search(
            ohlcv_provider=provider,
            start=args.start,
            end=args.end,
            capital=args.capital,
            fee=args.fee,
            max_combinations=args.max_combinations,
            space_name=args.space,
            seed=args.seed,
            workers=workers,
            ohlcv_data=ohlcv,
        )
        csv_path = opt.save_results(df)
        if args.apply:
            success = df[df["status"] == "success"] if "status" in df.columns else df
            if success.empty:
                logger.error("--apply 失败：无成功组合")
                return 1
            best_cfg = opt._build_factor_config(success.iloc[0].to_dict())
            best_cfg["description"] = (
                best_cfg.get("description", "")
                + f" | grid@{args.start}~{args.end} space={args.space}"
            )
            with open(_BASE_PARAMS_PATH, "w", encoding="utf-8") as f:
                json.dump(best_cfg, f, ensure_ascii=False, indent=2)
            logger.info("已写入正式配置: %s", _BASE_PARAMS_PATH)
        logger.info("结果目录: %s", csv_path.parent)
        return 0
    finally:
        await close_zstock_database()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="zstock 真实网格搜索（预计算回测）")
    p.add_argument("--start", default="2026-01-05", help="调参区间起点（建议 2026）")
    p.add_argument("--end", default="2026-07-27", help="调参区间终点（建议 2026）")
    p.add_argument("--capital", type=float, default=1e6)
    p.add_argument("--fee", type=float, default=0.0015)
    p.add_argument("--max-combinations", type=int, default=40)
    p.add_argument(
        "--space",
        choices=["core", "wide", "full"],
        default="wide",
        help="参数空间：wide=加大范围（默认）",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="进程池并发数（多组参数并行回测），默认 4；配合因子预加载缓存控制 Mongo 压力",
    )
    p.add_argument("--output", default=None, help="结果目录")
    p.add_argument(
        "--scoring-method",
        choices=["linear", "tree"],
        default="linear",
        help="龙头层打分方式：linear=线性加权，tree=决策树(非线性)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="将最优参数写入 zstock/common/config/strategy_params.json",
    )
    return p


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _quiet_loggers()
    logging.getLogger(__name__).setLevel(logging.INFO)
    args = build_parser().parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
