from zstock.order_management import (
    ExecutionStrategy,
    OrderGenerator,
    OrderManagementPipeline,
    QMTClientFactory,
    QMTConnectionError,
    TradeSettlement,
    XtQuantExecutor,
)


def test_public_exports():
    assert OrderGenerator is not None
    assert hasattr(OrderGenerator, "persist_orders")
    assert XtQuantExecutor is not None
    assert TradeSettlement is not None
    assert ExecutionStrategy is not None
    assert OrderManagementPipeline is not None
    assert QMTClientFactory is not None
    assert QMTConnectionError is not None
