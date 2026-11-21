"""因子测评 / 报告：等级与窗口用真实 summary.csv。"""

from pathlib import Path

import pandas as pd
import pytest

from zstock.factor_management.script.factor_evaluation import FactorEvaluation


def test_grade_letter_matches_eval_csv(factor_eval_2024_csv):
    df = pd.read_csv(factor_eval_2024_csv)
    for g in df["Grade"].head(20):
        letter = FactorEvaluation.grade_letter(g)
        assert letter in {"A", "B", "C", "D", "N/A"} or letter.startswith("A") or len(letter) <= 3
    assert FactorEvaluation.grade_letter(None) == "N/A"
    assert FactorEvaluation.grade_letter("A - 优秀因子") == "A"


def test_year_eval_window_and_grade_helpers():
    start, end = FactorEvaluation.year_eval_window(2024, today="2026-08-25")
    assert start == "2024-01-01"
    assert end == "2024-12-31"
    start25, _ = FactorEvaluation.year_eval_window(2025, today="2026-08-25")
    assert start25 == "2025-01-02"
    assert FactorEvaluation._get_grade(85).startswith("A")
    assert FactorEvaluation._get_grade(65).startswith("B")
    assert FactorEvaluation._get_grade(45).startswith("C")
    assert FactorEvaluation._get_grade(10).startswith("D")
    eq = pd.Series([1.0, 1.1, 1.05])
    mdd = FactorEvaluation._max_drawdown(eq)
    assert mdd <= 0.0


def test_ic_series_on_tiny_real_panel(tmp_path, stock_ohlcv_by_code):
    pipe = FactorEvaluation(
        start_date="2024-01-02",
        end_date="2024-01-16",
        period=5,
        plot=False,
        output_dir=str(tmp_path),
    )
    codes = [c for c in ("603201", "601107", "000060") if c in stock_ohlcv_by_code]
    frames = []
    for code in codes:
        df = stock_ohlcv_by_code[code].copy()
        df = df[(df["trade_date"] >= "2024-01-02") & (df["trade_date"] <= "2024-01-16")]
        s = df.set_index("trade_date")["close"]
        s.name = code
        frames.append(s)
    close = pd.concat(frames, axis=1).sort_index()
    open_ = close.copy()
    factor = close.copy()
    ic = pipe._calc_ic_series(factor, {"open": open_, "close": close}, period=1)
    assert ic is None or isinstance(ic, pd.DataFrame)


@pytest.mark.asyncio
async def test_load_factor_and_price_panels(factor_raw, stock_ohlcv_by_code, tmp_path):
    from zstock.data_management.query_service import COL_FACTOR_DRAGON
    from test.data_management.test.conftest import FakeDatabaseService
    from zstock.factor_management.script.factor_evaluation import (
        _invert_fields_from_registry,
        build_parser,
    )

    pipe = FactorEvaluation(
        start_date="2024-01-02",
        end_date="2024-01-16",
        period=5,
        plot=False,
        output_dir=str(tmp_path),
    )
    dragons = list(factor_raw.get("dragons") or [])[:20]
    pipe.db = FakeDatabaseService({COL_FACTOR_DRAGON: dragons})
    panel = await pipe.load_stock_factor_panel(COL_FACTOR_DRAGON, "f32_amount")
    assert not panel.empty
    sector_panel = await pipe.load_sector_factor_panel("f21_rps_10d")
    assert isinstance(sector_panel, pd.DataFrame)

    class QS:
        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            return {c: stock_ohlcv_by_code[c] for c in codes if c in stock_ohlcv_by_code}

    pipe.qs = QS()
    prices = await pipe.load_price_panel(["603201", "000060"], extra_days=5)
    assert "close" in prices and "open" in prices

    assert isinstance(_invert_fields_from_registry(), set)
    args = build_parser().parse_args(["--start", "2024-01-01", "--end", "2024-03-15"])
    assert args.start == "2024-01-01"
    assert not hasattr(args, "top_sectors") or getattr(args, "top_sectors", None) is None


def test_generate_report_merge_real_summary(factor_eval_2024_csv, tmp_path):
    df = pd.read_csv(factor_eval_2024_csv)
    df["factor_key"] = df["layer"] + "." + df["field"]
    merged = FactorEvaluation.build_merged({"2024": df, "2025": df, "2026": df})
    assert not merged.empty
    assert FactorEvaluation.fmt_cell(None, "N/A") == "N/A"
    assert "A" in FactorEvaluation.fmt_cell(80, "A")
    assert FactorEvaluation.fmt_ric(None) == "-"
    assert FactorEvaluation.fmt_ric(0.12).startswith("0.")
    lines = []
    FactorEvaluation.append_compare_sections(lines, None, {}, pd.DataFrame())
    assert any("未找到" in x for x in lines)
    assert FactorEvaluation.load_compare_summaries(tmp_path) == {}
    mode_dir = tmp_path / "cond_p5" / "2024"
    mode_dir.mkdir(parents=True)
    dest = mode_dir / "summary.csv"
    dest.write_bytes(Path(factor_eval_2024_csv).read_bytes())
    done = {"cond_p5/2024": dest}
    detail = FactorEvaluation.build_compare_detail(done)
    assert isinstance(detail, pd.DataFrame)
    report = FactorEvaluation.write_compare_report(tmp_path, done)
    assert Path(report).is_file()
    start, end = FactorEvaluation.year_eval_window(2024)
    assert start == "2024-01-01"
