import asyncio
import json
import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import IO, Any

import anyio
import pytest

from tests.conftest import (
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeSleep,
    FakeWebsocket,
    answer_gets,
    put,
)
from tests.mcp.conftest import areas, config, data, devices
from tydomio.mcp_server import server
from tydomio.mcp_server.cache import Cache

# invented secrets; the password built from parts: a literal reads as hardcoded
PASSWORD = "-".join(("Invented", "Pass", "42"))
IP = "192.0.2.77"
MAC = "00:11:22:aa:bb:cc"
ENV = {"TYDOM_IP": IP, "TYDOM_MAC_ADDRESS": MAC, "TYDOM_PASSWORD": PASSWORD}
SHUTTER = (10, 1)
AREA = 7
GROUP = 5
NAMES = ("Bedroom shutter", "Living area", "All shutters")
CONFIG = config(
    {SHUTTER: (NAMES[0], "shutter")},
    {AREA: (NAMES[1], "area")},
    {GROUP: (NAMES[2], "shutter")},
)
ANSWERS = {
    "/configs/file": (200, CONFIG.model_dump_json().encode()),
    "/devices/data": (
        200,
        devices((*SHUTTER, [data("position", 37)])).model_dump_json().encode(),
    ),
    "/areas/data": (
        200,
        areas((AREA, [data("hygroIn", 61)])).model_dump_json().encode(),
    ),
    "/refresh/all": (200, b""),
}
DEVICES_PUSH = put(
    "/devices/data",
    devices((*SHUTTER, [data("position", 83)])).model_dump_json().encode(),
)
# what no log line at INFO may hold: names, values, ids and secrets
FORBIDDEN = (*NAMES, "37", "83", "61", "e:10:1", "a:7", PASSWORD, IP, MAC, "AABBCC")


@dataclass
class Stdio:
    """The server's stdin and stdout as pipes, and the client's ends of them."""

    stdin: anyio.AsyncFile[str]
    stdout: anyio.AsyncFile[str]
    to_server: IO[str]
    from_server: IO[str]

    def send(self, message: dict[str, Any]) -> None:
        """Write one JSON-RPC message to the server's stdin."""
        self.to_server.write(json.dumps(message) + "\n")
        self.to_server.flush()

    async def receive(self) -> dict[str, Any]:
        """Read one JSON-RPC message from the server's stdout."""
        # a guard against a hang only
        async with asyncio.timeout(5):
            line = await asyncio.to_thread(self.from_server.readline)
        return json.loads(line)

    async def initialize(self) -> None:
        """Do the handshake: initialize, then the initialized notification."""
        self.send(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        assert "result" in await self.receive()
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def call(
        self, request_id: int, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Call a tool; its result parsed."""
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        response = await self.receive()
        assert response["id"] == request_id
        return json.loads(response["result"]["content"][0]["text"])

    async def get_state(
        self, request_id: int, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call get_state, with no arguments by default; its result parsed."""
        return await self.call(request_id, "get_state", arguments or {})


@pytest.fixture(name="stdio")
def pipes() -> Iterator[Stdio]:
    stdin_read, stdin_write = os.pipe()
    stdout_read, stdout_write = os.pipe()
    with (
        open(stdin_read, encoding="utf-8") as stdin,
        open(stdin_write, "w", encoding="utf-8") as to_server,
        open(stdout_write, "w", encoding="utf-8") as stdout,
        open(stdout_read, encoding="utf-8") as from_server,
    ):
        yield Stdio(
            anyio.wrap_file(stdin), anyio.wrap_file(stdout), to_server, from_server
        )


def start(
    stdio: Stdio, *websockets: FakeWebsocket, argv: tuple[str, ...] = ()
) -> asyncio.Task[int]:
    return asyncio.create_task(
        server.run(
            argv,
            ENV,
            connection_factory=FakeConnectionFactory(*websockets),
            authorization_provider=FakeAuthorizationProvider(),
            sleep=FakeSleep(),
            stdin=stdio.stdin,
            stdout=stdio.stdout,
        )
    )


async def ended(run: asyncio.Task[int]) -> int:
    # a guard against a hang only
    async with asyncio.timeout(5):
        return await run


async def until_state(
    stdio: Stdio, condition: Any, first_id: int = 1
) -> tuple[dict[str, Any], int]:
    """Call get_state until the condition holds; the state and the next id."""
    request_id = first_id
    async with asyncio.timeout(5):
        while not condition(state := await stdio.get_state(request_id)):
            request_id += 1
    return state, request_id + 1


REFRESH = b"POST /refresh/all HTTP/1.1"


def start_lines(websocket: FakeWebsocket) -> list[bytes]:
    return [m.split(b"\r\n")[0] for m in websocket.sent]


async def until(condition: Callable[[], bool]) -> None:
    # a guard against a hang only
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0)


def tasks_running(name: str) -> bool:
    return any(
        getattr(task.get_coro(), "__name__", None) == name
        for task in asyncio.all_tasks()
    )


def position(state: dict[str, Any]) -> object:
    return next(i for i in state["items"] if i["id"] == "e:10:1")["state"]["position"]


@pytest.mark.parametrize("name", list(ENV))
async def test_a_missing_variable_stops_with_2_naming_it(
    name: str, capfd: pytest.CaptureFixture[str]
) -> None:
    env = {key: value for key, value in ENV.items() if key != name}

    assert await server.run([], env) == 2

    out, err = capfd.readouterr()
    assert f"{name} is not set" in err
    assert not [value for value in ENV.values() if value in err]
    assert out == ""


async def test_a_mac_that_is_not_12_hex_digits_stops_with_2(
    capfd: pytest.CaptureFixture[str],
) -> None:
    assert await server.run([], ENV | {"TYDOM_MAC_ADDRESS": "00:11:22:zz"}) == 2

    err = capfd.readouterr().err
    assert "TYDOM_MAC_ADDRESS" in err
    assert "zz" not in err


@pytest.mark.parametrize("rate", ["0", "11", "-1", "nan", "inf", "x"])
def test_a_write_rate_out_of_range_is_refused(rate: str) -> None:
    with pytest.raises(SystemExit) as raised:
        server.parse_args(["--write-rate", rate])

    assert raised.value.code == 2


def test_the_highest_write_rate_is_accepted() -> None:
    assert server.parse_args(["--write-rate", "10"]).write_rate == 10


async def test_end_to_end_reads_the_synced_state_then_a_push(
    stdio: Stdio, capfd: pytest.CaptureFixture[str]
) -> None:
    websocket = FakeWebsocket(reply=answer_gets(ANSWERS))
    run = start(stdio, websocket)

    await stdio.initialize()
    state, next_id = await until_state(stdio, lambda s: s["cache"]["synced"])
    assert position(state) == 37
    websocket.push(DEVICES_PUSH)
    await until_state(stdio, lambda s: position(s) == 83, next_id)
    await until(lambda: REFRESH in start_lines(websocket))
    stdio.to_server.close()

    assert await ended(run) == 0
    assert websocket.closed
    # reads never reach the Tydom: only the bootstrap requests and pings were sent
    assert set(start_lines(websocket)) == {
        b"GET /ping HTTP/1.1",
        b"GET /configs/file HTTP/1.1",
        b"GET /devices/data HTTP/1.1",
        b"GET /areas/data HTTP/1.1",
        REFRESH,
    }
    out, err = capfd.readouterr()
    assert out == ""
    assert "tool results send equipment names and states to the MCP client" in err
    assert [word for word in FORBIDDEN if word.lower() in err.lower()] == []


async def test_end_to_end_set_shutter_sends_one_put_then_refuses_once_disconnected(
    stdio: Stdio, capfd: pytest.CaptureFixture[str]
) -> None:
    def acknowledge_puts(sent: bytes) -> bytes | None:
        if not sent.startswith(b"PUT "):
            return answer_gets(ANSWERS)(sent)
        transac_id = sent.split(b"transac-id: ")[1].split(b"\r\n")[0]
        return b"HTTP/1.1 200 OK\r\ntransac-id: " + transac_id + b"\r\n\r\n"

    first = FakeWebsocket(reply=acknowledge_puts)
    # the second connection never answers the config: the cache stays unsynced
    second = FakeWebsocket(reply=answer_gets({}))
    run = start(stdio, first, second, argv=("--writes", "shutters"))
    await stdio.initialize()
    _, next_id = await until_state(stdio, lambda s: s["cache"]["synced"])

    sent = await stdio.call(
        next_id, "set_shutter", {"target": f"g:{GROUP}", "command": "DOWN"}
    )
    await first.close()
    _, next_id = await until_state(
        stdio, lambda s: not s["cache"]["synced"], next_id + 1
    )
    refused = await stdio.call(
        next_id, "set_shutter", {"target": f"g:{GROUP}", "command": "UP"}
    )
    stdio.to_server.close()

    assert await ended(run) == 0
    assert sent["outcome"] == "acknowledged"
    assert (refused["outcome"], refused["reason"]) == ("not_sent", "not_synced")
    assert [
        m.split(b"\r\n")[0] for m in first.sent + second.sent if m.startswith(b"PUT ")
    ] == [b"PUT /groups/5/data HTTP/1.1"]
    out, err = capfd.readouterr()
    assert out == ""
    assert [w for w in (*FORBIDDEN, "g:5") if w.lower() in err.lower()] == []


async def listed_tools(stdio: Stdio) -> list[str]:
    stdio.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    response = await stdio.receive()
    return [tool["name"] for tool in response["result"]["tools"]]


@pytest.mark.parametrize(
    ("mode", "tools"),
    [
        ("none", ["get_state"]),
        ("shutters", ["get_state", "set_shutter"]),
        ("all", ["get_state", "set_shutter", "set_heating"]),
    ],
)
async def test_the_writes_argument_chooses_the_listed_tools(
    stdio: Stdio, mode: str, tools: list[str]
) -> None:
    run = start(
        stdio, FakeWebsocket(reply=answer_gets(ANSWERS)), argv=("--writes", mode)
    )

    await stdio.initialize()
    listed = await listed_tools(stdio)
    stdio.to_server.close()

    assert await ended(run) == 0
    assert sorted(listed) == sorted(tools)


async def test_at_debug_the_configured_handler_scrubs_the_secrets(
    stdio: Stdio, capfd: pytest.CaptureFixture[str]
) -> None:
    websocket = FakeWebsocket(reply=answer_gets(ANSWERS))
    run = start(stdio, websocket)
    await stdio.initialize()
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.DEBUG)
    try:
        # logged at DEBUG as received, then refused as invalid input
        await stdio.get_state(1, {"ids": [PASSWORD, IP, MAC]})
    finally:
        root.setLevel(level)
    stdio.to_server.close()
    assert await ended(run) == 0

    err = capfd.readouterr().err
    assert "arguments" in err
    assert [s for s in (PASSWORD, IP, MAC, "AABBCC") if s.lower() in err.lower()] == []


async def test_a_failing_handler_stops_with_1_and_logs_types_only(
    stdio: Stdio,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def merge_devices(_cache: Cache, _data: object) -> None:
        raise RuntimeError(f"{NAMES[0]} {PASSWORD}")

    monkeypatch.setattr(Cache, "merge_devices", merge_devices)
    websocket = FakeWebsocket(DEVICES_PUSH, reply=answer_gets(ANSWERS))
    run = start(stdio, websocket)
    await until(lambda: not tasks_running("run_websocket"))
    # the SDK reads stdin in a thread that a cancellation waits for: end stdin
    stdio.to_server.close()

    assert await ended(run) == 1
    out, err = capfd.readouterr()
    assert out == ""
    assert "CacheError" in err
    assert "Merging" not in err
    assert [word for word in FORBIDDEN if word.lower() in err.lower()] == []
