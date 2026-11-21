"""score_maps：分段边界与 sigmoid 输出区间。"""

from __future__ import annotations

import pytest

from zstock.common.utils.score_maps import (
    map_atr_ratio_to_score,
    map_momentum_to_score,
    map_slope_to_score,
    map_vol_ratio_to_score,
    sigmoid_score,
)


def test_sigmoid_and_slope_momentum_bounds():
    assert 0.0 <= sigmoid_score(0.0, k=30.0) <= 100.0
    assert sigmoid_score(0.0, k=30.0) == pytest.approx(50.0)
    high = map_slope_to_score(0.01)
    low = map_slope_to_score(-0.01)
    assert high > low
    assert 0.0 <= map_momentum_to_score(0.1) <= 100.0
    # clip 极端输入不炸
    assert 0.0 <= sigmoid_score(1e6, k=800.0) <= 100.0
    assert 0.0 <= sigmoid_score(-1e6, k=800.0) <= 100.0


def test_vol_ratio_segment_boundaries():
    assert map_vol_ratio_to_score(0.4) == pytest.approx(10.0)
    assert map_vol_ratio_to_score(0.5) == pytest.approx(10.0)
    assert map_vol_ratio_to_score(1.0) == pytest.approx(90.0)
    assert map_vol_ratio_to_score(1.5) == pytest.approx(100.0)
    mid = map_vol_ratio_to_score(2.25)
    assert 20.0 < mid < 100.0
    assert map_vol_ratio_to_score(5.0) == pytest.approx(10.0)


def test_atr_ratio_segments():
    assert map_atr_ratio_to_score(0.005) == pytest.approx(90.0)
    assert map_atr_ratio_to_score(0.02) < 90.0
    assert map_atr_ratio_to_score(0.04) < 50.0
    assert map_atr_ratio_to_score(0.1) == pytest.approx(10.0)
    custom = map_atr_ratio_to_score(0.02, segments=(0.02, 0.04, 0.08))
    assert custom == pytest.approx(90.0)
