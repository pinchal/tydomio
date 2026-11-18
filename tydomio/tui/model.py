"""The terminal UI as a pure state machine: list, detail, form, confirmation; no I/O."""

import curses
from dataclasses import dataclass
from typing import Final, Literal, cast

from pydantic.json_schema import JsonSchemaValue

from ..scrub import Secrets
from ..state.cache import Cache
from ..state.view import get_state, utc_text
from ..state.writer import (
    EVERYTHING,
    HEATING,
    NotSent,
    Sent,
    write_fields,
    write_model,
)

type Mode = Literal["browse", "form", "confirm"]
type _Item = dict[str, object]
type _FieldInfo = dict[str, dict[str, dict[str, str]]]

LABEL: Final = "tui"
WARNING: Final = "Some modes and low setpoints can leave the home unheated."
_ENTER: Final = frozenset({10, 13, curses.KEY_ENTER})
_ESC: Final = 27
_BACKSPACE: Final = frozenset({8, 127, curses.KEY_BACKSPACE})
_MOVES: Final = {curses.KEY_UP: -1, curses.KEY_DOWN: 1}
_CYCLES: Final = {curses.KEY_LEFT: -1, curses.KEY_RIGHT: 1}
_NUMERIC: Final = frozenset(map(ord, "0123456789.-"))
_NUMBERS: Final = {"integer": "an integer", "number": "a number"}
_PRINTABLE: Final = range(32, 127)
_SEND_ROW: Final = "[ Review and send ]"
_NOTHING_TO_SEND: Final = "nothing to send: set a value first"
_UNSET: Final = "unset"
_LEFT: Final = "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}"
_RIGHT: Final = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"


@dataclass(frozen=True)
class Submission:
    """A write the user confirmed: target id, values by Tydom name, log label."""

    target: str
    fields: dict[str, object]
    label: str


@dataclass(frozen=True)
class _Target:
    """The item the form writes; `heating` decided from the config."""

    id: str = ""
    name: str = ""
    heating: bool = False


@dataclass
class _Field:
    """One form field: its schema and the text typed, an enum member or digits."""

    name: str
    schema: JsonSchemaValue
    text: str = ""

    def value(self) -> object:
        """Give the typed value; `ValueError` if it does not parse."""
        if "enum" in self.schema:
            return self.text
        return (int if self.schema["type"] == "integer" else float)(self.text)

    def width(self) -> int:
        """Give the width of its value cell, the widest member for an enum."""
        if "enum" in self.schema:
            return max(map(len, [_UNSET, *map(str, self.schema["enum"])])) + 4
        # a slot for the cursor, so that the width does not change with the focus
        return max(len(self.text) + 1, len(_UNSET)) + 4

    def cell(self, width: int, *, focus: bool) -> str:
        """Give the value as `[ 50_ ]` for a number, an enum in angle quotes; padded."""
        if "enum" in self.schema:
            return f"{_LEFT} {self.text or _UNSET} {_RIGHT}".ljust(width)
        text = self.text + "_" if focus else self.text or _UNSET
        return f"[ {text.ljust(width - 4)} ]"

    def hint(self) -> str:
        """Give how to set the value: the enum keys, or the bounds of a number."""
        if "enum" in self.schema:
            return "←→ to choose"
        if "minimum" in self.schema and "maximum" in self.schema:
            return f"{self.schema['minimum']}..{self.schema['maximum']}"
        return ""


def _representable(schema: JsonSchemaValue) -> bool:
    return "enum" in schema or schema.get("type") in _NUMBERS


@dataclass(frozen=True)
class Row:
    """A row of the list: a header has the id `""` and the usage as its label."""

    id: str
    label: str
    value: str


@dataclass(frozen=True)
class DetailLine:
    """A line of the detail: text, a value drawn at the right, dim for a note."""

    text: str
    value: str = ""
    dim: bool = False


def _with_unit(value: object, unit: str | None) -> str:
    return str(value) if unit is None else f"{value} {unit}"


def _short(item: _Item, info: _FieldInfo) -> str:
    """Give the short value of a row: the first field with a unit, else not a bool."""
    if "error" in item:
        return "invalid_state"
    state = cast(dict[str, object], item.get("state", {}))
    units = info.get(str(item["kind"]), {})
    with_unit = [name for name in state if "unit" in units.get(name, {})]
    others = [name for name, value in state.items() if not isinstance(value, bool)]
    name = next(iter(with_unit or others), None)
    if name is None:
        return ""
    return _with_unit(state[name], units.get(name, {}).get("unit"))


def _rank(value: object, meta: dict[str, str]) -> int:
    """Order the fields: with a unit, then other values, then booleans."""
    if "unit" in meta:
        return 0
    return 2 if isinstance(value, bool) else 1


def _field_lines(item: _Item, info: _FieldInfo) -> list[DetailLine]:
    """Give a line per field, its description dimmed below; false flags counted."""
    state = cast(dict[str, object], item.get("state", {}))
    validity = cast(dict[str, object], item.get("validity", {}))
    metas = info.get(str(item["kind"]), {})
    lines: list[DetailLine] = []
    off, alert = 0, False
    for name, value in sorted(
        state.items(), key=lambda field: _rank(field[1], metas.get(field[0], {}))
    ):
        meta = metas.get(name, {})
        mark = f" ({validity[name]})" if name in validity else ""
        if value is False and not mark:
            off += 1
            continue
        if value is True:
            alert = True
            lines.append(DetailLine(f"⚠ {name}", mark.strip()))
        else:
            lines.append(DetailLine(name, _with_unit(value, meta.get("unit")) + mark))
        if "description" in meta:
            lines.append(DetailLine(meta["description"], dim=True))
    if off:
        flags = f"{off} flag{'s' if off > 1 else ''} off"
        lines.append(DetailLine(flags if alert else f"no alert ({flags})"))
    return lines


class Ui:
    """What the screen shows and how keys change it; confirming gives a Submission."""

    def __init__(self, cache: Cache, secrets: Secrets) -> None:
        """Read the cache through `get_state`, scrubbing with the secrets."""
        self._cache = cache
        self._secrets = secrets
        self.mode: Mode = "browse"
        self.quit = False
        self.status = ""
        self._selected: str | None = None
        self._index = 0
        self._target = _Target()
        self._fields: list[_Field] = []
        self._field = 0
        self._error: str | None = None
        self._values: dict[str, object] = {}
        self._typed = ""

    @property
    def selected(self) -> str:
        """Give the id of the selected item, `""` if there is none."""
        return self._selected or ""

    @property
    def heating_confirmation(self) -> bool:
        """Tell if the confirmation shown is for a heating target (the word `yes`)."""
        return self.mode == "confirm" and self._target.heating

    def rows(self) -> list[Row]:
        """Give a header row per kind, then its items with a short value."""
        items, info = self._read()
        rows: list[Row] = []
        kind = None
        for item in items:
            if item["kind"] != kind:
                kind = str(item["kind"])
                rows.append(Row("", kind, ""))
            rows.append(Row(str(item["id"]), str(item["name"]), _short(item, info)))
        return rows

    def detail(self) -> list[DetailLine]:
        """Give `name · kind` of the selected item, then its fields and errors."""
        items, info = self._read()
        item = next((i for i in items if i["id"] == self._selected), None)
        if item is None:
            return []
        lines = [DetailLine(f"{item['name']} · {item['kind']}")]
        if "error" in item:
            lines.append(DetailLine("invalid_state"))
        lines.extend(_field_lines(item, info))
        if "tydom_error" in item:
            lines.append(DetailLine("tydom_error", str(item["tydom_error"])))
        return lines

    @property
    def form_name(self) -> str:
        """Give the display name of the item the form writes, sanitized."""
        return self._target.name

    @property
    def form_focus(self) -> int:
        """Give the index in `form_lines` of the focused field or send row."""
        return 2 + self._field + (1 if self._field == len(self._fields) else 0)

    def form_lines(self) -> list[str]:
        """Give the target, a row per field in columns, the send row, the error."""
        names = max(len(form_field.name) for form_field in self._fields)
        cells = max(form_field.width() for form_field in self._fields)
        lines = [f"{self._target.name} ({self._target.id})", ""]
        for index, form_field in enumerate(self._fields):
            focus = index == self._field
            cell = form_field.cell(cells, focus=focus)
            hint = form_field.hint() if focus else ""
            row = f"{form_field.name.ljust(names)}  {cell}  {hint}"
            lines.append(f"{'> ' if focus else '  '}{row}".rstrip())
        send = self._field == len(self._fields)
        lines.extend(("", f"{'> ' if send else '  '}{_SEND_ROW}"))
        if self._error is not None:
            lines.append(self._error)
        return lines

    def confirm_lines(self) -> list[str]:
        """Give the target, current and new values, the heating warning, the prompt."""
        items, _ = self._items()
        item = next((i for i in items if i["id"] == self._target.id), {})
        current = cast(dict[str, object], item.get("state", {}))
        lines = [f"Send to {self._target.name} ({self._target.id})"]
        lines.extend(
            f"{name}: {current.get(name, 'unset')} -> {value}"
            for name, value in self._values.items()
        )
        if self._target.heating:
            lines.extend(
                (
                    WARNING,
                    "Type yes then Enter to send, anything else cancels.",
                    f"> {self._typed}",
                )
            )
        else:
            lines.append("Press y to send, any other key cancels.")
        return lines

    def connection_line(self) -> str:
        """Give the connection and the freshness of the cache."""
        synced_at = self._cache.synced_at
        fresh = (
            f"synced {utc_text(synced_at)}"
            if self._cache.synced and synced_at is not None
            else "not synced"
        )
        return f"{self._cache.connection}, {fresh}"

    def show_result(self, result: Sent | NotSent) -> None:
        """Set the status line to the outcome of a write."""
        if isinstance(result, Sent):
            self.status = (
                "acknowledged"
                if result.outcome == "acknowledged"
                else "unknown, check the state before retrying"
            )
            return
        status = f"not sent: {result.reason}"
        if result.detail is not None:
            status += f" ({result.detail})"
        if result.retry_after_s is not None:
            status += f", retry after {result.retry_after_s}s"
        self.status = status

    def handle_key(self, key: int) -> Submission | None:
        """Apply a key; only a confirmation gives the Submission to send."""
        items, _ = self._read()
        if self.mode == "form":
            self._form_key(key)
        elif self.mode == "confirm":
            return self._confirm_key(key)
        else:
            self._browse_key(key, items)
        return None

    def _items(self) -> tuple[list[_Item], _FieldInfo]:
        """Give the items grouped by kind in config order, and the field info."""
        state = get_state(self._cache, self._secrets, None, with_groups=False)
        items = cast(list[_Item], state["items"])
        kinds = list(dict.fromkeys(str(item["kind"]) for item in items))
        grouped = sorted(items, key=lambda item: kinds.index(str(item["kind"])))
        return grouped, cast(_FieldInfo, state.get("field_info", {}))

    def _read(self) -> tuple[list[_Item], _FieldInfo]:
        """Close a form voided by a lost connection, clamp the selection, give items."""
        if self.mode != "browse" and not self._cache.fresh:
            self._close()
        items, info = self._items()
        ids = [str(item["id"]) for item in items]
        if self._selected in ids:
            self._index = ids.index(self._selected)
        elif ids:
            self._index = min(self._index, len(ids) - 1)
            self._selected = ids[self._index]
        else:
            self._index, self._selected = 0, None
        return items, info

    def _browse_key(self, key: int, items: list[_Item]) -> None:
        if key in _MOVES and items:
            self._index = max(0, min(self._index + _MOVES[key], len(items) - 1))
            self._selected = str(items[self._index]["id"])
        elif key in _ENTER:
            self._open(items)
        elif key == ord("q"):
            self.quit = True

    def _open(self, items: list[_Item]) -> None:
        """Open the form on the selection when writable and the cache fresh."""
        config = self._cache.config
        if not self._cache.fresh:
            self.status = "cannot command: not connected and synced"
            return
        if config is None or self._selected is None:
            self.status = "cannot command: nothing selected"
            return
        model = write_model(config, self._selected, EVERYTHING)
        if model is None:
            self.status = "cannot command: this item has no writable field"
            return
        fields = [
            _Field(name, schema)
            for name, schema in write_fields(model).items()
            if _representable(schema)
        ]
        if not fields:
            self.status = "cannot command: no editable field"
            return
        item = next(i for i in items if i["id"] == self._selected)
        # from the raw config: a scrubbed display name could hide a heating usage
        heating = write_model(config, self._selected, HEATING) is not None
        self._target = _Target(self._selected, str(item["name"]), heating)
        self._fields, self._field, self._error = fields, 0, None
        self.status = ""
        self.mode = "form"

    def _form_key(self, key: int) -> None:
        if key == _ESC:
            self._close()
        elif key in _MOVES:
            self._field = max(0, min(self._field + _MOVES[key], len(self._fields)))
        elif key in _ENTER and self._field < len(self._fields):
            self._field += 1
        elif key in _ENTER:
            self._to_confirm()
        elif self._field < len(self._fields):
            self._edit(self._fields[self._field], key)

    @staticmethod
    def _edit(form_field: _Field, key: int) -> None:
        """Cycle an enum field through its members and unset, or edit a number."""
        if "enum" in form_field.schema:
            if key in _CYCLES:
                members = ["", *map(str, form_field.schema["enum"])]
                index = members.index(form_field.text) + _CYCLES[key]
                form_field.text = members[index % len(members)]
        elif key in _BACKSPACE:
            form_field.text = form_field.text[:-1]
        elif key in _NUMERIC:
            form_field.text += chr(key)

    def _to_confirm(self) -> None:
        """Parse the values set; an error stays in the form and nothing advances."""
        values: dict[str, object] = {}
        for form_field in self._fields:
            if not form_field.text:
                continue
            try:
                values[form_field.name] = form_field.value()
            except ValueError:
                kind = _NUMBERS[form_field.schema["type"]]
                self._error = f"{form_field.name}: not {kind}"
                return
        if not values:
            self._error = _NOTHING_TO_SEND
            return
        self._values, self._typed, self._error = values, "", None
        self.mode = "confirm"

    def _confirm_key(self, key: int) -> Submission | None:
        """Confirm with `y`, or the word `yes` then Enter for heating; else cancel."""
        if not self._target.heating:
            return self._submission() if key == ord("y") else self._close()
        if key in _ENTER:
            return self._submission() if self._typed == "yes" else self._close()
        if key in _PRINTABLE:
            self._typed += chr(key)
            return None
        return self._close()

    def _submission(self) -> Submission:
        submission = Submission(self._target.id, dict(self._values), LABEL)
        self._close()
        return submission

    def _close(self) -> None:
        """Leave the form or the confirmation for the list."""
        self.mode = "browse"
        self._fields, self._values, self._typed = [], {}, ""
