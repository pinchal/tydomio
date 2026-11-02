"""The usage models stay aligned with the catalog of fields seen on real Tydoms.

`tests/usages.json` is built by `python -m tydomio.capture.catalog`.
"""

import re
from pathlib import Path
from typing import get_args

import pytest
from pydantic.fields import FieldInfo

from tydomio.capture.catalog import AREA, Descriptor, read_catalog
from tydomio.model.usages import USAGES, Area, TydomEnum, UsageModel

CATALOG = read_catalog(Path(__file__).parent / "usages.json").root
MODELS: dict[str, type[UsageModel]] = {**USAGES, AREA: Area}
# a single value such as "XX.YY.ZZ" is the format of a version, not an enum
FORMAT = re.compile(r"[A-Z]{2}(\.[A-Z]{2})+")
TYPES = {"numeric": float, "boolean": bool, "string": str, "hexstring": str}


def is_readable(descriptor: Descriptor) -> bool:
    # write-only fields belong to commands, not to the read models
    return "r" in (descriptor.permission or "")


def is_enum(descriptor: Descriptor) -> bool:
    values = descriptor.enum_values or ()
    return bool(values) and not (len(values) == 1 and FORMAT.fullmatch(values[0]))


FIELDS = [
    pytest.param(usage, field, id=f"{usage}.{field.name}")
    for usage, entry in CATALOG.items()
    if usage in MODELS
    for field in entry.fields
    if is_readable(field)
]
ENUM_FIELDS = [
    pytest.param(usage, field, id=f"{usage}.{field.name}")
    for usage, entry in CATALOG.items()
    if usage in MODELS
    for field in entry.fields
    if is_readable(field) and is_enum(field)
]


def model_field(usage: str, name: str) -> FieldInfo:
    fields = {info.alias: info for info in MODELS[usage].model_fields.values()}
    assert name in fields, f"usage {usage!r}: no field with alias {name!r}"
    return fields[name]


def value_type(info: FieldInfo) -> object:
    """`X` of a field annotated `Reading[X] | None`."""
    reading = get_args(info.annotation)[0]
    return reading.__pydantic_generic_metadata__["args"][0]


def enum_of(info: FieldInfo) -> type[TydomEnum] | None:
    for member in get_args(value_type(info)):
        if isinstance(member, type) and issubclass(member, TydomEnum):
            return member
    return None


@pytest.mark.parametrize("usage", list(CATALOG))
def test_usage_has_a_model(usage: str) -> None:
    assert usage in MODELS, f"usage {usage!r} has no model: add it to USAGES"


@pytest.mark.parametrize(("usage", "field"), FIELDS)
def test_field_type_is_compatible(usage: str, field: Descriptor) -> None:
    info = model_field(usage, field.name)
    actual = value_type(info)
    if is_enum(field):
        enum = enum_of(info)
        compatible = enum is not None and set(get_args(actual)) == {enum, str}
        expected = "a TydomEnum | str"
    else:
        compatible = actual is TYPES[field.type]
        expected = TYPES[field.type].__name__
    assert compatible, (
        f"usage {usage!r}, field {field.name!r} ({field.type}): "
        f"expected Reading[{expected}], found Reading[{actual}]"
    )


@pytest.mark.parametrize(("usage", "field"), ENUM_FIELDS)
def test_enum_values_are_members(usage: str, field: Descriptor) -> None:
    enum = enum_of(model_field(usage, field.name))
    assert enum is not None, f"usage {usage!r}, field {field.name!r}: no TydomEnum"
    missing = sorted(set(field.enum_values or ()) - {member.value for member in enum})
    assert not missing, (
        f"usage {usage!r}, field {field.name!r}: {enum.__name__} lacks {missing}"
    )
