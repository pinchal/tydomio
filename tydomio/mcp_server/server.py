"""tydomio-mcp: the MCP server of a Tydom, over stdio, with its cache kept in sync."""

import argparse
import asyncio
import functools
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from ..client import AsyncTydomClient, AuthorizationProvider, ConnectionFactory
from ..requests import TydomPutAreasDataRequest, TydomPutDevicesDataRequest
from ..scrub import SecretFilter, Secrets, bare_mac
from ..stderr_logging import log_to_stderr
from .cache import Cache
from .sync import Sync
from .tools import build_server
from .writes import Writes

_logger = logging.getLogger(__name__)

EXIT_STOPPED: Final = 0
EXIT_FATAL: Final = 1
EXIT_CONFIGURATION: Final = 2
MAX_WRITE_RATE: Final = 10.0


def _write_rate(text: str) -> float:
    try:
        rate = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number") from None
    # also false for NaN
    if not 0 < rate <= MAX_WRITE_RATE:
        raise argparse.ArgumentTypeError(f"not in (0, {MAX_WRITE_RATE:g}]")
    return rate


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse the arguments; argparse exits with 2 on an invalid one."""
    parser = argparse.ArgumentParser(
        prog="tydomio-mcp",
        description="MCP server of a Tydom over stdio. TYDOM_IP, TYDOM_MAC_ADDRESS "
        "and TYDOM_PASSWORD come from the environment.",
    )
    parser.add_argument(
        "--writes",
        choices=("none", "shutters", "all"),
        default="none",
        help="the write tools to register",
    )
    parser.add_argument(
        "--write-rate",
        type=_write_rate,
        default=1.0,
        help=f"writes per second, in (0, {MAX_WRITE_RATE:g}]",
    )
    return parser.parse_args(argv)


def _read_secrets(env: Mapping[str, str]) -> Secrets | None:
    """Return the secrets from the environment; None, logged, if one is missing."""
    values: dict[str, str] = {}
    for name in ("TYDOM_IP", "TYDOM_MAC_ADDRESS", "TYDOM_PASSWORD"):
        if not (value := env.get(name, "")):
            _logger.error("%s is not set", name)
            return None
        values[name] = value
    if (mac := bare_mac(values["TYDOM_MAC_ADDRESS"])) is None:
        _logger.error("TYDOM_MAC_ADDRESS is not 12 hexadecimal digits")
        return None
    return Secrets(password=values["TYDOM_PASSWORD"], ip=values["TYDOM_IP"], mac=mac)


def _leaf_types(group: BaseExceptionGroup) -> Iterator[str]:
    for error in group.exceptions:
        if isinstance(error, BaseExceptionGroup):
            yield from _leaf_types(error)
        else:
            yield type(error).__name__


async def _run_stdio(
    server: Server,
    stdin: anyio.AsyncFile[str] | None,
    stdout: anyio.AsyncFile[str] | None,
) -> None:
    """Serve MCP until stdin ends; None means the process's own stdin or stdout."""
    async with stdio_server(stdin, stdout) as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


async def _serve(
    client: AsyncTydomClient,
    server: Server,
    stdin: anyio.AsyncFile[str] | None,
    stdout: anyio.AsyncFile[str] | None,
) -> None:
    """Run the websocket and the MCP server until stdin ends."""
    # a fatal error cancels the stdio task, but the SDK reads stdin in a thread that
    # the cancellation waits for: the process exits at the next line or at stdin close
    async with asyncio.TaskGroup() as tasks:
        websocket = tasks.create_task(client.run_websocket())
        await tasks.create_task(_run_stdio(server, stdin, stdout))
        # run_websocket disconnects and returns when cancelled
        websocket.cancel()


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    connection_factory: ConnectionFactory | None = None,
    authorization_provider: AuthorizationProvider | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    stdin: anyio.AsyncFile[str] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
) -> int:
    """Serve until stdin ends: 0; 1 on a fatal error; 2 misconfigured."""
    args = parse_args(argv)
    # stderr only: stdout carries the MCP protocol
    handler = log_to_stderr()
    try:
        if (secrets := _read_secrets(env)) is None:
            return EXIT_CONFIGURATION
        handler.addFilter(SecretFilter(secrets))
        _logger.info("tool results send equipment names and states to the MCP client")
        cache = Cache()
        sync = Sync(cache, sleep, functools.partial(datetime.now, UTC))
        client = AsyncTydomClient(
            tydom_password=secrets.password,
            tydom_ip=secrets.ip,
            tydom_mac=secrets.mac,
            on_connection_routines=(sync.routine,),
            authorization_provider=authorization_provider,
            connection_factory=connection_factory,
            sleep=sleep,
        )
        client.subscribe(TydomPutDevicesDataRequest, sync.on_devices_push)
        client.subscribe(TydomPutAreasDataRequest, sync.on_areas_push)
        writes = (
            None
            if args.writes == "none"
            else Writes(
                cache, client.send, args.write_rate, heating=args.writes == "all"
            )
        )
        try:
            await _serve(client, build_server(cache, secrets, writes), stdin, stdout)
        except BaseExceptionGroup as group:
            # the type names only: a message or a traceback may hold a value
            _logger.error("Stopped on %s", ", ".join(_leaf_types(group)))
            return EXIT_FATAL
        return EXIT_STOPPED
    finally:
        logging.getLogger().removeHandler(handler)


def main() -> None:  # pragma: no cover - binds the process's real stdin and stdout
    """Run the server on the process's stdio and environment."""
    sys.exit(asyncio.run(run(sys.argv[1:], os.environ)))
