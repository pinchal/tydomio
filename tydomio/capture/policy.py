r"""Anonymization rules per endpoint: the reviewed security control, pure data.

A rule path uses "." between keys, "[]" for a list item, "*" for any key and
"*suffix" for any key ending with suffix; "\." is a dot inside a key. Rules apply
to leaves only. Paths marked assumed come from shapes not seen on a real box: they
apply only if present.
"""

import functools
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

RULES_VERSION: Final = 1

LIST_ITEM: Final = "[]"
ANY_KEY: Final = "*"


class Action(StrEnum):
    """What a rule does to a leaf."""

    KEEP = "keep"
    ID = "id"
    NAME = "name"
    REDACT = "redact"
    VALUE = "value"


@dataclass(frozen=True)
class Keep:
    """Constraint a kept value must match, else it is redacted and reported."""

    json_type: type | tuple[type, ...]
    pattern: str | None = None


@dataclass(frozen=True)
class Rule:
    """Action on the leaves at a path."""

    path: str
    action: Action
    keep: Keep | None = None
    kind: str | None = None  # for NAME


@dataclass(frozen=True)
class EndpointPolicy:
    """Rules of an endpoint, and the subtrees whose keys are all renamed."""

    rules: tuple[Rule, ...]
    opaque: tuple[str, ...] = ()


@functools.cache
def parse_path(path: str) -> tuple[str, ...]:
    """Split a rule path into its segments: keys, LIST_ITEM and wildcards."""
    segments: list[str] = []
    for part in re.split(r"(?<!\\)\.", path):
        key = part
        items = 0
        while key.endswith(LIST_ITEM):
            key = key.removesuffix(LIST_ITEM)
            items += 1
        if key:
            segments.append(key.replace("\\.", "."))
        segments.extend([LIST_ITEM] * items)
    return tuple(segments)


def is_wildcard(segment: str) -> bool:
    """Whether the segment matches keys by wildcard rather than by name."""
    return segment.startswith(ANY_KEY)


def segment_matches(segment: str, key: str) -> bool:
    """Whether a key segment matches the key."""
    if not is_wildcard(segment):
        return segment == key
    suffix = segment.removeprefix(ANY_KEY)
    return key.endswith(suffix) and len(key) > len(suffix)


_WORD = r"[A-Za-z0-9_]{1,64}"
_SHORT_WORD = r"[A-Za-z0-9_]{1,32}"
_VERSION = r"[0-9A-Za-z._-]{1,32}"
_PROTOCOL = r"[A-Za-z0-9_-]{1,32}"

WORD: Final = Keep(str, _WORD)
SHORT_WORD: Final = Keep(str, _SHORT_WORD)
VERSION: Final = Keep(str, _VERSION)
PROTOCOL: Final = Keep(str, _PROTOCOL)
BOOL: Final = Keep(bool)
INT: Final = Keep(int)
NUMBER: Final = Keep((int, float))
ENUM_VALUE: Final = Keep(str, r"[A-Za-z0-9_.-]{1,64}")


def _keep(path: str, keep: Keep) -> Rule:
    return Rule(path, Action.KEEP, keep)


def _id(path: str) -> Rule:
    return Rule(path, Action.ID)


def _name(path: str, kind: str) -> Rule:
    return Rule(path, Action.NAME, kind=kind)


def _redact(path: str) -> Rule:
    return Rule(path, Action.REDACT)


def _descriptor(prefix: str) -> tuple[Rule, ...]:
    """Rules of the meta descriptor fields under prefix."""
    return (
        _keep(f"{prefix}.name", WORD),
        _keep(f"{prefix}.type", WORD),
        _keep(f"{prefix}.permission", WORD),
        _keep(f"{prefix}.validity", WORD),
        _keep(f"{prefix}.unit", Keep(str, r"[A-Za-z0-9_%°/.-]{1,32}")),
        _keep(f"{prefix}.shared", BOOL),
        _keep(f"{prefix}.min", NUMBER),
        _keep(f"{prefix}.max", NUMBER),
        _keep(f"{prefix}.step", NUMBER),
        _keep(f"{prefix}.size", INT),
        _keep(f"{prefix}.enum_values[]", ENUM_VALUE),
    )


def _protocols(prefix: str) -> tuple[Rule, ...]:
    """Rules of the protocol items under prefix."""
    return (
        _keep(f"{prefix}.protocol", PROTOCOL),
        _keep(f"{prefix}.available", BOOL),
        _keep(f"{prefix}.installed", BOOL),
        _keep(f"{prefix}.ready", BOOL),
        _keep(f"{prefix}.status", PROTOCOL),
        _keep(f"{prefix}.installStatus", PROTOCOL),
    )


def _cmeta(prefix: str) -> tuple[Rule, ...]:
    """Rules of the cmeta command fields under prefix."""
    return (
        _keep(f"{prefix}.name", WORD),
        _keep(f"{prefix}.permission", WORD),
        *_descriptor(f"{prefix}.parameters[]"),
    )


def _data(prefix: str) -> tuple[Rule, ...]:
    """Rules of the data items under prefix."""
    return (
        _keep(f"{prefix}.name", WORD),
        _keep(f"{prefix}.validity", WORD),
        Rule(f"{prefix}.value", Action.VALUE),
    )


_DEVICE_IDS = (
    _id("[].id"),
    _id("[].endpoints[].id"),
    _keep("[].endpoints[].error", INT),
    # seen with type "area": id is then an areas[].id, mapped in the same id space
    _keep("[].endpoints[].link.type", WORD),
    _keep("[].endpoints[].link.subtype", WORD),
    _id("[].endpoints[].link.id"),
)
# assumed: areas have no endpoints[] level, as AreasData in tydomio/model/data.py
_AREA_IDS = (_id("[].id"), _keep("[].error", INT))

_CONFIGS_FILE = EndpointPolicy(
    rules=(
        _keep("version", VERSION),
        _keep("version_application", VERSION),
        _keep("os", VERSION),
        _keep("old_tycam", BOOL),
        _keep("endpoints[].picto", WORD),
        _keep("endpoints[].first_usage", WORD),
        _keep("endpoints[].last_usage", WORD),
        _keep("endpoints[].skill", WORD),
        _keep("endpoints[].anticipation_start", BOOL),
        _keep(
            "endpoints[].widget_behavior.tutorial_id",
            Keep(str, r"[A-Za-z0-9_+]{1,64}"),
        ),
        _keep("scenarios[].picto", WORD),
        _keep("scenarios[].type", WORD),
        _keep("groups[].picto", WORD),
        _keep("groups[].usage", WORD),
        _keep("groups[].group_all", BOOL),
        _keep("groups[].is_group_user", BOOL),
        _keep("areas[].picto", WORD),
        _keep("areas[].first_usage", WORD),
        _keep("areas[].last_usage", WORD),
        _keep("areas[].anticipation_start", BOOL),
        _keep("moments[].color", INT),
        _keep("zigbee_networks[].is_connected", BOOL),
        _keep("zigbee_networks[].type", WORD),
        _id("endpoints[].id_device"),
        _id("endpoints[].id_endpoint"),
        _id("endpoints[].space_id"),
        _id("scenarios[].id"),
        _id("scenarios[].rule_id"),
        _id("groups[].id"),
        _id("areas[].id"),
        _id("moments[].id"),
        _id("moments[].rule_id"),
        _name("endpoints[].name", "endpoint"),
        _name("scenarios[].name", "scenario"),
        _name("groups[].name", "group"),
        _name("areas[].name", "area"),
        _name("moments[].name", "moment"),
        _name("zigbee_networks[].name", "zigbee"),
        _redact("date"),
        _redact("id_catalog"),
        _redact("zigbee_networks[].extended_pan_id"),
    )
)

_INFO = EndpointPolicy(
    rules=(
        _keep("productName", Keep(str, r"[A-Za-z0-9 ._-]{1,32}")),
        _keep("apiMode", BOOL),
        _keep("config", VERSION),
        _keep("bddStatus", INT),
        _keep("local_claim.status", SHORT_WORD),
        _keep("clock.source", SHORT_WORD),
        _keep("bddEmpty", BOOL),
        _keep("pltRegistered", BOOL),
        _keep("updateAvailable", BOOL),
        _keep("passwordEmpty", BOOL),
        _keep("clock.summerOffset", SHORT_WORD),
        *_protocols("protocols[]"),
        _keep("mainReference", VERSION),
        _keep("keyReference", VERSION),
        _keep("zigbeeReference", VERSION),
        _keep("bootReference", VERSION),
        _keep("javaVersion", VERSION),
        _keep("bootVersion", VERSION),
        _keep("oryxVersion", VERSION),
        _keep("*VersionSW", VERSION),
        _keep("*VersionHW", VERSION),
        _keep("*VersionStack", VERSION),
        # file counters such as config.json or TYDOM.dat
        _keep(r"*\.json", INT),
        _keep(r"*\.dat", INT),
        _id("weather.*.devId"),
        _id("weather.*.epId"),
        _redact("mac"),
        _redact("mainId"),
        _redact("urlMediation"),
        _redact("maintenance.id"),
        _redact("geoloc.*"),
        _redact("clock.clock"),
        _redact("clock.timezone"),
        _redact("local_claim.lastAccess"),
        _redact("moments.suspend.to"),
    ),
    opaque=("geoloc", "weather"),
)

POLICY: Final[Mapping[str, EndpointPolicy]] = {
    "/configs/file": _CONFIGS_FILE,
    "/devices/meta": EndpointPolicy(
        rules=(*_DEVICE_IDS, *_descriptor("[].endpoints[].metadata[]"))
    ),
    "/devices/cmeta": EndpointPolicy(
        rules=(*_DEVICE_IDS, *_cmeta("[].endpoints[].cmetadata[]"))
    ),
    "/devices/data": EndpointPolicy(
        rules=(*_DEVICE_IDS, *_data("[].endpoints[].data[]"))
    ),
    "/areas/meta": EndpointPolicy(rules=(*_AREA_IDS, *_descriptor("[].metadata[]"))),
    "/areas/cmeta": EndpointPolicy(rules=(*_AREA_IDS, *_cmeta("[].cmetadata[]"))),
    "/areas/data": EndpointPolicy(rules=(*_AREA_IDS, *_data("[].data[]"))),
    "/groups/file": EndpointPolicy(
        rules=(
            _id("groups[].id"),
            # assumed
            _id("groups[].devices[].id"),
            _id("groups[].devices[].endpoints[].id"),
            _id("groups[].areas[].id"),
        )
    ),
    "/scenarios/file": EndpointPolicy(
        rules=(
            _id("scn[].id"),
            _id("scn[].grpAct[].id"),
            _keep("scn[].grpAct[].state[].name", WORD),
            _keep("scn[].grpAct[].state[].value", ENUM_VALUE),
        )
    ),
    "/moments/file": EndpointPolicy(
        rules=(
            _id("prog[].id"),
            _id("event[].progId"),
            # assumed
            _id("rdv[].id"),
            _id("mom[].id"),
        ),
        opaque=("prog[]", "rdv[]", "mom[]", "event[]"),
    ),
    "/protocols": EndpointPolicy(rules=_protocols("[]")),
    "/info": _INFO,
}

# data url -> (meta url holding its descriptors, list keys between the id levels)
DESCRIPTOR_SOURCES: Final[Mapping[str, tuple[str, tuple[str, ...]]]] = {
    "/devices/data": ("/devices/meta", ("endpoints",)),
    "/areas/data": ("/areas/meta", ()),
}
