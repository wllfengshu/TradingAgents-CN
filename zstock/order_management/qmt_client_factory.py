"""QMT 客户端工厂：优先真实 miniQMT，否则 Mock（延迟导入 app 层）。"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def create_qmt_util(prefer_real: bool = True) -> Any:
    """
    创建已连接的 QMT 客户端。

    prefer_real=True 时尝试 QMTUtil；失败或未安装则回退 MockQMTUtil。
    """
    if prefer_real:
        try:
            from app.utils.xtquant_util import QMTUtil

            client = QMTUtil()
            if client.connect():
                logger.info("使用真实 QMTUtil")
                return client
            logger.warning("QMTUtil 连接失败，回退 Mock")
        except Exception as e:
            logger.warning("QMTUtil 不可用 (%s)，回退 Mock", e)

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
