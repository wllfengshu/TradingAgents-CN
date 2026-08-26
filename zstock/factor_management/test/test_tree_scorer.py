"""决策树评分：正式模型 + 2024-01-02 真实龙头字段。"""

from pathlib import Path

import pytest

from zstock.factor_management.tree_scorer import TreeScorer, _DEFAULT_MODEL_PATH


def test_tree_scorer_on_real_dragon_fields(signal_names_raw):
    if not Path(_DEFAULT_MODEL_PATH).is_file():
        pytest.skip("缺少 dragon_tree_v1.pkl")
    scorer = TreeScorer()
    field_values = {
        "f33_consecutive_boards": {},
        "f34_resonance_pct_5d": {},
        "f36_identity_premium": {},
    }
    for d in signal_names_raw["dragons"]:
        code = d["code"]
        field_values["f33_consecutive_boards"][code] = float(d.get("f33_consecutive_boards") or 0)
        field_values["f34_resonance_pct_5d"][code] = float(
            d.get("f34_resonance_pct_5d") or d.get("f34_resonance_pct") or 0
        )
        field_values["f36_identity_premium"][code] = float(d.get("f36_identity_premium") or 0)
    scores = scorer.score(field_values)
    assert set(scores) == {d["code"] for d in signal_names_raw["dragons"]}
    assert all(0.0 <= v <= 100.0 for v in scores.values())
    raw = scorer.predict_raw(field_values)
    assert set(raw) == set(scores)
    assert scorer.predict_raw({}) == {}
    assert scorer.score({}) == {}
