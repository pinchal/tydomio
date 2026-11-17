"""The read side of the cache: typed, sanitized `get_state` output, no I/O."""

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from ..error import InvalidResponse
from ..model.usages import USAGES, Area, FieldMeta, UsageModel
from ..scrub import Secrets
from .cache import Cache, Entity

MAX_STRING: Final = 64
_UP_TO_DATE: Final = "upToDate"
_WHITESPACE: Final = re.compile(r"\s+")


def utc_text(moment: datetime) -> str:
    """Format a moment as ISO 8601 UTC with a Z, to the second."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize(text: str, secrets: Secrets) -> str:
    """Make a Tydom string safe to output: no control or format characters, no secret.

    The characters are removed before the scrub, so a secret cannot hide behind
    them, and the cut comes after it, so that it cannot leave part of a secret
    unscrubbed.
    """
    text = "".join(
        c for c in text if c.isspace() or unicodedata.category(c) not in {"Cc", "Cf"}
    )
    text = secrets.scrub(_WHITESPACE.sub(" ", text).strip())
    if len(text) > MAX_STRING:
        text = text[: MAX_STRING - 1] + "…"
    return text


def _value(value: object, secrets: Secrets) -> object:
    return sanitize(value, secrets) if isinstance(value, str) else value


def _finite(value: object) -> bool:
    return not isinstance(value, float) or math.isfinite(value)


def _typed_state(
    model: UsageModel, secrets: Secrets
) -> tuple[dict[str, object], dict[str, object]] | None:
    """Give the values and the validities that are not up to date, by Tydom name.

    None when a value is not finite: it cannot be output as JSON.
    """
    values: dict[str, object] = {}
    validity: dict[str, object] = {}
    dumped = model.model_dump(
        mode="json", by_alias=True, exclude={"unknown"}, exclude_none=True
    )
    for name, reading in dumped.items():
        if not _finite(reading.get("value")):
            return None
        values[name] = _value(reading.get("value"), secrets)
        if reading["validity"] != _UP_TO_DATE:
            validity[name] = sanitize(reading["validity"], secrets)
    aliases = {field.alias for field in type(model).model_fields.values()}
    for name, field in model.unknown.items():
        key = sanitize(name, secrets)
        # a raw name never takes the place of a typed field, present or not
        if key in aliases or key in values:
            continue
        if not _finite(field.value):
            return None
        values[key] = _value(field.value, secrets)
        if field.validity != _UP_TO_DATE:
            validity[key] = sanitize(field.validity, secrets)
    return values, validity


def _item(
    item_id: str,
    *,
    name: str,
    kind: str,
    model_type: type[UsageModel] | None,
    entity: Entity,
    secrets: Secrets,
    field_info: dict[str, dict[str, dict[str, str]]],
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": item_id,
        "name": sanitize(name, secrets),
        "kind": sanitize(kind, secrets),
    }
    if model_type is None:
        return item
    if entity.invalid:
        item["error"] = "invalid_state"
        return item
    try:
        model = model_type.from_data(entity.fields.values())
    except InvalidResponse:
        item["error"] = "invalid_state"
        return item
    typed = _typed_state(model, secrets)
    if typed is None:
        item["error"] = "invalid_state"
        return item
    state, validity = typed
    item["state"] = state
    if validity:
        item["validity"] = validity
    if entity.error != 0:
        item["tydom_error"] = entity.error
    _add_field_info(field_info, item["kind"], model_type.field_meta(), state)
    return item


def _add_field_info(
    field_info: dict[str, dict[str, dict[str, str]]],
    kind: object,
    meta: Mapping[str, FieldMeta],
    state: dict[str, object],
) -> None:
    fields = field_info.setdefault(str(kind), {})
    for name, info in meta.items():
        if name in state:
            fields[name] = {
                key: value
                for key, value in (
                    ("unit", info.unit),
                    ("description", info.description),
                )
                if value is not None
            }


def get_state(
    cache: Cache,
    secrets: Secrets,
    ids: Sequence[str] | None,
    *,
    with_groups: bool,
) -> dict[str, object]:
    """Build the `get_state` output from the cache; atomic, it never awaits."""
    synced_at = cache.synced_at
    result: dict[str, object] = {
        "cache": {
            "connection": cache.connection,
            "synced": cache.synced,
            "synced_at": None if synced_at is None else utc_text(synced_at),
        }
    }
    items: list[dict[str, object]] = []
    field_info: dict[str, dict[str, dict[str, str]]] = {}
    known: set[str] = set()
    wanted = None if ids is None else set(ids)
    config = cache.config
    if config is not None:
        for endpoint in config.endpoints:
            item_id = f"e:{endpoint.id_device}:{endpoint.id_endpoint}"
            known.add(item_id)
            if wanted is None or item_id in wanted:
                entity = cache.endpoints.get(
                    (endpoint.id_device, endpoint.id_endpoint), Entity({})
                )
                items.append(
                    _item(
                        item_id,
                        name=endpoint.name,
                        kind=endpoint.last_usage,
                        model_type=USAGES.get(endpoint.last_usage),
                        entity=entity,
                        secrets=secrets,
                        field_info=field_info,
                    )
                )
        for area in config.areas:
            item_id = f"a:{area.id}"
            known.add(item_id)
            if wanted is None or item_id in wanted:
                items.append(
                    _item(
                        item_id,
                        name=area.name,
                        kind="area",
                        model_type=Area,
                        entity=cache.areas.get(area.id, Entity({})),
                        secrets=secrets,
                        field_info=field_info,
                    )
                )
    result["items"] = items
    field_info = {kind: fields for kind, fields in field_info.items() if fields}
    if field_info:
        result["field_info"] = field_info
    if with_groups and ids is None:
        result["groups"] = [
            {
                "id": f"g:{group.id}",
                "name": sanitize(group.name, secrets),
                "group_all": group.group_all,
            }
            for group in (config.groups if config is not None else [])
            if group.usage == "shutter"
        ]
    result["unknown_ids"] = [i for i in ids or [] if i not in known]
    return result
