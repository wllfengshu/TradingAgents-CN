"""低波策略：国债各 35%，其余 30% 先黄金，再银行，都不达标则政金债。"""

from zstock.common.book_codes import BOND, BOND30, GOLD, POLICY
from zstock.strategy_management.strategy_low_volatility import (
    BANK,
    WEIGHT_10Y,
    WEIGHT_30Y,
    WEIGHT_SLEEVE,
    low_volatility_targets,
)


def _up(n: int = 80) -> list[float]:
    return [float(i + 1) for i in range(n)]


def test_gold_takes_the_sleeve_when_both_trend():
    weights = low_volatility_targets({BANK: _up(), GOLD: _up()})
    assert weights[BOND30] == WEIGHT_30Y
    assert weights[BOND] == WEIGHT_10Y
    assert weights[GOLD] == WEIGHT_SLEEVE
    assert BANK not in weights
    assert POLICY not in weights
    assert abs(sum(weights.values()) - 1.0) < 1e-8


def test_bank_takes_the_sleeve_when_gold_fails():
    weights = low_volatility_targets({BANK: _up(), GOLD: [1.0] * 10})
    assert weights[BANK] == WEIGHT_SLEEVE
    assert GOLD not in weights
    assert POLICY not in weights
    assert abs(sum(weights.values()) - 1.0) < 1e-8


def test_policy_when_neither_trends():
    weights = low_volatility_targets({BANK: [1.0] * 5, GOLD: [1.0] * 5})
    assert weights[POLICY] == WEIGHT_SLEEVE
    assert BANK not in weights
    assert GOLD not in weights
    assert abs(sum(weights.values()) - 1.0) < 1e-8
