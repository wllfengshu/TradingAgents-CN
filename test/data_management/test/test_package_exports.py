from zstock import data_management
from zstock.data_management import DataQueryService, strategy_store


def test_public_exports():
    assert DataQueryService is not None
    assert strategy_store is not None
    assert data_management.strategy_store is strategy_store
