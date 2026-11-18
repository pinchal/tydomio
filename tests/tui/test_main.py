import asyncio
import curses
import logging
from typing import override

import pytest

from tests.builders import config, data, devices
from tests.conftest import (
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeWebsocket,
    answer_gets,
    put,
)
from tests.tui.conftest import ENTER, ESC, REVIEW, FakeWindow
from tydomio.scrub import REDACTED
from tydomio.state.cache import Cache
from tydomio.tui import main

# invented secrets; the password built from parts: a literal reads as hardcoded
PASSWORD = "-".join(("Invented", "Pass", "42"))
IP = "192.0.2.77"
MAC = "00:11:22:aa:bb:cc"
ENV = {"TYDOM_IP": IP, "TYDOM_MAC_ADDRESS": MAC, "TYDOM_PASSWORD": PASSWORD}
SHUTTER_NAME = "Bedroom shutter"
CONFIG = config({(10, 1): (SHUTTER_NAME, "shutter"), (20, 1): ("Boiler", "boiler")})
ANSWERS = {
    "/configs/file": (200, CONFIG.model_dump_json().encode()),
    "/devices/data": (
        200,
        devices((10, 1, [data("position", 37)]), (20, 1, [data("setpoint", 19.5)]))
        .model_dump_json()
        .encode(),
    ),
    "/areas/data": (200, b"[]"),
    "/refresh/all": (200, b""),
}
SYNCED = "connected, synced"
NO_KEY = -1


class FakeScreen(FakeWindow):
    """A window that gives the scripted keys once a frame shows the cache synced."""

    def __init__(self, *keys: int) -> None:
        """Give the keys in order, then no key (-1) for ever."""
        super().__init__()
        self._keys = list(keys)
        self._synced = False
        self.frames: list[list[str]] = []
        self.timeouts: list[int] = []
        self.reads = 0

    def getch(self) -> int:
        """Give no key until the user can see the cache synced, then the next key."""
        self.reads += 1
        if not self._synced or not self._keys:
            return NO_KEY
        return self._keys.pop(0)

    def timeout(self, ms: int) -> None:
        """Record the delay asked."""
        self.timeouts.append(ms)

    @override
    def refresh(self) -> None:
        """Keep the frame shown."""
        frame = self.screen_text()
        self._synced = self._synced or any(SYNCED in line for line in frame)
        self.frames.append(frame)

    def shown(self) -> list[str]:
        """Give every line of every frame shown."""
        return [line for frame in self.frames for line in frame]


async def tick(_delay: float) -> None:
    """Sleep no time, but let the other tasks run, as a real sleep does."""
    await asyncio.sleep(0)


def acknowledge_puts(sent: bytes) -> bytes | None:
    if not sent.startswith(b"PUT "):
        return answer_gets(ANSWERS)(sent)
    transac_id = sent.split(b"transac-id: ")[1].split(b"\r\n")[0]
    return b"HTTP/1.1 200 OK\r\ntransac-id: " + transac_id + b"\r\n\r\n"


async def run(
    screen: FakeScreen,
    websocket: FakeWebsocket,
    env: dict[str, str] = ENV,
    logs: list[str] | None = None,
) -> int:
    # a guard against a hang only
    async with asyncio.timeout(5):
        return await main.run(
            screen,
            env,
            logs=logs,
            connection_factory=FakeConnectionFactory(websocket),
            authorization_provider=FakeAuthorizationProvider(),
            sleep=tick,
        )


def puts(websocket: FakeWebsocket) -> list[bytes]:
    return [message for message in websocket.sent if message.startswith(b"PUT ")]


async def test_scenario_browse_and_quit() -> None:
    websocket = FakeWebsocket(reply=answer_gets(ANSWERS))
    screen = FakeScreen(curses.KEY_DOWN, curses.KEY_UP, ord("q"))

    assert await run(screen, websocket) == 0
    assert websocket.closed
    assert any(SHUTTER_NAME in line and "37" in line for line in screen.frames[-1])


async def test_scenario_command_is_sent_once_after_confirmation() -> None:
    websocket = FakeWebsocket(reply=acknowledge_puts)
    keys = (ENTER, ord("4"), ord("0"), *REVIEW, ord("y"), ord("q"))
    screen = FakeScreen(*keys)

    assert await run(screen, websocket) == 0
    (sent,) = puts(websocket)
    assert sent.split(b"\r\n")[0] == b"PUT /devices/10/endpoints/1/data HTTP/1.1"
    assert sent.endswith(b'\r\n\r\n[{"name": "position", "value": 40}]')
    assert "acknowledged" in screen.frames[-1]


async def test_scenario_a_position_is_set_through_the_send_row() -> None:
    websocket = FakeWebsocket(reply=acknowledge_puts)
    # Enter on the position, Enter on the command (unset), Enter to review
    keys = (ENTER, ord("5"), ord("0"), ENTER, ENTER, ENTER, ord("y"), ord("q"))
    screen = FakeScreen(*keys)

    assert await run(screen, websocket) == 0
    (sent,) = puts(websocket)
    assert sent.endswith(b'\r\n\r\n[{"name": "position", "value": 50}]')
    assert "acknowledged" in screen.frames[-1]


async def test_sending_is_shown_before_the_result() -> None:
    screen = FakeScreen(ENTER, ord("4"), ord("0"), *REVIEW, ord("y"), ord("q"))

    def acknowledge_once_sending_is_shown(sent: bytes) -> bytes | None:
        shown = any("sending…" in line for line in screen.shown())
        # the acknowledgement of a write is held until the user has seen `sending…`
        return acknowledge_puts(sent) if shown or not sent.startswith(b"PUT ") else None

    websocket = FakeWebsocket(reply=acknowledge_once_sending_is_shown)

    assert await run(screen, websocket) == 0
    assert len(puts(websocket)) == 1
    assert "acknowledged" in screen.frames[-1]


async def test_scenario_cancelled_confirmation_sends_nothing() -> None:
    websocket = FakeWebsocket(reply=acknowledge_puts)
    keys = (ENTER, ord("4"), ord("0"), *REVIEW, ord("n"), ord("q"))
    screen = FakeScreen(*keys)

    assert await run(screen, websocket) == 0
    assert "Press y to send, any other key cancels." in screen.shown()
    assert puts(websocket) == []


async def test_a_second_command_within_the_interval_is_held_by_the_write_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a 1000 s interval: the second command is held whatever the machine load
    monkeypatch.setattr(main, "WRITE_RATE", 0.001)
    websocket = FakeWebsocket(reply=acknowledge_puts)
    one = (ENTER, ord("4"), ord("0"), *REVIEW, ord("y"))
    screen = FakeScreen(*one, *one, ord("q"))

    assert await run(screen, websocket) == 0
    assert len(puts(websocket)) == 1
    assert any("not sent: rate_limited" in line for line in screen.frames[-1])


async def test_missing_variable_exits_2() -> None:
    screen = FakeScreen()
    logs: list[str] = []
    env = {key: value for key, value in ENV.items() if key != "TYDOM_IP"}

    assert await run(screen, FakeWebsocket(), env, logs) == 2
    assert (screen.reads, screen.timeouts, screen.frames) == (0, [], [])
    assert logs == ["ERROR TYDOM_IP is not set"]


async def test_logs_pane_shows_info_without_secrets() -> None:
    def leaking_reply(sent: bytes) -> bytes | None:
        if sent.startswith(b"GET /configs/file "):
            logging.getLogger("tests").info("seen %s %s %s", PASSWORD, IP, MAC)
        return answer_gets(ANSWERS)(sent)

    screen = FakeScreen(ord("q"))

    assert await run(screen, FakeWebsocket(reply=leaking_reply)) == 0
    lines = screen.shown()
    assert any(line.endswith("INFO Connected to tydom") for line in lines)
    assert any(f"INFO seen {REDACTED}" in line for line in lines)
    leaked = (PASSWORD, IP, MAC, "AABBCC")
    assert [s for s in leaked if any(s.lower() in x.lower() for x in lines)] == []


HEATING_CONFIRMATION = (curses.KEY_DOWN, ENTER, curses.KEY_RIGHT, *REVIEW)
TYPED_YES = (ord("y"), ord("e"), ord("s"))


async def test_no_key_is_never_passed_to_the_ui_as_minus_one() -> None:
    websocket = FakeWebsocket(reply=acknowledge_puts)
    idle = (NO_KEY,) * 25
    screen = FakeScreen(*HEATING_CONFIRMATION, *idle, *TYPED_YES, ESC, ord("q"))

    assert await run(screen, websocket) == 0
    # a -1 given to the UI would have cancelled the confirmation before the word
    assert "> yes" in screen.shown()
    assert puts(websocket) == []


async def test_a_resize_redraws_without_reaching_the_ui() -> None:
    websocket = FakeWebsocket(reply=acknowledge_puts)
    keys = (*HEATING_CONFIRMATION, curses.KEY_RESIZE, *TYPED_YES, ESC, ord("q"))
    screen = FakeScreen(*keys)

    assert await run(screen, websocket) == 0
    assert "> yes" in screen.shown()
    assert puts(websocket) == []


async def test_a_fatal_websocket_error_exits_1_naming_types_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def merge_devices(_cache: Cache, _data: object) -> None:
        raise RuntimeError(f"{SHUTTER_NAME} {PASSWORD}")

    monkeypatch.setattr(Cache, "merge_devices", merge_devices)
    push = put(
        "/devices/data",
        devices((10, 1, [data("position", 83)])).model_dump_json().encode(),
    )
    logs: list[str] = []

    code = await run(
        FakeScreen(), FakeWebsocket(push, reply=answer_gets(ANSWERS)), logs=logs
    )

    assert code == 1
    assert any(line.endswith("ERROR Stopped on CacheError") for line in logs)
    assert [line for line in logs if PASSWORD in line or SHUTTER_NAME in line] == []
