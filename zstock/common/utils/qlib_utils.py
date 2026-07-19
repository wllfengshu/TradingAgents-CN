"""
Qlib 工具模块

集中管理 Qlib 的导入、初始化和常用功能。
"""

import logging
import sys
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def ensure_qlib_available():
    """
    确保 Qlib 已安装并可用

    如果 Qlib 未安装，直接抛出 RuntimeError

    Returns:
        Dict: 包含所有必要的 Qlib 模块和类

    Raises:
        RuntimeError: 当 Qlib 未安装时
    """
    try:
        import qlib
        from qlib.backtest import (
            backtest_loop,
            get_strategy_executor,
            get_exchange,
            create_account_instance,
        )
        from qlib.backtest.account import Account
        from qlib.backtest.decision import BaseTradeDecision, Order, OrderDir, OrderHelper
        from qlib.backtest.exchange import Exchange
        from qlib.backtest.executor import SimulatorExecutor
        from qlib.backtest.utils import CommonInfrastructure, LevelInfrastructure
        from qlib.strategy.base import BaseStrategy
        from qlib.data import D

        logger.info("✅ Qlib 已可用")

        return {
            'qlib': qlib,
            'backtest_loop': backtest_loop,
            'get_strategy_executor': get_strategy_executor,
            'get_exchange': get_exchange,
            'create_account_instance': create_account_instance,
            'Account': Account,
            'BaseTradeDecision': BaseTradeDecision,
            'Order': Order,
            'OrderDir': OrderDir,
            'OrderHelper': OrderHelper,
            'Exchange': Exchange,
            'SimulatorExecutor': SimulatorExecutor,
            'CommonInfrastructure': CommonInfrastructure,
            'LevelInfrastructure': LevelInfrastructure,
            'BaseStrategy': BaseStrategy,
            'D': D,
        }
    except ImportError as e:
        raise RuntimeError(
            "❌ Qlib 未安装。请手动安装：\n"
            "项目说明文档.md  -> qlib安装教程，注意，是安装当前目录下的qlib"
        ) from e


def get_qlib_module(name: str) -> Any:
    """
    获取单个 Qlib 模块或类

    Args:
        name: 模块或类的名称，例如 'backtest_loop', 'D', 'BaseStrategy'

    Returns:
        Any: 指定的 Qlib 模块或类

    Raises:
        KeyError: 当模块不存在时
        RuntimeError: 当 Qlib 未安装时
    """
    qlib_modules = ensure_qlib_available()
    if name not in qlib_modules:
        raise KeyError(f"Qlib 模块 '{name}' 不存在。可用的模块有: {list(qlib_modules.keys())}")
    return qlib_modules[name]


def initialize_qlib(qlib_config: Optional[Dict] = None) -> bool:
    """
    初始化 Qlib 环境

    这是在运行回测或因子计算前的必要步骤。
    Qlib 是量化因子计算和回测框架，需要初始化数据源和交易参数。

    Args:
        qlib_config: Qlib 初始化配置，例如：
            {
                'provider_uri': 'file:///path/to/qlib/data',
                'region': 'cn',
                'limit_threshold': (0.1, -0.1),  # tuple 格式：(up_limit, down_limit)
                'benchmark': 'SH000300',
            }
            如果为 None，使用默认配置。

    Returns:
        bool: 初始化是否成功

    Raises:
        Exception: 初始化失败时抛出异常
    """
    try:
        qlib = get_qlib_module('qlib')

        if qlib_config is None:
            # 完整的默认配置（只传必要参数）
            qlib_config = {
                'provider_uri': 'file:///D:/data/qlib' if sys.platform == 'win32' else 'file:///data/qlib',
                'region': 'cn',
                'limit_threshold': (0.1, -0.1),  # tuple 格式：(up, down)
                'auto_mount': False,  # Windows 上不自动挂载
            }

        logger.info(f"🔧 初始化 Qlib: provider_uri={qlib_config.get('provider_uri')}, region={qlib_config.get('region')}")

        if hasattr(qlib, 'init'):
            qlib.init(**qlib_config)

        logger.info("✅ Qlib 初始化成功")
        return True

    except Exception as e:
        logger.error(f"❌ Qlib 初始化失败: {e}", exc_info=True)
        raise


def get_qlib_data_handler() -> Any:
    """
    获取 Qlib 数据处理器

    Returns:
        Any: Qlib 的 D（DataHandler）对象
    """
    return get_qlib_module('D')


def get_qlib_backtest_tools() -> Dict[str, Any]:
    """
    获取 Qlib 回测所需的所有工具

    Returns:
        Dict: 包含回测所需的类和函数
            {
                'backtest_loop': ...,
                'get_exchange': ...,
                'create_account_instance': ...,
                'SimulatorExecutor': ...,
                'BaseStrategy': ...,
                'CommonInfrastructure': ...,
                'LevelInfrastructure': ...,
            }
    """
    qlib_modules = ensure_qlib_available()
    return {
        'backtest_loop': qlib_modules['backtest_loop'],
        'get_exchange': qlib_modules['get_exchange'],
        'create_account_instance': qlib_modules['create_account_instance'],
        'SimulatorExecutor': qlib_modules['SimulatorExecutor'],
        'BaseStrategy': qlib_modules['BaseStrategy'],
        'CommonInfrastructure': qlib_modules['CommonInfrastructure'],
        'LevelInfrastructure': qlib_modules['LevelInfrastructure'],
        'Exchange': qlib_modules['Exchange'],
        'BaseTradeDecision': qlib_modules['BaseTradeDecision'],
        'Order': qlib_modules['Order'],
        'OrderDir': qlib_modules['OrderDir'],
    }


def get_qlib_factor_tools() -> Dict[str, Any]:
    """
    获取 Qlib 因子计算所需的工具

    Returns:
        Dict: 包含因子计算所需的类和函数
            {
                'D': ...,  # DataHandler
                'Alpha158': ...,  # 因子计算器
            }
    """
    try:
        from qlib.contrib.data.handler import Alpha158
        qlib_modules = ensure_qlib_available()
        return {
            'D': qlib_modules['D'],
            'Alpha158': Alpha158,
        }
    except ImportError as e:
        raise RuntimeError(
            "❌ Qlib 因子计算模块未安装。"
            "请确保已完整安装 Qlib: pip install -e ./qlib"
        ) from e
