"""Draw the terminal UI on a curses-like window; every text is cleaned and cut."""

import curses
import re
import textwrap
from typing import Final, Protocol

from ..state.view import strip_controls
from .logs import LogBuffer
from .model import DetailLine, Row, Ui

MIN_WIDTH: Final = 60
MIN_HEIGHT: Final = 12
TOO_SMALL: Final = "terminal too small"
LOG_LINES: Final = 4
_HINTS: Final = {
    "browse": "↑↓ move · Enter command · q quit",
    "form": "↑↓ move · type or ←→ choose · Enter next · Esc cancel",
    "confirm": "y send · any other key cancels",
    "heating": "type yes then Enter to send · Esc cancels",
}
_TITLES: Final = {"form": "Command", "confirm": "Confirm"}
_WHITESPACE: Final = re.compile(r"\s+")
_NOT_SPACE: Final = re.compile(r"[^\S ]")
_INDENT: Final = "  "

type _Line = tuple[str, int]  # a laid out line and its attribute


class Window(Protocol):
    """The curses window methods used."""

    def getmaxyx(self) -> tuple[int, int]:
        """Give the height and width."""
        ...

    def erase(self) -> None:
        """Clear the window."""
        ...

    def addnstr(self, y: int, x: int, text: str, n: int, attr: int = 0, /) -> None:
        """Write at most `n` characters of `text` at the position."""
        ...

    def refresh(self) -> None:
        """Show what was written."""
        ...


def _cell(text: str) -> str:
    """Clean one cell: no control characters, whitespace runs made one space."""
    return _WHITESPACE.sub(" ", strip_controls(text)).strip()


def _put(
    window: Window, y: int, x: int, width: int, text: str, *, attr: int = 0
) -> None:
    """Write one line cut to `width`; controls go, other whitespace becomes a space.

    Runs of spaces stay: they are the padding of the layout, cells are cleaned apart.
    """
    clean = _NOT_SPACE.sub(" ", strip_controls(text))[:width]
    if clean and width > 0:
        window.addnstr(y, x, clean, width, attr)


def _aligned(left: str, right: str, width: int) -> str:
    """Give `left` then `right` ending at `width`; `left` is cut to leave a space."""
    right = right[:width]
    left = left[: max(0, width - len(right) - (1 if right else 0))]
    return left + " " * (width - len(left) - len(right)) + right


def _frame(window: Window, y: int, x: int, size: tuple[int, int], title: str) -> None:
    """Draw a rounded box of `size` (height, width), its title in the top border."""
    height, width = size
    title = _cell(title)[: width - 4]
    head = f"╭ {title} " if title else "╭"
    _put(window, y, x, width, head + "─" * (width - 1 - len(head)) + "╮")
    for row in range(y + 1, y + height - 1):
        _put(window, row, x, 1, "│")
        _put(window, row, x + width - 1, 1, "│")
    _put(window, y + height - 1, x, width, "╰" + "─" * (width - 2) + "╯")


def _rows(
    window: Window, rows: list[Row], selected: str, height: int, width: int
) -> None:
    """Draw the rows inside the left box, scrolled so that the selected one shows.

    A row has a margin of a space each side, highlighted with it when selected.
    """
    index = next((i for i, row in enumerate(rows) if row.id == selected), 0)
    start = max(0, index - height + 1)
    for y, row in enumerate(rows[start : start + height], start=1):
        if not row.id:
            header = f" {_cell(row.label).upper()}"
            _put(window, y, 1, width, header, attr=curses.A_BOLD)
            continue
        text = _aligned(_INDENT + _cell(row.label), _cell(row.value), width - 2)
        attr = curses.A_REVERSE if selected and row.id == selected else 0
        _put(window, y, 1, width, f" {text} ", attr=attr)


def _detail_lines(lines: list[DetailLine], width: int) -> list[_Line]:
    """Lay the detail out: values at the right edge, notes indented, dim, wrapped."""
    laid: list[_Line] = []
    for line in lines:
        if line.value:
            laid.append((_aligned(_cell(line.text), _cell(line.value), width), 0))
            continue
        indent = _INDENT if line.dim else ""
        attr = curses.A_DIM if line.dim else 0
        parts = textwrap.wrap(_cell(line.text), width - len(indent)) or [""]
        laid.extend((indent + part, attr) for part in parts)
    return laid


def _form_lines(ui: Ui, width: int, height: int) -> list[_Line]:
    """Give the form rows, cut by `_put`, not wrapped; if too many, scroll.

    The target and the last line (send row or error, wrapped) stay; the rows between
    scroll so that the focused one shows. The height is at least 8.
    """
    target, *middle, last = ui.form_lines()
    tail = textwrap.wrap(last, width)
    size = height - 1 - len(tail)
    if len(middle) > size:
        focus = min(ui.form_focus - 1, len(middle) - 1)
        start = min(max(0, focus - size + 1), len(middle) - size)
        middle = middle[start : start + size]
    return [(line, 0) for line in (target, *middle, *tail)]


def _command_lines(ui: Ui, width: int, height: int) -> list[_Line]:
    """Wrap the confirmation; if too many, keep the end (prompt, buffer).

    It also keeps its first line, the target, whatever the values.
    """
    lines = ui.confirm_lines()
    wrapped = [part for line in lines for part in textwrap.wrap(line, width) or [""]]
    if len(wrapped) > height:
        # the target first, then what fits of the end; height is at least 8
        wrapped = wrapped[:1] + wrapped[1 - height :]
    return [(line, 0) for line in wrapped[-height:]]


def _right(ui: Ui, width: int, height: int) -> tuple[str, list[_Line]]:
    """Give the title and the lines of the right box."""
    if ui.mode == "form":
        return f"{_TITLES['form']} · {ui.form_name}", _form_lines(ui, width, height)
    if ui.mode != "browse":
        return _TITLES[ui.mode], _command_lines(ui, width, height)
    title, *lines = ui.detail() or [DetailLine("")]
    return title.text, _detail_lines(lines, width)[:height]


def _last_line(ui: Ui, width: int) -> str:
    """Give the hint at the left and the connection at the right, or both cut."""
    hint = _HINTS["heating" if ui.heating_confirmation else ui.mode]
    connection = _cell(ui.connection_line())
    if len(hint) + 2 + len(connection) <= width:
        return _aligned(hint, connection, width)
    return f"{hint} · {connection}"[:width].ljust(width)


def _bottom(window: Window, ui: Ui, logs: LogBuffer, log_height: int) -> None:
    """Draw the last log lines dimmed, the status, then the hint and connection."""
    height, width = window.getmaxyx()
    top = height - 2 - log_height
    shown = logs.lines()[-log_height:] if log_height else []
    for y, line in enumerate(shown, start=top):
        _put(window, y, 1, width - 1, _cell(line), attr=curses.A_DIM)
    _put(window, height - 2, 0, width, _cell(ui.status))
    # cut one short of the width: curses fails on the last cell
    last = _last_line(ui, width - 1)
    _put(window, height - 1, 0, width - 1, last, attr=curses.A_REVERSE)


def draw(window: Window, ui: Ui, logs: LogBuffer) -> None:
    """Draw the framed list and detail (or form, or confirmation), logs and hints."""
    rows = ui.rows()  # every call: it closes a form voided by a lost connection
    height, width = window.getmaxyx()
    window.erase()
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        _put(window, 0, 0, width - 1, TOO_SMALL)  # curses fails on the last cell
        window.refresh()
        return
    # the form takes the full width: the list is not needed while editing
    left = 0 if ui.mode == "form" else max(24, width * 40 // 100)
    log_height = min(LOG_LINES, height - 10) if ui.mode == "browse" else 0
    box = height - 2 - log_height  # the status and hint lines are below the logs
    # the right pane has a margin of a space each side
    title, lines = _right(ui, width - left - 4, box - 2)
    # the frames first, so that the lines of a pane are drawn one after the other
    if left:
        _frame(window, 0, 0, (box, left), "Home")
    _frame(window, 0, left, (box, width - left), title)
    if left:
        _rows(window, rows, ui.selected, box - 2, left - 2)
    for y, (line, attr) in enumerate(lines, start=1):
        _put(window, y, left + 2, width - left - 4, line, attr=attr)
    _bottom(window, ui, logs, log_height)
    window.refresh()
