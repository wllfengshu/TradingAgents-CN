"""隔夜校验脚本的纯日期/计数辅助。"""

import json
from pathlib import Path

from zstock.factor_management.script.overnight_validation import (
    _StepCounter,
    _backtest_jobs,
    _count_steps,
    _default_periods,
    _eval_windows,
    _load_active_factor_summary,
    _max_date,
    _min_date,
    _periods_for_year,
    _today_str,
    _top_factors_from_summary,
    build_parser,
    generate_summary,
)


def test_date_helpers_and_year_scope():
    assert _min_date("2024-01-02", "2024-06-03") == "2024-01-02"
    assert _max_date("2024-01-02", "2024-06-03") == "2024-06-03"
    p = _periods_for_year(2024)
    assert p["precompute_start"] == "2024-01-01"
    assert p["year_scope"] == 2024
    d = _default_periods("2026-08-25")
    assert d["full_2024_start"] == "2024-01-01"
    assert d["precompute_start"] == "2024-01-01"
    assert _today_str()
    d2 = _default_periods("2026-01-01")
    assert d2["is_end"] >= d2["is_start"]


def test_step_counter_and_active_summary():
    c = _StepCounter(3)
    c.next("precompute")
    assert c.current == 1
    lines = _load_active_factor_summary()
    assert lines


def test_windows_parser_and_summary(overnight_dir, factor_eval_2024_csv):
    year_p = _periods_for_year(2024)
    assert _eval_windows(year_p)[0][0] == "2024"
    jobs = _backtest_jobs(year_p)
    assert any(j[0] == "oos_q4" for j in jobs)
    full_p = _default_periods("2026-08-25")
    assert _eval_windows(full_p)
    assert _backtest_jobs(full_p)
    parser = build_parser()
    args = parser.parse_args(["--skip-precompute", "--skip-eval", "--skip-grid", "--year", "2024"])
    assert args.skip_grid
    assert _count_steps(args) == 1
    args2 = parser.parse_args(["--end-date", "2026-08-25"])
    assert _count_steps(args2) >= 4
    top = _top_factors_from_summary(factor_eval_2024_csv, top_n=5)
    assert top
    empty = _top_factors_from_summary(Path("missing.csv"))
    assert empty and "失败" in empty[0]

    path = overnight_dir / "overnight_backtest_results.json"
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    metrics = payload.get("current_2024") or {}
    success = {"status": "success", **metrics}
    text = generate_summary(
        {
            "is_main": success,
            "oos_q4": success,
            "full_year": {"status": "skipped"},
            "eval_summaries": {"2024": str(factor_eval_2024_csv)},
            "precompute_stats": {"market": 1, "sector": 10, "dragon": 20, "force": 20},
            "best_params": {"top_k": 5},
        },
        year_p,
    )
    assert "隔夜全面验证报告" in text
    assert "过拟合" in text
    text2 = generate_summary(
        {
            "is_main": success,
            "oos_2025": success,
            "oos_forward_2026": success,
        },
        full_p,
    )
    assert "2025 OOS" in text2
