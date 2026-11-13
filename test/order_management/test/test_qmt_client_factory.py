"""QMT 工厂：只连 HTTP 桥，连不上就抛错。"""

import pytest

from zstock.order_management.qmt_client_factory import (
    QMTClientFactory,
    QMTConnectionError,
)


def test_connect_failure_raises(monkeypatch):
    class Boom:
        def connect(self):
            return False

    monkeypatch.setattr(
        "zstock.common.utils.qmt_util.py.QMTUtil",
        Boom,
        raising=True,
    )
    with pytest.raises(QMTConnectionError):
        QMTClientFactory.create()


def test_connect_exception_raises(monkeypatch):
    class Boom:
        def connect(self):
            raise RuntimeError("bridge down")

    monkeypatch.setattr("zstock.common.utils.qmt_util.py.QMTUtil", Boom, raising=True)
    with pytest.raises(QMTConnectionError):
        QMTClientFactory.create(prefer_real=False, allow_mock=True)


def test_is_mock_is_always_false():
    assert QMTClientFactory.is_mock(object()) is False
