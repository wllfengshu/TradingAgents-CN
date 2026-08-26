"""龙头 / 板块 / 合力：用 2024-01-02 Mongo 导出原始值 + SSOT 权重。"""

from zstock.factor_management.dragon_factors import DragonFactors
from zstock.factor_management.force_factors import ForceFactors
from zstock.factor_management.sector_factors import SectorFactors
from zstock.factor_management.test.conftest import docs_to_field_map


def test_sector_scores_from_dumped_raw(factor_raw, strategy_params):
    docs = factor_raw["sectors"]
    raw = docs_to_field_map(docs, "sector_code")
    scores = SectorFactors.scores_from_raw(
        raw,
        regime="momentum",
        active_factors=strategy_params["active_factors"],
        top_n=int(strategy_params["sector_layer"]["top_sectors"]),
    )
    assert scores
    assert len(scores) <= int(strategy_params["sector_layer"]["top_sectors"])
    assert all(v >= 0 for v in scores.values())


def test_dragon_scores_from_signal_names(signal_names_raw, strategy_params):
    raw = {d["code"]: d for d in signal_names_raw["dragons"]}
    scores = DragonFactors.scores_from_raw(
        raw,
        regime="momentum",
        active_factors=strategy_params["active_factors"],
        scoring_method="linear",
    )
    assert set(scores) <= set(raw)
    assert scores
    empty = DragonFactors.scores_from_raw({}, regime="momentum", active_factors=strategy_params["active_factors"])
    assert empty == {}
    no_cfg = DragonFactors.scores_from_raw(raw, regime="momentum", active_factors=None)
    assert no_cfg == {}


def test_m4_gate_on_real_force_docs(signal_names_raw, strategy_params):
    thr = float(strategy_params["cooperative_force"]["threshold_pct"])
    docs = signal_names_raw["forces"]
    results = {d["code"]: ForceFactors.passes_precomputed_m4_gate(d, thr) for d in docs}
    # 这四只是隔夜截面真实入选/候选，fcoop1 均 ≥ 门槛
    assert any(results.values())
    assert ForceFactors.passes_precomputed_m4_gate({"fcoop1_main_net_ratio": "x"}, thr) is False
    assert ForceFactors.passes_precomputed_m4_gate(
        {"fcoop1_main_net_ratio": 0.0, "fcoop3_sustained_days": 5}, thr
    ) is False


def test_force_helpers_with_real_codes(signal_names_raw, strategy_params):
    ranks = ForceFactors._normalize_sector_ranks(
        [(d["sector_code"], 80.0) for d in signal_names_raw["dragons"]]
    )
    assert ranks
    bonus = ForceFactors._calculate_lhb_bonus("603201", {})
    assert bonus == 0.0
    filtered = []
    for d in signal_names_raw["forces"]:
        row = dict(d)
        row["dragon_composite_score"] = 70.0
        filtered.append(row)
    style = {"regime": "momentum", "momentum_weight": 0.7, "reversal_weight": 0.3}
    entries = ForceFactors._adjust_weights_by_style(style, strategy_params["active_factors"])
    scores = ForceFactors._composite_force_scores(filtered, entries)
    assert set(scores) <= {d["code"] for d in filtered}
