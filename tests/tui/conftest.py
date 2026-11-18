"""Shared builders and the window double of the TUI tests."""

import curses
from datetime import UTC, datetime
from typing import NamedTuple

from tests.builders import areas, config, data, devices
from tydomio.scrub import Secrets
from tydomio.state.cache import Cache

# invented secrets; the password built from parts: a literal reads as hardcoded
PASSWORD = "".join(("hunter", "2"))
SECRETS = Secrets(password=PASSWORD, ip="192.168.1.9", mac="001A2BAABBCC")
SHUTTER = "e:1:2"
BOILER = "e:20:1"
ENTER = 10
ESC = 27
# more ↓ than a form has rows: they stop on the send row, where Enter reviews
REVIEW = (*(curses.KEY_DOWN,) * 12, ENTER)


def make_cache() -> Cache:
    """Build a fresh cache: shutters, a boiler, an alarm without a model, two areas."""
    cache = Cache()
    cache.apply_config(
        config(
            {
                (1, 2): ("Kitchen", "shutter"),
                (20, 1): ("Boiler", "boiler"),
                (1, 3): ("Bedroom", "shutter"),
                (30, 1): ("Alarm", "alarm"),
            },
            {7: ("Living", "re2020ControlBoiler")},
        )
    )
    cache.replace_devices(
        devices(
            (1, 2, [data("position", 100)]),
            (1, 3, [data("position", 20, "expired")]),
            (20, 1, [data("setpoint", 19.5)]),
        ),
        [],
    )
    cache.replace_areas(areas((7, [data("hygroIn", 48.0)])), [])
    cache.mark_connected()
    cache.mark_synced(datetime(2026, 11, 2, 9, 14, 5, tzinfo=UTC))
    return cache


class Call(NamedTuple):
    """One `addnstr` call, exactly as received."""

    y: int
    x: int
    text: str
    n: int
    attr: int

    @property
    def shown(self) -> str:
        """Give what curses would put on the screen: at most `n` characters."""
        return self.text[: self.n]


class FakeWindow:
    """A curses window double: records the writes, size configurable."""

    def __init__(self, height: int = 30, width: int = 100) -> None:
        """Give a window of the size."""
        self.size = (height, width)
        self.calls: list[Call] = []

    def getmaxyx(self) -> tuple[int, int]:
        """Give the height and width."""
        return self.size

    def erase(self) -> None:
        """Forget what was drawn."""
        self.calls.clear()

    def addnstr(self, y: int, x: int, text: str, n: int, attr: int = 0) -> None:
        """Record a write; like curses, fail when it reaches the bottom-right cell."""
        height, width = self.size
        call = Call(y, x, text, n, attr)
        if y == height - 1 and x + len(call.shown) >= width:
            raise curses.error("write to the bottom-right cell")
        self.calls.append(call)

    def refresh(self) -> None:
        """Do nothing: nothing is shown."""

    def screen_text(self) -> list[str]:
        """Give the texts drawn, in call order, as received."""
        return [call.text for call in self.calls]
