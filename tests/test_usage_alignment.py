"""The usage models stay aligned with the catalog of fields seen on real Tydoms.

`tests/usages.json` is built by `python -m tydomio.capture.catalog`.
"""

import re
from pathlib import Path
from typing import Annotated, get_args, get_origin

import pytest
from pydantic.fields import FieldInfo

from tydomio.capture.catalog import AREA, Descriptor, read_catalog
from tydomio.model.usages import (
    NOT_WRITTEN,
    USAGES,
    WRITES,
    Area,
    TydomEnum,
    UsageModel,
)

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


def is_writable(descriptor: Descriptor) -> bool:
    return "w" in (descriptor.permission or "")


def write_fields(usage: str) -> dict[str, FieldInfo]:
    write = WRITES.get(MODELS[usage])
    fields = {} if write is None else write.model_fields
    return {info.alias: info for info in fields.values() if info.alias is not None}


def not_written(usage: str) -> dict[str, str]:
    return dict(NOT_WRITTEN.get(MODELS[usage], {}))


WRITABLE = [
    pytest.param(usage, field, id=f"{usage}.{field.name}")
    for usage, entry in CATALOG.items()
    if usage in MODELS
    for field in entry.fields
    if is_writable(field)
]
EXPOSED = [
    pytest.param(usage, field, id=f"{usage}.{field.name}")
    for usage, entry in CATALOG.items()
    if usage in MODELS
    for field in entry.fields
    if field.name in write_fields(usage)
]
WRITABLE_COMMANDS = [
    pytest.param(usage, command.name, id=f"{usage}.{command.name}")
    for usage, entry in CATALOG.items()
    if usage in MODELS
    for command in entry.commands
    if "w" in command.permission
]
WRITE_FIELDS = [
    pytest.param(usage, name, id=f"{usage}.{name}")
    for usage in CATALOG
    if usage in MODELS
    for name in write_fields(usage)
]


def constraint(info: FieldInfo, name: str) -> object:
    """Give the `ge`, `le` or `multiple_of` constraint of the field, None if absent."""
    return next(
        (getattr(item, name) for item in info.metadata if hasattr(item, name)), None
    )


def write_enum(info: FieldInfo) -> type[TydomEnum] | None:
    """`E` of a field annotated `Annotated[E, member_of(E)] | None`."""
    for member in get_args(info.annotation):
        if get_origin(member) is Annotated:
            enum = get_args(member)[0]
            assert isinstance(enum, type)
            assert issubclass(enum, TydomEnum)
            return enum
    return None


@pytest.mark.parametrize(("usage", "field"), WRITABLE)
def test_writable_field_is_written_or_not_written_with_a_reason(
    usage: str, field: Descriptor
) -> None:
    exposed = field.name in write_fields(usage)
    excluded = field.name in not_written(usage)
    state = (
        "both in the write model and" if exposed else "neither in the write model nor"
    )
    assert exposed != excluded, (
        f"usage {usage!r}, writable field {field.name!r}: {state} in NOT_WRITTEN"
    )


@pytest.mark.parametrize(("usage", "command"), WRITABLE_COMMANDS)
def test_writable_command_is_not_written_with_a_reason(
    usage: str, command: str
) -> None:
    assert command in not_written(usage), (
        f"usage {usage!r}, command {command!r}: commands are not written, "
        "list it in NOT_WRITTEN"
    )


@pytest.mark.parametrize(("usage", "name"), WRITE_FIELDS)
def test_write_field_is_writable_in_the_catalog(usage: str, name: str) -> None:
    writable = {field.name for field in CATALOG[usage].fields if is_writable(field)}
    assert name in writable, f"usage {usage!r}: {name!r} is not writable in the catalog"


@pytest.mark.parametrize(("usage", "field"), EXPOSED)
def test_write_bounds_are_the_catalog_range(usage: str, field: Descriptor) -> None:
    info = write_fields(usage)[field.name]
    found = (constraint(info, "ge"), constraint(info, "le"))
    assert found == (field.min, field.max), (
        f"usage {usage!r}, field {field.name!r}: "
        f"bounds {found}, catalog ({field.min}, {field.max})"
    )


@pytest.mark.parametrize(("usage", "field"), EXPOSED)
def test_write_step_is_the_catalog_step(usage: str, field: Descriptor) -> None:
    info = write_fields(usage)[field.name]
    # a step of 1 is the int type
    is_int = int in get_args(info.annotation)
    found = 1 if is_int else constraint(info, "multiple_of")
    assert found == field.step, (
        f"usage {usage!r}, field {field.name!r}: step {found}, catalog {field.step}"
    )


@pytest.mark.parametrize(("usage", "field"), EXPOSED)
def test_write_enum_values_are_the_catalog_values(
    usage: str, field: Descriptor
) -> None:
    enum = write_enum(write_fields(usage)[field.name])
    found = None if enum is None else sorted(member.value for member in enum)
    expected = None if field.enum_values is None else sorted(field.enum_values)
    assert found == expected, (
        f"usage {usage!r}, field {field.name!r}: enum {found}, catalog {expected}"
    )
