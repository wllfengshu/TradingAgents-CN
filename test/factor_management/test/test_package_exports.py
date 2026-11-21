from zstock.factor_management import (
    DragonFactors,
    FactorRegistry,
    FactorSpec,
    ForceFactors,
    MarketFactors,
    SectorFactors,
    factor,
)
import zstock.factor_management as fm
from zstock.common import PreFilters


def test_public_exports():
    assert SectorFactors is not None
    assert DragonFactors is not None
    assert ForceFactors is not None
    assert MarketFactors is not None
    assert FactorSpec is not None
    assert FactorRegistry is not None
    assert factor is not None
    assert set(fm.__all__) == {
        "SectorFactors",
        "DragonFactors",
        "ForceFactors",
        "MarketFactors",
        "FactorSpec",
        "FactorRegistry",
        "factor",
        "ensure_factors_loaded",
    }
    assert PreFilters is not None
