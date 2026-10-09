"""QMT 工厂：禁止默认静默 Mock；纸面路径显式 prefer_real=False。"""

import pytest

from zstock.order_management.qmt_client_factory import (
    QMTClientFactory,
    QMTConnectionError,
)


def test_prefer_real_false_uses_official_mock():
    client = QMTClientFactory.create(prefer_real=False, allow_mock=True)
    assert QMTClientFactory.is_mock(client) is True
    assert client.connect() is True


def test_real_connect_fail_without_mock_raises(monkeypatch):
    class Boom:
        def connect(self):
            return False

    monkeypatch.setattr(
        "app.utils.xtquant_util.QMTUtil",
        Boom,
        raising=True,
    )
    with pytest.raises(QMTConnectionError):
        QMTClientFactory.create(prefer_real=True, allow_mock=False)


def test_real_connect_fail_with_allow_mock_falls_back(monkeypatch):
    class Boom:
        def connect(self):
            raise RuntimeError("no miniQMT")

    monkeypatch.setattr("app.utils.xtquant_util.QMTUtil", Boom, raising=True)
    client = QMTClientFactory.create(prefer_real=True, allow_mock=True)
    assert QMTClientFactory.is_mock(client) is True
