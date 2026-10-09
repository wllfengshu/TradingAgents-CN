# -*- coding: utf-8 -*-
"""依次运行 stock_info / ohlcv / fundamental 同步（经 QMT 子进程调用 xtquant）。"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


async def _run_all() -> None:
    from zstock.data_management.script.qmt_subprocess_bridge import patch_xtquant_data_utils
    patch_xtquant_data_utils()

    logger.info('=' * 50)
    logger.info('▶ [1/3] sync_stock_info')
    logger.info('=' * 50)
    from zstock.data_management.script import sync_stock_info
    await sync_stock_info.main()

    logger.info('=' * 50)
    logger.info('▶ [2/3] sync_ohlcv  (增量: 2026-08-19 ~ 今天)')
    logger.info('=' * 50)
    sys.argv = ['sync_ohlcv.py', '--start', '2026-08-19']
    from zstock.data_management.script import sync_ohlcv
    await sync_ohlcv.main()

    logger.info('=' * 50)
    logger.info('▶ [3/3] sync_fundamental')
    logger.info('=' * 50)
    sys.argv = ['sync_fundamental.py']
    from zstock.data_management.script import sync_fundamental
    await sync_fundamental.main()

    logger.info('=' * 50)
    logger.info('✅ 三项同步全部完成')
    logger.info('=' * 50)


def main() -> None:
    asyncio.run(_run_all())


if __name__ == '__main__':
    main()
