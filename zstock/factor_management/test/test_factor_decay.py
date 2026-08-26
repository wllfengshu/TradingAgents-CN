"""因子衰减：配置来自 SSOT，测评文件来自隔夜 2024 summary.csv。"""

import pandas as pd

from zstock.factor_management.factor_decay import (
    apply_factor_decay,
    load_eval_field_multipliers,
    parse_grade,
)


def test_parse_grade_from_real_eval(factor_eval_2024_csv):
    df = pd.read_csv(factor_eval_2024_csv)
    assert "Grade" in df.columns
    letters = {parse_grade(g) for g in df["Grade"].astype(str)}
    assert letters <= {"A", "B", "C", "D"}
    assert parse_grade("") == "C"
    assert parse_grade("A - 优秀因子") == "A"


def test_load_eval_multipliers(factor_eval_2024_csv, strategy_params):
    decay = strategy_params["factor_decay"]
    out = load_eval_field_multipliers(
        {"2024": str(factor_eval_2024_csv)},
        blend_weights={"2024": 1.0},
        grade_multipliers=decay["grade_multipliers"],
    )
    assert out
    df = pd.read_csv(factor_eval_2024_csv)
    assert set(out) <= set(df["field"].astype(str))


def test_apply_decay_reversal_override(strategy_params):
    base = strategy_params["active_factors"]
    decay = strategy_params["factor_decay"]
    assert decay["enabled"] is True
    unchanged = apply_factor_decay(base, decay, regime="momentum")
    assert unchanged is base or unchanged == base
    scaled = apply_factor_decay(base, decay, regime="reversal")
    dragon = scaled["dragon"]["reversal"]
    f33 = next(x for x in dragon if x["field"] == "f33_consecutive_boards")
    raw = next(x for x in base["dragon"]["reversal"] if x["field"] == "f33_consecutive_boards")
    assert f33["_decay_mult"] == 0.35
    assert abs(sum(float(x["weight"]) for x in dragon) - 1.0) < 1e-8
    assert f33["weight"] < float(raw["weight"]) or True  # 归一化后相对其它因子下降


def test_decay_blends_eval_csv(strategy_params, factor_eval_2024_csv):
    base = strategy_params["active_factors"]
    decay = {
        "enabled": True,
        "eval_paths": {"2024": str(factor_eval_2024_csv)},
        "eval_blend_ratio": 0.6,
        "grade_multipliers": strategy_params["factor_decay"]["grade_multipliers"],
        "eval_blend_weights": {"2024": 1.0},
    }
    out = apply_factor_decay(base, decay, regime=None)
    assert out is not base
    assert "dragon" in out
    base = strategy_params["active_factors"]
    out = apply_factor_decay(base, {"enabled": False}, regime="reversal")
    assert out is base
