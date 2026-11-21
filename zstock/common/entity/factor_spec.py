"""因子元数据自注册：FactorSpec + @factor + FactorRegistry。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_SPEC_ATTR = "__factor_spec__"


@dataclass(frozen=True)
class FactorSpec:
    """单个公开因子的自描述元数据（与算子方法绑定）。"""

    field: str
    name_en: str
    name_cn: str
    definition: str
    inputs: Tuple[str, ...]
    outputs: Tuple[str, ...]
    layer: str  # sector | dragon | force | market
    default_polarity: str = "positive"  # positive | negative
    regimes: Tuple[str, ...] = ("momentum", "reversal")
    windows: Optional[Tuple[int, ...]] = None
    """多窗口时展开为 field_{n}d；与落库列名对齐。"""
    include_base: bool = False
    """windows 非空时是否额外保留无后缀的 field（如 f31_excess_return）。"""
    catalog: bool = True
    """策略勾选目录是否展示；False 仍注册可供测评扫描。"""
    eval: bool = True
    """因子测评默认字段列表是否包含。"""


class FactorRegistry:
    """进程内因子规格表（装饰器写入）。"""

    _specs: Dict[str, FactorSpec] = {}
    _by_field: Dict[str, FactorSpec] = {}

    @classmethod
    def clear(cls) -> None:
        cls._specs.clear()
        cls._by_field.clear()

    @classmethod
    def register(cls, spec: FactorSpec) -> None:
        key = f"{spec.layer}.{spec.field}"
        cls._specs[key] = spec
        cls._by_field[spec.field] = spec

    @classmethod
    def all_specs(cls) -> List[FactorSpec]:
        return list(cls._specs.values())

    @classmethod
    def get(cls, field: str) -> Optional[FactorSpec]:
        return cls._by_field.get(field)

    @classmethod
    def expand_rows(
        cls,
        *,
        catalog_only: bool = False,
        eval_only: bool = False,
        layers: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """展开为 Web / 测评用的扁平行（含多窗口变体）。"""
        layer_set = set(layers) if layers else None
        rows: List[Dict[str, Any]] = []
        for spec in cls._specs.values():
            if layer_set is not None and spec.layer not in layer_set:
                continue
            if catalog_only and not spec.catalog:
                continue
            if eval_only and not spec.eval:
                continue
            rows.extend(_expand_spec(spec))
        rows.sort(key=lambda r: (r["layer"], r["field"]))
        return rows

    @classmethod
    def fields_for_layer(cls, layer: str, *, eval_only: bool = True) -> List[str]:
        return [
            r["field"]
            for r in cls.expand_rows(eval_only=eval_only, layers=[layer])
        ]


def _expand_spec(spec: FactorSpec) -> List[Dict[str, Any]]:
    base = {
        "layer": spec.layer,
        "name_en": spec.name_en,
        "definition": spec.definition,
        "inputs": list(spec.inputs),
        "outputs": list(spec.outputs),
        "default_polarity": spec.default_polarity,
        "regimes": list(spec.regimes),
    }
    rows: List[Dict[str, Any]] = []
    if spec.windows:
        if spec.include_base:
            rows.append(
                {
                    **base,
                    "field": spec.field,
                    "name_cn": spec.name_cn,
                }
            )
        for w in spec.windows:
            rows.append(
                {
                    **base,
                    "field": f"{spec.field}_{w}d",
                    "name_cn": (
                        f"{spec.name_cn}({w}日)"
                        if "日" not in spec.name_cn
                        else f"{spec.name_cn}({w})"
                    ),
                    "name_en": f"{spec.name_en}_{w}d",
                }
            )
    else:
        rows.append(
            {
                **base,
                "field": spec.field,
                "name_cn": spec.name_cn,
            }
        )
    return rows


def factor(spec: FactorSpec) -> Callable[[F], F]:
    """装饰公开算子：写入 __factor_spec__ 并登记 Registry。"""

    def decorator(fn: F) -> F:
        # @staticmethod / @classmethod 包裹时取 __func__
        underlying = getattr(fn, "__func__", fn)
        setattr(underlying, _SPEC_ATTR, spec)
        FactorRegistry.register(spec)
        return fn

    return decorator


