"""QMT 工厂：只连 HTTP 桥，连不上就抛错。"""

import pytest

from zstock.order_management.broker import (
    QMTClientFactory,
    QMTConnectionError,
)


def test_connect_failure_raises(monkeypatch):
    class Boom:
        def connect(self):
            return False

    monkeypatch.setattr(
        "zstock.common.utils.qmt_util.QMTUtil",
        Boom,
        raising=True,
    )
    with pytest.raises(QMTConnectionError):
        QMTClientFactory.create()


def test_connect_exception_raises(monkeypatch):
    class Boom:
        def connect(self):
            raise RuntimeError("bridge down")

    monkeypatch.setattr("zstock.common.utils.qmt_util.QMTUtil", Boom, raising=True)
    with pytest.raises(QMTConnectionError):
        QMTClientFactory.create()
