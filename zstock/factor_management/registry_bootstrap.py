"""触发 @factor 自注册（放在 factor 包，避免 common→factor 环）。"""

from __future__ import annotations

import importlib

# 模块级 FactorRegistry.register 的哨兵（非 @factor），用于检测 clear 后需 reload
_SENTINEL_FIELD = "f35_bollinger_pass"

_FACTOR_MODULES = (
    "zstock.factor_management.factors_dragon",
    "zstock.factor_management.factors_force",
    "zstock.factor_management.factors_market",
    "zstock.factor_management.factors_sector",
)


def ensure_factors_loaded() -> None:
    """导入四层门面以触发装饰器与模块级登记（幂等）。

    ``FactorRegistry.clear()`` 后会 ``importlib.reload`` 各模块，
    以恢复模块级 ``FactorRegistry.register(...)``（非装饰器路径）。
    """
    from zstock.common.entity.factor_spec import FactorRegistry

    need_reload = FactorRegistry.get(_SENTINEL_FIELD) is None
    for name in _FACTOR_MODULES:
        mod = importlib.import_module(name)
        if need_reload:
            importlib.reload(mod)
