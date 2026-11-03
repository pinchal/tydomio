import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp import types as mcp_types

from tests.conftest import (
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeSleep,
    FakeWebsocket,
    ScriptExhaustedError,
)
from tests.mcp.conftest import config, data, devices
from tydomio.client import AsyncTydomClient
from tydomio.error import CommunicationError, InvalidResponse, InvalidWrite
from tydomio.mcp_server.cache import Cache
from tydomio.mcp_server.writes import (
    HEATING_USAGES,
    SET_HEATING,
    SHUTTER_USAGES,
    Writes,
)
from tydomio.model.usages import WRITES
from tydomio.model.usages.area import AreaWrite
from tydomio.model.usages.boiler import BoilerWrite
from tydomio.model.usages.re2020_control_boiler import Re2020ControlBoilerWrite
from tydomio.requests import ClientRequest
from tydomio.response import Ok, Response

SHUTTER = "e:10:1"
SENT_AT = datetime(2026, 11, 3, 8, 9, 10, tzinfo=UTC)
SHUTTER_GROUP = "g:5"
ACKNOWLEDGED_NOTE = (
    "The Tydom accepted the command; the device state is not observed yet. "
    "Call get_state later."
)
UNKNOWN_NOTE = (
    "The write may have been applied. It was not and will not be resent. "
    "Read the state with get_state before any retry."
)


@pytest.fixture(name="cache")
def synced_cache() -> Cache:
    cache = Cache()
    cache.apply_config(
        config(
            {
                (10, 1): ("Shutter", "shutter"),
                (20, 1): ("Boiler", "boiler"),
                (30, 1): ("Thermostat", "re2020ControlBoiler"),
            },
            {7: ("Living", "re2020ControlBoiler"), 8: ("Hall", "hvac")},
            {5: ("All shutters", "shutter"), 6: ("Heating", "boiler")},
        )
    )
    cache.replace_devices(devices((10, 1, [data("position", 100)])), [])
    cache.mark_connected()
    cache.mark_synced(datetime(2026, 11, 2, 9, 14, 5, tzinfo=UTC))
    return cache


class FakeClock:
    """A monotonic clock the test moves."""

    def __init__(self) -> None:
        """Start at an arbitrary time."""
        self.now = 1000.0

    def __call__(self) -> float:
        """Give the time."""
        return self.now


class FakeSend:
    """Records each request; waits for `gate` when given, then acknowledges."""

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        """Record from now on."""
        self.requests: list[ClientRequest[Ok]] = []
        self._gate = gate

    async def __call__(self, request: ClientRequest[Ok]) -> Ok:
        """Record the request, then acknowledge it."""
        self.requests.append(request)
        if self._gate is not None:
            await self._gate.wait()
        else:
            # suspends as a real send does
            await asyncio.sleep(0)
        return Ok(Response(status=200, headers={}, content=None))


def body(result: mcp_types.CallToolResult) -> dict[str, Any]:
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, mcp_types.TextContent)
    return json.loads(block.text)


def not_sent(result: mcp_types.CallToolResult) -> str:
    """Check the refusal sent nothing; give its reason."""
    assert result.is_error
    parsed = body(result)
    assert parsed["outcome"] == "not_sent"
    assert parsed["fields"] == {}
    assert "sent_at" not in parsed
    reason = parsed["reason"]
    assert isinstance(reason, str)
    return reason


def tydom_answer(status: int, content: bytes = b"") -> Callable[[bytes], bytes | None]:
    """Answer a PUT with this status and content, any other request with a 200."""

    def reply(sent: bytes) -> bytes:
        transac_id = sent.split(b"transac-id: ")[1].split(b"\r\n")[0]
        start = f"HTTP/1.1 {status if sent.startswith(b'PUT ') else 200} X\r\n"
        headers = start.encode() + b"transac-id: " + transac_id + b"\r\n"
        if sent.startswith(b"PUT ") and content:
            return headers + b"content-type: application/json\r\n\r\n" + content
        return headers + b"\r\n"

    return reply


@asynccontextmanager
async def connected(websocket: FakeWebsocket) -> AsyncGenerator[AsyncTydomClient]:
    """Connect a client to the fake websocket; stop it on exit."""
    factory = FakeConnectionFactory(websocket)
    client = AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        authorization_provider=FakeAuthorizationProvider(),
        connection_factory=factory,
        sleep=FakeSleep(),
    )
    run = asyncio.create_task(client.run_websocket())
    await factory.connected.wait()
    try:
        yield client
    finally:
        run.cancel()
        # a lost connection ends the run when it tries the next, absent, websocket
        with suppress(asyncio.CancelledError, ScriptExhaustedError):
            await run


def puts(websocket: FakeWebsocket) -> list[bytes]:
    return [m for m in websocket.sent if m.startswith(b"PUT ")]


@pytest.mark.parametrize(
    "arguments",
    [
        {"target": "e:1:2/../x", "command": "UP"},
        {"target": "a:7", "command": "UP"},
        {"target": 5, "command": "UP"},
        {"command": "UP"},
        {"target": SHUTTER, "command": "UP", "extra": "zz-secret"},
    ],
)
async def test_invalid_input_sends_nothing_and_echoes_nothing(
    cache: Cache, arguments: dict[str, object]
) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0).set_shutter(arguments)

    assert result.is_error
    assert body(result) == {"code": "invalid_input", "message": "Invalid arguments"}
    assert send.requests == []


async def test_a_write_while_not_synced_sends_nothing(cache: Cache) -> None:
    cache.mark_disconnected()
    send = FakeSend()

    result = await Writes(cache, send, 1.0).set_shutter(
        {"target": SHUTTER, "command": "UP"}
    )

    assert not_sent(result) == "not_synced"
    assert send.requests == []


@pytest.mark.parametrize("target", ["e:10:2", "e:99:1", "g:99"])
async def test_an_unknown_target_sends_nothing(cache: Cache, target: str) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0).set_shutter(
        {"target": target, "command": "UP"}
    )

    assert not_sent(result) == "unknown_target"
    assert body(result)["target"] == target
    assert send.requests == []


async def test_a_write_after_drift_removed_the_target_sends_nothing(
    cache: Cache,
) -> None:
    cache.apply_config(config({(20, 1): ("Boiler", "boiler")}))
    send = FakeSend()

    result = await Writes(cache, send, 1.0).set_shutter(
        {"target": SHUTTER, "command": "UP"}
    )

    assert not_sent(result) == "unknown_target"
    assert send.requests == []


@pytest.mark.parametrize("target", ["e:20:1", "g:6"])
async def test_a_target_that_is_not_a_shutter_sends_nothing(
    cache: Cache, target: str
) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0).set_shutter(
        {"target": target, "command": "UP"}
    )

    assert not_sent(result) == "wrong_usage"
    assert send.requests == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"position": 101},
        {"position": -1},
        {"position": 50.0},
        {"position": True},
        {"position": None},
        {"command": "zz-OPEN"},
        {"command": 1},
        {"command": "UP", "position": 50},
        {},
    ],
)
async def test_an_invalid_write_sends_nothing_and_echoes_nothing(
    cache: Cache, arguments: dict[str, object]
) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0).set_shutter({"target": SHUTTER} | arguments)

    assert not_sent(result) == "invalid_write"
    assert "zz" not in body(result)["note"]
    assert send.requests == []


async def test_the_write_model_names_the_invalid_field(cache: Cache) -> None:
    result = await Writes(cache, FakeSend(), 1.0).set_shutter(
        {"target": SHUTTER, "position": 101}
    )

    assert "position" in body(result)["note"]
    assert "101" not in body(result)["note"]


@pytest.mark.parametrize("rate", [0.1, 1.0, 3.0, 10.0])
async def test_a_write_within_the_interval_is_rate_limited(
    cache: Cache, rate: float
) -> None:
    send = FakeSend()
    clock = FakeClock()
    writes = Writes(cache, send, rate, clock)
    await writes.set_shutter({"target": SHUTTER, "command": "DOWN"})
    clock.now += 0.5 / rate

    result = await writes.set_shutter({"target": SHUTTER, "command": "STOP"})

    assert not_sent(result) == "rate_limited"
    assert 0 < body(result)["retry_after_s"] <= 1 / rate
    assert len(send.requests) == 1


async def test_retry_after_s_never_exceeds_the_interval(cache: Cache) -> None:
    clock = FakeClock()
    # 1 / 6 s rounds up to 0.167 at the millisecond
    writes = Writes(cache, FakeSend(), 6.0, clock)
    await writes.set_shutter({"target": SHUTTER, "command": "DOWN"})

    result = await writes.set_shutter({"target": SHUTTER, "command": "STOP"})

    assert body(result)["retry_after_s"] <= 1 / 6


async def test_a_write_after_the_interval_is_sent(cache: Cache) -> None:
    send = FakeSend()
    clock = FakeClock()
    writes = Writes(cache, send, 2.0, clock)
    await writes.set_shutter({"target": SHUTTER, "command": "DOWN"})
    clock.now += 0.5

    result = await writes.set_shutter({"target": SHUTTER, "command": "STOP"})

    assert body(result)["outcome"] == "acknowledged"
    assert len(send.requests) == 2


async def test_a_refused_write_is_not_counted(cache: Cache) -> None:
    send = FakeSend()
    writes = Writes(cache, send, 1.0, FakeClock())
    await writes.set_shutter({"target": SHUTTER, "position": 101})

    result = await writes.set_shutter({"target": SHUTTER, "position": 0})

    assert body(result)["outcome"] == "acknowledged"


async def test_two_parallel_calls_send_one_write(cache: Cache) -> None:
    send = FakeSend()
    writes = Writes(cache, send, 1.0, FakeClock())

    results = await asyncio.gather(
        writes.set_shutter({"target": SHUTTER, "command": "UP"}),
        writes.set_shutter({"target": SHUTTER_GROUP, "command": "DOWN"}),
    )

    assert sorted(body(r)["outcome"] for r in results) == ["acknowledged", "not_sent"]
    assert len(send.requests) == 1


async def test_cancelled_during_the_send_is_sent_once_counted_and_logged(
    cache: Cache, caplog: pytest.LogCaptureFixture
) -> None:
    send = FakeSend(gate=asyncio.Event())
    writes = Writes(cache, send, 1.0, FakeClock())
    call = asyncio.create_task(writes.set_shutter({"target": SHUTTER, "command": "UP"}))
    while not send.requests:
        await asyncio.sleep(0)

    with caplog.at_level("INFO", logger="tydomio.mcp_server.writes"):
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    assert [r.getMessage() for r in caplog.records] == [
        "tool set_shutter: write cancelled, outcome unknown"
    ]
    later = await writes.set_shutter({"target": SHUTTER, "command": "UP"})
    assert not_sent(later) == "rate_limited"
    assert len(send.requests) == 1


@pytest.mark.parametrize(
    ("target", "url"),
    [(SHUTTER, b"/devices/10/endpoints/1/data"), (SHUTTER_GROUP, b"/groups/5/data")],
)
async def test_an_ok_is_acknowledged_after_one_put(
    cache: Cache, target: str, url: bytes
) -> None:
    websocket = FakeWebsocket(reply=tydom_answer(200))
    async with connected(websocket) as client:
        result = await Writes(cache, client.send, 1.0, now=lambda: SENT_AT).set_shutter(
            {"target": target, "command": "DOWN"}
        )

    assert not result.is_error
    assert body(result) == {
        "sent_at": "2026-11-03T08:09:10Z",
        "target": target,
        "outcome": "acknowledged",
        "fields": {"positionCmd": "DOWN"},
        "note": ACKNOWLEDGED_NOTE,
    }
    assert [p.split(b" ")[1] for p in puts(websocket)] == [url]
    assert puts(websocket)[0].endswith(b'[{"name": "positionCmd", "value": "DOWN"}]')


async def test_a_position_is_sent_as_the_position_field(cache: Cache) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0).set_shutter(
        {"target": SHUTTER, "position": 40}
    )

    assert body(result)["fields"] == {"position": 40}
    assert send.requests[0].content == b'[{"name": "position", "value": 40}]'


@pytest.mark.parametrize(
    ("reply", "timeout", "error"),
    [
        # no acknowledgement: the request times out at once
        (
            lambda sent: None if sent.startswith(b"PUT ") else tydom_answer(200)(sent),
            0,
            CommunicationError,
        ),
        (tydom_answer(500), 5, InvalidResponse),
        (tydom_answer(200, b'{"zz": 1}'), 5, InvalidResponse),
    ],
    ids=["timeout", "error status", "content"],
)
async def test_a_failed_send_is_unknown_after_one_put(
    cache: Cache,
    reply: Callable[[bytes], bytes | None],
    timeout: int,
    error: type[CommunicationError],
) -> None:
    raised: list[type[Exception]] = []
    websocket = FakeWebsocket(reply=reply)
    async with connected(websocket) as client:

        async def send(request: ClientRequest[Ok]) -> Ok:
            try:
                return await client.send(request, timeout)
            except CommunicationError as failure:
                raised.append(type(failure))
                raise

        result = await Writes(cache, send, 1.0, now=lambda: SENT_AT).set_shutter(
            {"target": SHUTTER, "command": "UP"}
        )

    assert raised == [error]
    assert result.is_error
    parsed = body(result)
    assert (parsed["outcome"], parsed["note"]) == ("unknown", UNKNOWN_NOTE)
    assert parsed["fields"] == {"positionCmd": "UP"}
    assert parsed["sent_at"] == "2026-11-03T08:09:10Z"
    assert len(puts(websocket)) == 1


async def test_a_write_lost_with_the_connection_is_unknown_after_one_put(
    cache: Cache,
) -> None:
    websocket = FakeWebsocket(
        reply=lambda sent: None if sent.startswith(b"PUT ") else tydom_answer(200)(sent)
    )
    async with connected(websocket) as client:
        call = asyncio.create_task(
            Writes(cache, client.send, 1.0).set_shutter(
                {"target": SHUTTER, "command": "UP"}
            )
        )
        while not puts(websocket):
            await asyncio.sleep(0)
        await websocket.close()
        result = await call

    assert body(result)["outcome"] == "unknown"
    assert len(puts(websocket)) == 1


async def test_a_communication_error_is_counted(cache: Cache) -> None:
    async def fail(_request: ClientRequest[Ok]) -> Ok:
        raise CommunicationError("Request timeout")

    writes = Writes(cache, fail, 1.0, FakeClock())
    await writes.set_shutter({"target": SHUTTER, "command": "UP"})

    result = await writes.set_shutter({"target": SHUTTER, "command": "UP"})

    assert not_sent(result) == "rate_limited"


async def test_logs_at_info_hold_no_id_nor_value(
    cache: Cache, caplog: pytest.LogCaptureFixture
) -> None:
    writes = Writes(cache, FakeSend(), 1.0, FakeClock())
    with caplog.at_level("INFO", logger="tydomio.mcp_server.writes"):
        await writes.set_shutter({"target": "e:99:1", "command": "UP"})
        await writes.set_shutter({"target": SHUTTER_GROUP, "position": 37})
        await writes.set_shutter({"target": SHUTTER, "position": 37})

    assert [r.getMessage() for r in caplog.records] == [
        "tool set_shutter on an endpoint: unknown_target",
        "tool set_shutter on a group: acknowledged",
        "tool set_shutter on an endpoint: rate_limited",
    ]


def test_every_write_model_is_classified_once() -> None:
    assert SHUTTER_USAGES | HEATING_USAGES == set(WRITES)
    assert not SHUTTER_USAGES & HEATING_USAGES


@pytest.mark.parametrize("target", ["e:10:1", "g:6", "a:8"])
async def test_set_heating_refuses_a_target_that_is_not_heating(
    cache: Cache, target: str
) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0, heating=True).set_heating(
        {"target": target, "fields": {"setpoint": 19.5}}
    )

    assert not_sent(result) == "wrong_usage"
    assert send.requests == []


@pytest.mark.parametrize("target", ["e:99:1", "a:99"])
async def test_set_heating_refuses_an_unknown_target(cache: Cache, target: str) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0, heating=True).set_heating(
        {"target": target, "fields": {"setpoint": 19.5}}
    )

    assert not_sent(result) == "unknown_target"
    assert send.requests == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"target": "a:7/../x", "fields": {"setpoint": 19.5}},
        {"target": "a:7", "fields": [["setpoint", 19.5]]},
        {"target": "a:7"},
        {"target": "a:7", "fields": {"setpoint": 19.5}, "zz-extra": 1},
    ],
)
async def test_set_heating_invalid_input_sends_nothing_and_echoes_nothing(
    cache: Cache, arguments: dict[str, object]
) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0, heating=True).set_heating(arguments)

    assert body(result) == {"code": "invalid_input", "message": "Invalid arguments"}
    assert send.requests == []


@pytest.mark.parametrize(
    ("target", "fields"),
    [
        ("e:20:1", {"setpoint": 19.3}),
        ("e:20:1", {"zz-field": 1}),
        ("e:20:1", {}),
        ("e:20:1", {"localMode": "zz-WARM"}),
        # the Python name is not the Tydom name: only aliases are taken
        ("e:20:1", {"heat_setpoint": 19.5}),
        ("e:20:1", {"selectedProg": 1}),
        ("e:30:1", {"setpoint": 19.5}),
        ("a:7", {"selectedProg": 1}),
    ],
)
async def test_set_heating_refuses_an_invalid_write(
    cache: Cache, target: str, fields: dict[str, object]
) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0, heating=True).set_heating(
        {"target": target, "fields": fields}
    )

    assert not_sent(result) == "invalid_write"
    assert "zz" not in body(result)["note"]
    assert send.requests == []


@pytest.mark.parametrize(
    ("target", "fields", "url"),
    [
        ("a:7", {"setpoint": 19.5}, b"/areas/7/data"),
        ("e:20:1", {"localMode": "NORMAL"}, b"/devices/20/endpoints/1/data"),
        ("e:30:1", {"selectedProg": 2}, b"/devices/30/endpoints/1/data"),
    ],
)
async def test_a_heating_write_is_acknowledged_after_one_put(
    cache: Cache, target: str, fields: dict[str, object], url: bytes
) -> None:
    websocket = FakeWebsocket(reply=tydom_answer(200))
    async with connected(websocket) as client:
        result = await Writes(cache, client.send, 1.0, heating=True).set_heating(
            {"target": target, "fields": fields}
        )

    assert not result.is_error
    parsed = body(result)
    assert (parsed["outcome"], parsed["fields"]) == ("acknowledged", fields)
    assert [p.split(b" ")[1] for p in puts(websocket)] == [url]
    ((name, value),) = fields.items()
    assert puts(websocket)[0].endswith(
        json.dumps([{"name": name, "value": value}]).encode()
    )


async def test_the_rate_is_shared_by_both_tools(cache: Cache) -> None:
    send = FakeSend()
    writes = Writes(cache, send, 1.0, FakeClock(), heating=True)
    await writes.set_shutter({"target": SHUTTER, "command": "UP"})

    result = await writes.set_heating({"target": "a:7", "fields": {"setpoint": 19.5}})

    assert not_sent(result) == "rate_limited"
    assert len(send.requests) == 1


async def test_set_heating_logs_at_info_hold_no_id_nor_value(
    cache: Cache, caplog: pytest.LogCaptureFixture
) -> None:
    writes = Writes(cache, FakeSend(), 1.0, FakeClock(), heating=True)
    with caplog.at_level("INFO", logger="tydomio.mcp_server.writes"):
        await writes.set_heating({"target": "g:6", "fields": {"setpoint": 19.5}})
        await writes.set_heating({"target": "a:7", "fields": {"setpoint": 19.5}})

    assert [r.getMessage() for r in caplog.records] == [
        "tool set_heating on a group: wrong_usage",
        "tool set_heating on an area: acknowledged",
    ]


def test_set_heating_lists_each_heating_write_field_with_its_bounds() -> None:
    models = (AreaWrite, BoilerWrite, Re2020ControlBoilerWrite)
    fields = SET_HEATING.input_schema["properties"]["fields"]
    properties: dict[str, dict[str, Any]] = fields["properties"]

    assert set(properties) == {
        info.alias for model in models for info in model.model_fields.values()
    }
    for name, schema in properties.items():
        model = next(
            m for m in models if name in {i.alias for i in m.model_fields.values()}
        )
        accepted, refused = schema_values(schema)
        for value in accepted:
            model.model_validate({name: value})
        for value in refused:
            with pytest.raises(InvalidWrite):
                model.model_validate({name: value})


def schema_values(schema: dict[str, Any]) -> tuple[list[object], list[object]]:
    """Values the schema accepts, and values just outside it."""
    if "enum" in schema:
        return [str(member) for member in schema["enum"]], ["zz-other"]
    low, high = schema["minimum"], schema["maximum"]
    step = schema.get("multipleOf", 1)
    refused = [low - step, high + step]
    if schema["type"] == "integer":
        assert step == 1
        refused.append(float(low))
    else:
        refused.append(low + step / 2)
    return [low, high], refused
