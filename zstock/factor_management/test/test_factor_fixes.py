"""针对因子模块审查修复的单元测试（不依赖 MongoDB / QMT）。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.common.utils.common_utils import (
    is_main_board,
    limit_up_threshold,
    ohlcv_asof,
)
from zstock.factor_management.dragon_factors import DragonFactors
from zstock.factor_management.force_factors import ForceFactors
from zstock.factor_management.market_factors import MarketFactors
from zstock.factor_management.prefilters import PreFilters
from zstock.factor_management.sector_factors import SectorFactors


def test_limit_up_threshold_by_board():
    assert abs(limit_up_threshold("600000") - 0.095) < 1e-9
    assert abs(limit_up_threshold("000001") - 0.095) < 1e-9
    assert abs(limit_up_threshold("300750") - 0.195) < 1e-9
    assert abs(limit_up_threshold("688001") - 0.195) < 1e-9
    assert abs(limit_up_threshold("830799") - 0.295) < 1e-9


def test_is_main_board_includes_602():
    assert is_main_board("602000") is True
    assert is_main_board("600000") is True
    assert is_main_board("300750") is False


def test_fcoop2_retail_divergence_uses_main_in_numerator():
    ratio = ForceFactors._main_retail_ratio(100.0, -50.0)
    assert abs(ratio - 100.0 / 150.0) < 1e-9
    ratio2 = ForceFactors._main_retail_ratio(100.0, 50.0)
    assert abs(ratio2 - 100.0 / 150.0) < 1e-9


def test_fcoop2_through_raw_path():
    candidates = [{"code": "600000", "sector_code": "BANK", "dragon_score": 80.0}]
    flow = {
        "600000": [
            {"main_net": 10.0, "m_net": -3.0, "s_net": -2.0, "turnover": 1_000_000.0},
        ]
        * 5  # 凑满 5 日，避免 sustained_days 因不足窗口返回 nan
    }
    # 前 4 日改为流出，最后一日流入 → sustained_days_5d = 1
    flow["600000"] = [
        {"main_net": -1.0, "m_net": 0.0, "s_net": 0.0, "turnover": 1_000_000.0},
        {"main_net": -1.0, "m_net": 0.0, "s_net": 0.0, "turnover": 1_000_000.0},
        {"main_net": -1.0, "m_net": 0.0, "s_net": 0.0, "turnover": 1_000_000.0},
        {"main_net": -1.0, "m_net": 0.0, "s_net": 0.0, "turnover": 1_000_000.0},
        {"main_net": 10.0, "m_net": -3.0, "s_net": -2.0, "turnover": 1_000_000.0},
    ]
    rows = ForceFactors.apply_cooperative_force_raw(candidates, stock_flow_recent=flow)
    assert len(rows) == 1
    assert abs(rows[0]["fcoop2_main_retail_ratio"] - 2.0 / 3.0) < 1e-6
    # F_coop3 必须是天数（1），不是占比（0.2）
    assert abs(rows[0]["fcoop3_sustained_days"] - 1.0) < 1e-9
    assert abs(rows[0]["fcoop3_sustained_days_5d"] - 1.0) < 1e-9
    assert rows[0]["fcoop3_sustained_days_10d"] != rows[0]["fcoop3_sustained_days_10d"]  # nan


def test_fcoop3_insufficient_window_is_nan():
    docs = [{"main_net": 1.0} for _ in range(3)]
    assert ForceFactors._sustained_days(docs, 5) != ForceFactors._sustained_days(docs, 5)  # nan
    docs5 = [{"main_net": 1.0}, {"main_net": -1.0}, {"main_net": 1.0},
             {"main_net": 1.0}, {"main_net": 1.0}]
    assert ForceFactors._sustained_days(docs5, 5) == 4.0


def test_dragon_f35_no_double_minmax():
    # 仅 f35 不同时，差分应为 0.12*(90-60)=3.6（绝对分，禁止二次 min-max）
    raw2 = {
        "A": {
            "f31_excess_return": 0.0,
            "f32_amount": 1.0,
            "f33_consecutive_boards": 0,
            "f34_resonance_pct": 0.5,
            "f35_bollinger_trend": 90.0,
        },
        "B": {
            "f31_excess_return": 0.0,
            "f32_amount": 1.0,
            "f33_consecutive_boards": 0,
            "f34_resonance_pct": 0.5,
            "f35_bollinger_trend": 60.0,
        },
    }
    s2 = DragonFactors.scores_from_raw(raw2)
    assert abs((s2["A"] - s2["B"]) - 0.12 * 30.0) < 1e-6


def test_sector_limit_up_density_uses_all_members():
    sectors = [{"sector_code": "S1"}]
    sector_stocks = {"S1": ["A", "B", "C"]}
    # 仅 A 有涨停判定；B/C 无数据应视为非涨停 → 密度 1/3
    density = SectorFactors._collect_limit_up_densities(
        sectors, sector_stocks, {"A": True}
    )
    assert abs(density["S1"] - 1.0 / 3.0) < 1e-9


def test_sector_limit_up_density_eligible_universe():
    sectors = [{"sector_code": "S1"}]
    sector_stocks = {"S1": ["600001", "300001", "600002"]}
    density = SectorFactors._collect_limit_up_densities(
        sectors,
        sector_stocks,
        {"600001": True, "300001": True, "600002": False},
        eligible_codes={"600001", "600002"},
    )
    # 创业板涨停不计入；主板 1/2
    assert abs(density["S1"] - 0.5) < 1e-9


def test_ohlcv_asof_require_exact():
    dates = pd.date_range("2025-01-01", periods=5, freq="B")
    df = pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y-%m-%d"),
            "close": np.arange(5, dtype=float) + 10,
        }
    )
    td = dates[-1].strftime("%Y-%m-%d")
    assert ohlcv_asof(df, td, require_exact=True) is not None
    assert ohlcv_asof(df, "2025-01-10", require_exact=True) is None
    # 截到中间日
    mid = dates[2].strftime("%Y-%m-%d")
    asof = ohlcv_asof(df, mid, require_exact=True)
    assert asof is not None and len(asof) == 3


def test_dragon_scores_drop_missing_critical():
    raw = {
        "OK": {
            "f31_excess_return": 0.01,
            "f32_amount": 1e8,
            "f33_consecutive_boards": 1,
            "f34_resonance_pct": 0.6,
            "f35_bollinger_trend": 70.0,
        },
        "NO_AMT": {
            "f31_excess_return": 0.01,
            "f32_amount": float("nan"),
            "f33_consecutive_boards": 2,
            "f34_resonance_pct": 0.6,
            "f35_bollinger_trend": 70.0,
        },
        "ZERO_AMT": {
            "f31_excess_return": 0.01,
            "f32_amount": 0.0,
            "f33_consecutive_boards": 2,
            "f34_resonance_pct": 0.6,
            "f35_bollinger_trend": 70.0,
        },
    }
    scores = DragonFactors.scores_from_raw(raw)
    assert "OK" in scores
    assert "NO_AMT" not in scores
    assert "ZERO_AMT" not in scores


def test_f32_amount_inverted_in_score():
    """高成交额应得到更低龙头分（反拥挤）。"""
    raw = {
        "LOW": {
            "f31_excess_return": 0.0,
            "f32_amount": 1e7,
            "f33_consecutive_boards": 0,
            "f34_resonance_pct": 0.5,
            "f35_bollinger_trend": 50.0,
        },
        "HIGH": {
            "f31_excess_return": 0.0,
            "f32_amount": 1e9,
            "f33_consecutive_boards": 0,
            "f34_resonance_pct": 0.5,
            "f35_bollinger_trend": 50.0,
        },
    }
    scores = DragonFactors.scores_from_raw(raw)
    assert scores["LOW"] > scores["HIGH"]


def test_f25_uses_amount_and_recent_slope():
    # 两板块：A 近期成交额占比抬升，B 持平
    dates = pd.date_range("2025-01-01", periods=20, freq="B")
    n = len(dates)
    amt_a = np.concatenate([np.full(n - 5, 1e8), np.linspace(1e8, 3e8, 5)])
    amt_b = np.full(n, 1e8)
    sector_ohlcv = {
        "A": pd.DataFrame(
            {
                "trade_date": dates.strftime("%Y-%m-%d"),
                "amount": amt_a,
                "volume": np.full(n, 1e6),
                "close": np.full(n, 100.0),
            }
        ),
        "B": pd.DataFrame(
            {
                "trade_date": dates.strftime("%Y-%m-%d"),
                "amount": amt_b,
                "volume": np.full(n, 1e6),
                "close": np.full(n, 100.0),
            }
        ),
    }
    slopes = SectorFactors._collect_volume_ratio_slopes_multi(
        sector_ohlcv, windows=(5,), sector_codes={"A", "B"}
    )[5]
    assert slopes["A"] > slopes["B"]


async def _test_st_uses_is_st_field():
    pf = PreFilters.__new__(PreFilters)
    pf.sector_blacklist = set()
    pf.stock_blacklist = {}
    stocks = ["600001", "600002"]
    infos = {
        "600001": {"name": "正常股", "is_st": False},
        "600002": {"name": "", "is_st": True},  # 名称空但 is_st=True 应剔除
    }
    out = await pf.apply_main_board_filter(stocks, infos)
    assert out == ["600001"]


def test_st_uses_is_st_field():
    import asyncio

    asyncio.run(_test_st_uses_is_st_field())


def test_sector_minmax_skips_nan():
    norm = SectorFactors._minmax_normalize({"A": 1.0, "B": float("nan"), "C": 3.0})
    assert "B" not in norm
    assert abs(norm["A"] - 0.0) < 1e-9
    assert abs(norm["C"] - 100.0) < 1e-9


def test_market_atr_inv_is_reciprocal():
    dates = pd.date_range("2025-01-01", periods=40, freq="B")
    close = np.linspace(100, 110, len(dates))
    df = pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y-%m-%d"),
            "close": close,
            "high": close + 1,
            "low": close - 1,
            "volume": np.full(len(dates), 1e9),
        }
    )
    df = df.sample(frac=1.0, random_state=0).reset_index(drop=True)
    out = MarketFactors.calculate_market_sentiment(df, index_name="test")
    atr = out["detail"]["mf5_atr_ratio"]
    inv = out["detail"]["mf5_atr_ratio_inv"]
    assert atr == atr and atr > 0
    assert abs(inv - 1.0 / atr) < 1e-9


def test_pipeline_config_helpers():
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

    p = CrossSectionStrategyPipeline()
    assert p._cfg_top_sectors() == 3
    assert p._cfg_top_per_sector() == 3
    assert p._cfg_top_k() == 5
    w = p._cfg_final_weights()
    assert abs(w["sector"] - 0.4) < 1e-9
    assert abs(w["dragon"] - 0.35) < 1e-9
    assert abs(w["cooperative"] - 0.25) < 1e-9


def test_bollinger_pass_hard_filter():
    dates = pd.date_range("2025-01-01", periods=40, freq="B")
    # 上升趋势：应通过
    up = np.linspace(100, 130, len(dates))
    df_up = pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y-%m-%d"),
            "close": up,
            "high": up + 1,
            "low": up - 1,
            "volume": np.full(len(dates), 1e6),
            "amount": np.full(len(dates), 1e8),
        }
    )
    # 下降趋势：不应通过
    down = np.linspace(130, 100, len(dates))
    df_down = pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y-%m-%d"),
            "close": down,
            "high": down + 1,
            "low": down - 1,
            "volume": np.full(len(dates), 1e6),
            "amount": np.full(len(dates), 1e8),
        }
    )
    passed = DragonFactors._compute_bollinger_pass(
        {"600001": df_up, "600002": df_down}, ["600001", "600002"]
    )
    assert passed["600001"] == 1.0
    assert passed["600002"] == 0.0


def main():
    tests = [
        ("limit_up_threshold", test_limit_up_threshold_by_board),
        ("is_main_board_602", test_is_main_board_includes_602),
        ("fcoop2 formula", test_fcoop2_retail_divergence_uses_main_in_numerator),
        ("fcoop2+fcoop3 raw", test_fcoop2_through_raw_path),
        ("fcoop3 days/nan", test_fcoop3_insufficient_window_is_nan),
        ("dragon f35 absolute score", test_dragon_f35_no_double_minmax),
        ("sector density denominator", test_sector_limit_up_density_uses_all_members),
        ("sector density eligible", test_sector_limit_up_density_eligible_universe),
        ("ohlcv asof", test_ohlcv_asof_require_exact),
        ("dragon drop missing", test_dragon_scores_drop_missing_critical),
        ("f32 amount inverted", test_f32_amount_inverted_in_score),
        ("f25 amount recent slope", test_f25_uses_amount_and_recent_slope),
        ("st uses is_st field", test_st_uses_is_st_field),
        ("sector minmax nan", test_sector_minmax_skips_nan),
        ("market atr_inv + sort", test_market_atr_inv_is_reciprocal),
        ("pipeline config helpers", test_pipeline_config_helpers),
        ("bollinger pass field", test_bollinger_pass_hard_filter),
    ]
    for name, fn in tests:
        fn()
        print(f"[ok] {name}")
    print("\nALL FIX TESTS PASSED")
    return True


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ok = main()
    sys.exit(0 if ok else 1)
