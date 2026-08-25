"""对比 grid26 应用前后三年回测（快速脚本）。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def main() -> None:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.strategy_management.script.backtester import Backtester

    segments = [
        ("2024", "2024-01-01", "2024-12-31"),
        ("2025", "2025-01-02", "2025-12-31"),
        ("2026_YTD", "2026-01-01", "2026-08-24"),
    ]
    rows = []
    await init_zstock_database()
    try:
        bt = Backtester()
        for label, s, e in segments:
            print(f"回测 {label} {s} ~ {e} ...", flush=True)
            res = await bt.run_real_data(
                start_date=s,
                end_date=e,
                use_precomputed_factors=True,
                rebalance_freq=None,
            )
            m = res.metrics
            rows.append({
                "label": label,
                "start": s,
                "end": e,
                "total_return": round(float(m.get("total_return", 0)) * 100, 2),
                "annualized_return": round(float(m.get("annualized_return", 0)) * 100, 2),
                "sharpe": round(float(m.get("sharpe", 0)), 3),
                "max_drawdown": round(float(m.get("max_drawdown", 0)) * 100, 2),
            })
    finally:
        await close_zstock_database()

    print("\n=== grid26 已应用 (strategy_params.json) ===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
