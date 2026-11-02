"""Catalog of the fields and commands of each usage, extracted from captures.

Run as `python -m tydomio.capture.catalog CAPTURE_DIR CATALOG_FILE`: the capture's
descriptors are merged into the catalog file, which is written only without
conflict. The catalog describes types of equipment: no id, count, name or value.
"""

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from pydantic import BaseModel, ConfigDict, RootModel, ValidationError

from ..error import TydomIoError
from .jsonvalue import JsonValue, NotJson, parse_json

AREA: Final = "@area"
"""Catalog key of the areas; `@` never starts a Tydom usage name."""

EXIT_WRITTEN: Final = 0
EXIT_CONFLICT: Final = 1
EXIT_INVALID: Final = 2

type Number = int | float
type Documents = Mapping[str, JsonValue]


class Descriptor(BaseModel):
    """Attributes of a field or a command parameter, without any value."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    type: str
    permission: str | None = None
    validity: str | None = None
    unit: str | None = None
    min: Number | None = None
    max: Number | None = None
    step: Number | None = None
    size: int | None = None
    enum_values: tuple[str, ...] | None = None


class Command(BaseModel):
    """A command of a usage, from cmeta."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    permission: str
    parameters: tuple[Descriptor, ...] = ()


class Usage(BaseModel):
    """Fields and commands of one usage, each sorted by name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fields: tuple[Descriptor, ...] = ()
    commands: tuple[Command, ...] = ()


class Catalog(RootModel[dict[str, Usage]]):
    """Usage name to its fields and commands."""


@dataclass(frozen=True)
class Conflict:
    """An attribute that differs between two descriptions of the same entry."""

    usage: str
    name: str
    attribute: str
    known: object
    found: object

    @override
    def __str__(self) -> str:
        """Describe the conflict on one line."""
        return (
            f"{self.usage}: {self.name}: {self.attribute}: "
            f"{self.known!r} != {self.found!r}"
        )


# shapes of the capture envelopes, reduced to what the catalog reads
class _ConfigEndpoint(BaseModel):
    id_device: int
    id_endpoint: int
    last_usage: str


class _Config(BaseModel):
    endpoints: list[_ConfigEndpoint]


class _MetaEndpoint(BaseModel):
    id: int
    metadata: list[Descriptor] = []
    cmetadata: list[Command] = []


class _Device(BaseModel):
    id: int
    endpoints: list[_MetaEndpoint]


class _Envelope[T](BaseModel):
    body: T


CONFIG: Final = "configs_file.json"
DEVICES_META: Final = "devices_meta.json"
DEVICES_CMETA: Final = "devices_cmeta.json"
AREAS_META: Final = "areas_meta.json"
AREAS_CMETA: Final = "areas_cmeta.json"
CAPTURE_FILES: Final = (CONFIG, DEVICES_META, DEVICES_CMETA, AREAS_META, AREAS_CMETA)


class InvalidInput(TydomIoError):
    """A capture or catalog document missing or not of the expected shape."""


def _sorted_enum(descriptor: Descriptor) -> Descriptor:
    if descriptor.enum_values is None:
        return descriptor
    return descriptor.model_copy(
        update={"enum_values": tuple(sorted(set(descriptor.enum_values)))}
    )


def _sorted_command(command: Command) -> Command:
    return command.model_copy(
        update={"parameters": tuple(_sorted_enum(p) for p in command.parameters)}
    )


def _merge_field(
    usage: str, known: Descriptor, found: Descriptor, conflicts: list[Conflict]
) -> Descriptor:
    for attribute in Descriptor.model_fields:
        if attribute == "enum_values":
            continue
        if getattr(known, attribute) != getattr(found, attribute):
            conflicts.append(
                Conflict(
                    usage,
                    known.name,
                    attribute,
                    getattr(known, attribute),
                    getattr(found, attribute),
                )
            )
    if known.enum_values is None and found.enum_values is None:
        return known
    union = set(known.enum_values or ()) | set(found.enum_values or ())
    return known.model_copy(update={"enum_values": tuple(sorted(union))})


def _merge_usage(
    name: str, known: Usage, found: Usage, conflicts: list[Conflict]
) -> Usage:
    fields = {field.name: field for field in known.fields}
    for field in found.fields:
        fields[field.name] = (
            _merge_field(name, fields[field.name], field, conflicts)
            if field.name in fields
            else field
        )
    commands = {command.name: command for command in known.commands}
    for command in found.commands:
        if command.name in commands and commands[command.name] != command:
            conflicts.append(
                Conflict(
                    name,
                    command.name,
                    "command",
                    commands[command.name].model_dump(exclude_none=True),
                    command.model_dump(exclude_none=True),
                )
            )
        else:
            commands[command.name] = command
    return Usage(
        fields=tuple(fields[key] for key in sorted(fields)),
        commands=tuple(commands[key] for key in sorted(commands)),
    )


def merge(known: Catalog, found: Catalog) -> tuple[Catalog, list[Conflict]]:
    """Merge `found` into `known`: entries added, enum values unioned.

    Any other attribute that differs for an existing entry is a conflict.
    """
    conflicts: list[Conflict] = []
    usages = dict(known.root)
    for name, usage in found.root.items():
        usages[name] = (
            _merge_usage(name, usages[name], usage, conflicts)
            if name in usages
            else _merge_usage(name, Usage(), usage, conflicts)
        )
    return Catalog({key: usages[key] for key in sorted(usages)}), conflicts


def _entry(name: str, endpoint: _MetaEndpoint) -> Catalog:
    return Catalog(
        {
            name: Usage(
                fields=tuple(_sorted_enum(field) for field in endpoint.metadata),
                commands=tuple(_sorted_command(c) for c in endpoint.cmetadata),
            )
        }
    )


def _validated[T](envelope: type[_Envelope[T]], documents: Documents, name: str) -> T:
    try:
        return envelope.model_validate(documents[name]).body
    except KeyError, ValidationError:
        # the error details may hold content: only the document is named
        raise InvalidInput(f"{name}: missing or not of the expected shape") from None


def _entries(documents: Documents) -> Iterable[Catalog]:
    config = _validated(_Envelope[_Config], documents, CONFIG)
    usages = {
        (endpoint.id_device, endpoint.id_endpoint): endpoint.last_usage
        for endpoint in config.endpoints
    }
    devices = (
        *_validated(_Envelope[list[_Device]], documents, DEVICES_META),
        *_validated(_Envelope[list[_Device]], documents, DEVICES_CMETA),
    )
    areas = (
        *_validated(_Envelope[list[_MetaEndpoint]], documents, AREAS_META),
        *_validated(_Envelope[list[_MetaEndpoint]], documents, AREAS_CMETA),
    )
    for device in devices:
        for endpoint in device.endpoints:
            # an endpoint missing from the configuration has no known usage
            usage = usages.get((device.id, endpoint.id))
            if usage is not None:
                yield _entry(usage, endpoint)
    for area in areas:
        yield _entry(AREA, area)


def extract(documents: Documents) -> tuple[Catalog, list[Conflict]]:
    """Catalog of the capture documents (envelopes by file name), deduplicated.

    Raise InvalidInput when a document is missing or not of the expected shape.
    """
    catalog = Catalog({})
    conflicts: list[Conflict] = []
    for entry in _entries(documents):
        catalog, found = merge(catalog, entry)
        conflicts.extend(found)
    return catalog, conflicts


def dump(catalog: Catalog) -> str:
    """Deterministic JSON of the catalog, for reviewable diffs."""
    content = catalog.model_dump(mode="json", exclude_none=True)
    return json.dumps(content, indent=2, sort_keys=True) + "\n"


def read_capture(directory: Path) -> Documents:
    """Parse the capture documents of `directory`, by file name."""
    documents: dict[str, JsonValue] = {}
    for name in CAPTURE_FILES:
        try:
            documents[name] = parse_json((directory / name).read_bytes())
        except OSError, NotJson:
            raise InvalidInput(f"{name}: missing or not JSON") from None
    return documents


def read_catalog(path: Path) -> Catalog:
    """Read a catalog file, empty when it does not exist yet."""
    if not path.exists():
        return Catalog({})
    try:
        return Catalog.model_validate(parse_json(path.read_bytes()))
    except OSError, NotJson, ValidationError:
        raise InvalidInput(f"{path}: not a catalog") from None


def main(argv: Sequence[str] | None = None) -> int:
    """Merge a capture into a catalog file; return the exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m tydomio.capture.catalog",
        description="Merge the usage descriptors of a capture into a catalog file.",
    )
    parser.add_argument("capture", type=Path, help="directory of tydomio-capture")
    parser.add_argument("catalog", type=Path, help="catalog JSON file to update")
    args = parser.parse_args(argv)
    try:
        found, conflicts = extract(read_capture(args.capture))
        catalog, merged_conflicts = merge(read_catalog(args.catalog), found)
    except InvalidInput as error:
        print(error, file=sys.stderr)
        return EXIT_INVALID
    conflicts.extend(merged_conflicts)
    if conflicts:
        for conflict in conflicts:
            print(conflict, file=sys.stderr)
        return EXIT_CONFLICT
    args.catalog.write_text(dump(catalog), encoding="utf-8")
    return EXIT_WRITTEN


if __name__ == "__main__":
    sys.exit(main())
