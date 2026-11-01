"""Anonymization of the captured bodies in one pass, driven by the policy."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final

from .jsonvalue import JsonValue
from .policy import (
    ANY_KEY,
    DESCRIPTOR_SOURCES,
    ENUM_VALUE,
    LIST_ITEM,
    POLICY,
    Action,
    EndpointPolicy,
    Keep,
    Rule,
    parse_path,
    segment_matches,
)

REDACTED: Final = "<redacted>"
NOT_IN_ENUM: Final = "<not-in-enum>"
IDENTIFIER: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_STATUS_OK: Final = 200

NO_RULE: Final = "no-rule"
KEEP_CONSTRAINT: Final = "keep-constraint"


@dataclass(frozen=True)
class UnknownLeaf:
    """A redacted leaf no rule kept, by output path, without its value."""

    url: str
    path: str
    json_type: str
    reason: str


@dataclass(frozen=True)
class RenamedKey:
    """A key renamed to <key-n>, by output path."""

    url: str
    path: str


@dataclass(frozen=True)
class Anonymized:
    """The anonymized bodies and what was redacted or renamed, without values."""

    bodies: dict[str, JsonValue]
    unclassified: tuple[UnknownLeaf, ...]
    renamed_keys: tuple[RenamedKey, ...]


def json_type_name(value: JsonValue) -> str:
    """JSON type of a leaf, as reported."""
    if value is None:
        return "null"
    return type(value).__name__


def placeholder(value: JsonValue) -> JsonValue:
    """Return the redacted value of a leaf, of the same JSON type."""
    match value:
        case bool():
            return False
        case int():
            return 0
        case float():
            return 0.0
        case str():
            return REDACTED
        case _:
            return None


def passes(value: JsonValue, keep: Keep) -> bool:
    """Whether a value matches a keep constraint: type and pattern."""
    types = keep.json_type if isinstance(keep.json_type, tuple) else (keep.json_type,)
    if type(value) not in types:
        return False
    return keep.pattern is None or (
        isinstance(value, str) and re.fullmatch(keep.pattern, value) is not None
    )


def _generic(parts: tuple[str, ...]) -> str:
    text = ""
    for part in parts:
        text += part if part == LIST_ITEM else f".{part}"
    return text.removeprefix(".")


@dataclass(frozen=True)
class _Position:
    """Where the walk is: active rules with their next segment, opaque state."""

    rules: tuple[tuple[Rule, int], ...]
    opaque_paths: tuple[tuple[tuple[str, ...], int], ...]
    opaque: bool = False  # whether the keys of this node are all renamed
    generic: tuple[str, ...] = ()
    dicts: tuple[dict[str, JsonValue], ...] = ()

    def child(self, segment: str, key: str, out_key: str) -> _Position:
        """Position of the child reached through a key or a list item."""

        def follows(segments: tuple[str, ...], position: int) -> bool:
            if position >= len(segments):
                return False
            if segment == LIST_ITEM:
                return segments[position] == LIST_ITEM
            return segments[position] != LIST_ITEM and segment_matches(
                segments[position], key
            )

        opaque_paths = tuple(
            (segments, position + 1)
            for segments, position in self.opaque_paths
            if follows(segments, position)
        )
        return replace(
            self,
            rules=tuple(
                (rule, position + 1)
                for rule, position in self.rules
                if follows(parse_path(rule.path), position)
            ),
            opaque_paths=opaque_paths,
            opaque=self.opaque
            or any(position == len(segments) for segments, position in opaque_paths),
            generic=(*self.generic, out_key),
        )

    def complete_rule(self) -> Rule | None:
        """Return the first rule whose path ends here, if any."""
        for rule, position in self.rules:
            if position == len(parse_path(rule.path)):
                return rule
        return None


# descriptor key (ids of the levels, then name) -> the raw descriptor
type _Descriptors = dict[tuple[str, ...], dict[str, JsonValue]]


def _index_descriptors(meta: JsonValue, levels: tuple[str, ...]) -> _Descriptors:
    """Index the metadata descriptors of a meta body by raw ids and name."""
    index: _Descriptors = {}

    def visit(
        items: JsonValue, prefix: tuple[str, ...], remaining: tuple[str, ...]
    ) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            key = (*prefix, str(item.get("id")))
            if remaining:
                visit(item.get(remaining[0]), key, remaining[1:])
                continue
            descriptors = item.get("metadata")
            for descriptor in descriptors if isinstance(descriptors, list) else []:
                if isinstance(descriptor, dict) and isinstance(
                    name := descriptor.get("name"), str
                ):
                    index.setdefault((*key, name), descriptor)

    visit(meta, (), levels)
    return index


class _Anonymizer:
    """State of one capture: id, name and key maps and the report."""

    def __init__(self, descriptors: Mapping[str, _Descriptors]) -> None:
        self._descriptors = descriptors
        self._ids: dict[str, int] = {}
        self._keys: dict[str, str] = {}
        self._names: dict[str, dict[str, int]] = {}
        self._unknown: dict[UnknownLeaf, None] = {}
        self._renamed: dict[RenamedKey, None] = {}
        self._url = ""

    def endpoint(self, url: str, body: JsonValue, policy: EndpointPolicy) -> JsonValue:
        """Anonymize the body of an endpoint with its policy."""
        self._url = url
        position = _Position(
            rules=tuple((rule, 0) for rule in policy.rules),
            opaque_paths=tuple((parse_path(path), 0) for path in policy.opaque),
        )
        return self._node(body, position)

    def result(self, bodies: dict[str, JsonValue]) -> Anonymized:
        """Return the anonymized capture and its report."""
        return Anonymized(
            bodies=bodies,
            unclassified=tuple(self._unknown),
            renamed_keys=tuple(self._renamed),
        )

    def _node(self, node: JsonValue, position: _Position) -> JsonValue:
        if isinstance(node, list):
            return [
                self._node(item, position.child(LIST_ITEM, "", LIST_ITEM))
                for item in node
            ]
        if not isinstance(node, dict):
            return self._leaf(node, position)
        position = replace(position, dicts=(*position.dicts, node))
        anonymized: dict[str, JsonValue] = {}
        for key, value in node.items():
            out_key = self._key(key, position)
            anonymized[out_key] = self._node(value, position.child(key, key, out_key))
        return anonymized

    def _key(self, key: str, position: _Position) -> str:
        """Return the output key: kept if declared or an identifier, else renamed."""
        # a key named by a rule, or matched by a "*suffix" segment, is declared
        declared = any(
            index < len(segments := parse_path(rule.path))
            and segments[index] not in (ANY_KEY, LIST_ITEM)
            and segment_matches(segments[index], key)
            for rule, index in position.rules
        )
        if declared or (not position.opaque and IDENTIFIER.fullmatch(key)):
            return key
        renamed = self._keys.setdefault(key, f"<key-{len(self._keys) + 1}>")
        self._renamed[RenamedKey(self._url, _generic((*position.generic, renamed)))] = (
            None
        )
        return renamed

    def _leaf(self, value: JsonValue, position: _Position) -> JsonValue:
        rule = position.complete_rule()
        if rule is None:
            return self._unknown_leaf(value, position, NO_RULE)
        match rule.action:
            case Action.KEEP:
                kept = rule.keep is not None and passes(value, rule.keep)
                return (
                    value
                    if kept
                    else self._unknown_leaf(value, position, KEEP_CONSTRAINT)
                )
            case Action.ID:
                return self._id(value, position)
            case Action.NAME:
                return self._name(rule, value, position)
            case Action.VALUE:
                return self._value(value, position)
            case _:
                return placeholder(value)

    def _unknown_leaf(
        self, value: JsonValue, position: _Position, reason: str
    ) -> JsonValue:
        """Redact a leaf, keeping its type, and report it without its value."""
        leaf = UnknownLeaf(
            self._url, _generic(position.generic), json_type_name(value), reason
        )
        self._unknown[leaf] = None
        return placeholder(value)

    def _id(self, value: JsonValue, position: _Position) -> JsonValue:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | str):
            return self._unknown_leaf(value, position, KEEP_CONSTRAINT)
        number = self._ids.setdefault(str(value), len(self._ids) + 1)
        return number if isinstance(value, int) else str(number)

    def _name(self, rule: Rule, value: JsonValue, position: _Position) -> JsonValue:
        if value is None:
            return None
        if not isinstance(value, str):
            return self._unknown_leaf(value, position, KEEP_CONSTRAINT)
        kind = rule.kind or "name"
        names = self._names.setdefault(kind, {})
        number = names.setdefault(value, len(names) + 1)
        return f"<name:{kind}:{number}>"

    def _value(self, value: JsonValue, position: _Position) -> JsonValue:
        """Anonymize a data value: bool, null and enum strings kept.

        A number becomes the min of its descriptor, of the received type, else 0.
        """
        if value is None or isinstance(value, bool):
            return value
        *levels, item = position.dicts or ({},)
        key = (*(str(level.get("id")) for level in levels), str(item.get("name")))
        descriptor = self._descriptors.get(self._url, {}).get(key, {})
        if isinstance(value, int | float):
            minimum = descriptor.get("min")
            if isinstance(minimum, bool) or not isinstance(minimum, int | float):
                return placeholder(value)
            return float(minimum) if isinstance(value, float) else int(minimum)
        if not isinstance(value, str):
            return placeholder(value)
        enum_values = descriptor.get("enum_values")
        if not isinstance(enum_values, list):
            return REDACTED
        listed = value in enum_values and passes(value, ENUM_VALUE)
        return value if listed else NOT_IN_ENUM


def anonymize(
    bodies: Mapping[str, JsonValue], statuses: Mapping[str, int]
) -> Anonymized:
    """Anonymize the bodies of the ok and http-error endpoints in one pass.

    An http-error body is handled with an empty rule set. Endpoints are processed in
    the policy order, so ids and renamed keys are numbered deterministically.
    """
    descriptors: dict[str, _Descriptors] = {}
    for data_url, (meta_url, levels) in DESCRIPTOR_SOURCES.items():
        if statuses.get(meta_url) == _STATUS_OK and meta_url in bodies:
            descriptors[data_url] = _index_descriptors(bodies[meta_url], levels)
    anonymizer = _Anonymizer(descriptors)
    order = [url for url in POLICY if url in bodies]
    order += [url for url in bodies if url not in POLICY]
    anonymized: dict[str, JsonValue] = {}
    for url in order:
        policy = POLICY.get(url)
        if policy is None or statuses.get(url) != _STATUS_OK:
            policy = EndpointPolicy(rules=())
        anonymized[url] = anonymizer.endpoint(url, bodies[url], policy)
    return anonymizer.result(anonymized)
