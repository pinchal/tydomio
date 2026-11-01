"""JSON values and their strict parsing."""

import json
import math
from typing import Final

from ..error import TydomIoError

type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)

MAX_DEPTH: Final = 64


class NotJson(TydomIoError):
    """Content that is not strict JSON; the message names the reason, never content."""


def _reject_constant(_name: str) -> float:
    raise NotJson("Non-finite number")


def _finite_float(text: str) -> float:
    # an overflowing literal such as 1e400 parses to infinity without parse_constant
    value = float(text)
    if not math.isfinite(value):
        raise NotJson("Non-finite number")
    return value


def parse_json(content: bytes) -> JsonValue:
    """Parse UTF-8 JSON, rejecting NaN, Infinity and nesting deeper than MAX_DEPTH.

    Raise NotJson otherwise; the error chain never holds the content.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise NotJson("Content is not UTF-8") from None
    try:
        value: object = json.loads(
            text, parse_constant=_reject_constant, parse_float=_finite_float
        )
    except json.JSONDecodeError:
        raise NotJson("Invalid JSON") from None
    except RecursionError:
        raise NotJson("Nested too deep") from None
    return _checked(value, 0)


def _checked(value: object, depth: int) -> JsonValue:
    """Return the parsed value, typed, its containers at most MAX_DEPTH deep."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if depth >= MAX_DEPTH:
        raise NotJson("Nested too deep")
    if isinstance(value, list):
        return [_checked(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {str(key): _checked(item, depth + 1) for key, item in value.items()}
    raise NotJson("Unexpected value")
