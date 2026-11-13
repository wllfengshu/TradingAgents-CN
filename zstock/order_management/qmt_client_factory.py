"""QMT 客户端工厂：只连接本机 HTTP 桥。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class QMTConnectionError(ConnectionError):
    """HTTP 桥不可用。"""


class QMTClientFactory:
    """创建已连接的 QMT 桥客户端。连不上就抛错。"""

    @staticmethod
    def create(prefer_real: bool = True, allow_mock: bool = False) -> Any:
        """连接 HTTP 桥。prefer_real / allow_mock 已无分支，保留参数以免旧调用签名报错。"""
        del prefer_real, allow_mock
        last_error: str | None = None
        try:
            from zstock.common.utils.qmt_util import QMTUtil

            client = QMTUtil()
            if client.connect():
                logger.info("已连接 QMT 桥")
                return client
            last_error = "QMTUtil.connect() 返回 False"
        except Exception as e:
            last_error = str(e)
        raise QMTConnectionError(f"QMT 桥不可用（{last_error}）")

    @staticmethod
    def is_mock(qmt_util: Any) -> bool:
        del qmt_util
        return False
