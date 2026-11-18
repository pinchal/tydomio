"""tydomio-tui: the terminal UI of a Tydom, its cache kept in sync, in one loop."""

import asyncio
import contextlib
import curses
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from logging.handlers import BufferingHandler
from typing import Final, Protocol

from ..client import AuthorizationProvider, ConnectionFactory
from ..error import leaf_type_names
from ..scrub import SecretFilter, Secrets, read_secrets
from ..state.cache import Cache
from ..state.sync import synced_client
from ..state.writer import EVERYTHING, Writer
from ..stderr_logging import set_levels
from .logs import LogBuffer
from .model import Ui
from .render import Window, draw

_logger = logging.getLogger(__name__)

EXIT_QUIT: Final = 0
EXIT_FATAL: Final = 1
EXIT_CONFIGURATION: Final = 2
WRITE_RATE: Final = 1.0  # writes per second, the MCP server's default
TICK: Final = 0.05  # seconds between two reads of the keyboard when idle
IDLE_DRAWS: Final = 10  # idle ticks between two draws, so that pushes show
_NO_KEY: Final = -1

type Sleep = Callable[[float], Awaitable[None]]


class Screen(Window, Protocol):
    """The curses window methods used: those `draw` uses, and the keyboard."""

    def getch(self) -> int:
        """Give the key pressed, -1 if none."""
        ...

    def timeout(self, ms: int, /) -> None:
        """Make `getch` wait at most `ms` milliseconds, 0 for not at all."""
        ...


async def _loop(
    screen: Screen, ui: Ui, logs: LogBuffer, writer: Writer, sleep: Sleep
) -> None:
    """Read the keys and draw until the user quits."""
    screen.timeout(0)
    idle = IDLE_DRAWS  # the first iteration draws
    while not ui.quit:
        key = screen.getch()
        if key == _NO_KEY:
            if idle >= IDLE_DRAWS:
                draw(screen, ui, logs)
                idle = 0
            idle += 1
            await sleep(TICK)
            continue
        # a resize only redraws: any key would cancel a heating confirmation
        submission = None if key == curses.KEY_RESIZE else ui.handle_key(key)
        if submission is not None:
            # the reply may take a while: tell the user the command is on its way
            ui.status = "sending…"
            draw(screen, ui, logs)
            # the Writer logs the outcome with the label; a cancellation propagates
            result = await writer.write(
                submission.target, submission.fields, EVERYTHING, submission.label
            )
            ui.show_result(result)
        draw(screen, ui, logs)
        idle = 0


def _secrets(env: Mapping[str, str], kept: list[str]) -> Secrets | None:
    """Read the secrets; the reason of a refusal goes to `kept`, not the terminal."""
    root = logging.getLogger()
    # read_secrets logs only variable names; without a handler they reach stderr
    held = BufferingHandler(capacity=8)
    root.addHandler(held)
    try:
        secrets = read_secrets(env)
    finally:
        root.removeHandler(held)
    kept.extend(f"{record.levelname} {record.getMessage()}" for record in held.buffer)
    return secrets


async def _show(
    screen: Screen,
    secrets: Secrets,
    logs: LogBuffer,
    *,
    connection_factory: ConnectionFactory | None,
    authorization_provider: AuthorizationProvider | None,
    sleep: Sleep,
) -> int:
    """Run the websocket and the loop until the user quits: 0; 1 on a fatal error."""
    cache = Cache()
    client = synced_client(
        cache,
        secrets,
        connection_factory=connection_factory,
        authorization_provider=authorization_provider,
        sleep=sleep,
    )
    writer = Writer(cache, client.send, WRITE_RATE)
    try:
        # a fatal error of the websocket cancels the loop
        async with asyncio.TaskGroup() as tasks:
            websocket = tasks.create_task(client.run_websocket())
            await _loop(screen, Ui(cache, secrets), logs, writer, sleep)
            # run_websocket disconnects and returns when cancelled
            websocket.cancel()
    except BaseExceptionGroup as group:
        # the type names only: a message or a traceback may hold a value
        _logger.error("Stopped on %s", ", ".join(leaf_type_names(group)))
        return EXIT_FATAL
    return EXIT_QUIT


async def run(
    screen: Screen,
    env: Mapping[str, str],
    *,
    logs: list[str] | None = None,
    connection_factory: ConnectionFactory | None = None,
    authorization_provider: AuthorizationProvider | None = None,
    sleep: Sleep = asyncio.sleep,
) -> int:
    """Show the Tydom until the user quits: 0; 1 on a fatal error; 2 misconfigured.

    Nothing is written to the terminal but the screen: `logs` receives the log lines
    kept, for the caller to show once the terminal is restored.
    """
    kept: list[str] = [] if logs is None else logs
    if (secrets := _secrets(env, kept)) is None:
        return EXIT_CONFIGURATION
    set_levels()
    buffer = LogBuffer(secrets)
    buffer.addFilter(SecretFilter(secrets))
    root = logging.getLogger()
    root.addHandler(buffer)
    try:
        return await _show(
            screen,
            secrets,
            buffer,
            connection_factory=connection_factory,
            authorization_provider=authorization_provider,
            sleep=sleep,
        )
    finally:
        root.removeHandler(buffer)
        kept.extend(buffer.lines())


def _wrapped(  # pragma: no cover - runs on the real curses screen
    stdscr: curses.window, logs: list[str]
) -> int:
    # a terminal that cannot hide the cursor still works
    with contextlib.suppress(curses.error):
        curses.curs_set(0)
    return asyncio.run(run(stdscr, os.environ, logs=logs))


def main() -> None:  # pragma: no cover - binds the real terminal and environment
    """Run the UI on the process's terminal and environment."""
    logs: list[str] = []
    try:
        code = curses.wrapper(_wrapped, logs)
    except KeyboardInterrupt:
        code = EXIT_QUIT
    if code != EXIT_QUIT:
        # the terminal is restored: the reason can be read now
        print("\n".join(logs), file=sys.stderr)
    sys.exit(code)
