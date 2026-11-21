"""FactorSpec / FactorRegistry：加载真实因子装饰器注册表。"""

from __future__ import annotations

from zstock.common.entity.factor_spec import (
    FactorRegistry,
    FactorSpec,
    factor,
)
from zstock.factor_management.registry_bootstrap import ensure_factors_loaded


def test_ensure_factors_loaded_and_expand():
    ensure_factors_loaded()
    specs = FactorRegistry.all_specs()
    assert specs
    assert FactorRegistry.get("fcoop1_main_net_ratio") is not None or any(
        s.field.startswith("fcoop") for s in specs
    )
    rows = FactorRegistry.expand_rows(catalog_only=True)
    assert rows
    assert all("field" in r and "layer" in r for r in rows)
    eval_rows = FactorRegistry.expand_rows(eval_only=True, layers=["force"])
    assert isinstance(eval_rows, list)
    force_fields = FactorRegistry.fields_for_layer("force")
    assert isinstance(force_fields, list)


def test_windows_expand_and_decorator():
    FactorRegistry.clear()

    @factor(
        FactorSpec(
            field="f_ut_window",
            name_en="ut",
            name_cn="单测",
            definition="ut",
            inputs=("ohlcv",),
            outputs=("f_ut_window",),
            layer="dragon",
            windows=(5, 10),
            include_base=True,
            catalog=True,
            eval=True,
        )
    )
    def _dummy():
        return None

    rows = FactorRegistry.expand_rows()
    fields = {r["field"] for r in rows}
    assert "f_ut_window" in fields
    assert "f_ut_window_5d" in fields
    assert "f_ut_window_10d" in fields
    # 恢复真实注册表
    FactorRegistry.clear()
    ensure_factors_loaded()
