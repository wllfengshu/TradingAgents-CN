"""QMT 客户端工厂：默认只连真实 miniQMT；Mock 必须显式打开。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class QMTConnectionError(ConnectionError):
    """真实 QMT 不可用，且未允许 Mock。"""


def create_qmt_util(prefer_real: bool = True, allow_mock: bool = False) -> Any:
    """
    创建已连接的 QMT 客户端。

    - prefer_real=True, allow_mock=False（默认）：只连真实 QMT，失败则抛错，
      禁止静默落到 Mock（避免实盘以为在成交、实际在模拟）。
    - prefer_real=True, allow_mock=True：先真连，失败再 Mock（纸面/联调）。
    - prefer_real=False：明确使用 Mock。
    """
    if prefer_real:
        last_error: str | None = None
        try:
            from app.utils.xtquant_util import QMTUtil

            client = QMTUtil()
            if client.connect():
                logger.info("使用真实 QMTUtil")
                return client
            last_error = "QMTUtil.connect() 返回 False"
        except Exception as e:
            last_error = str(e)

        if not allow_mock:
            raise QMTConnectionError(
                f"QMT 不可用（{last_error}），拒绝静默 Mock。"
                "纸面测试请传 allow_mock=True 或 prefer_real=False。"
            )
        logger.warning("QMT 不可用（%s），allow_mock=True，回退 Mock", last_error)

    from app.utils.xtquant_mock_util import get_xtquant_mock_client

    client = get_xtquant_mock_client()
    client.connect()
    logger.info("使用 MockQMTUtil")
    return client


def is_mock_client(qmt_util: Any) -> bool:
    try:
        from app.utils.xtquant_mock_util import MockQMTUtil

        return isinstance(qmt_util, MockQMTUtil)
    except ImportError:
        return False
