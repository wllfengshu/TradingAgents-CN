"""单配置 2020–2026 逐年回测。用法: python -m ... run_single_config_history v120e"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (  # noqa: E402
    _latest_trade_date_in_factors,
    _run_one,
    _setup_logging,
    _write_report,
    _year_ranges,
)

CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"


async def async_main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "prod"
    _files = {
        "prod": "strategy_params.json",
        "v120e": "strategy_params_v120e_no_target_equity.json",
        "v120j": "strategy_params_v120j_v119_ov_te_mom.json",
        "v121b": "strategy_params_v121b_m4_green_off.json",
        "v122": "strategy_params_v122_manual_3stock.json",
        "v122b": "strategy_params_v122b_hybrid.json",
        "v123_exposure": "strategy_params_v123_exposure.json",
        "v123_naked": "strategy_params_v123_naked_select.json",
        "v123_alpha": "strategy_params_v123_alpha_trim.json",
        "v124": "strategy_params_v124_candidate.json",
    }
    path = CONFIG / _files.get(name, f"strategy_params_{name}.json")
    if not path.is_file():
        print("missing", path)
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent / "output" / f"history_{name}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    end_2026 = await _latest_trade_date_in_factors()
    rows = []
    for i, (year, start, end) in enumerate(_year_ranges(end_2026), 1):
        seg = out_dir / f"{name}_{year}"
        seg.mkdir(parents=True, exist_ok=True)
        print(f"[{i}/7] {name} {year}")
        rows.append(await _run_one(name, start, end, path, seg))
        _write_report(out_dir, rows, end_2026)
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
