import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp import types as mcp_types

from tests.builders import writable_cache
from tests.conftest import (
    FakeAuthorizationProvider,
    FakeClock,
    FakeConnectionFactory,
    FakeSend,
    FakeSleep,
    FakeWebsocket,
    ScriptExhaustedError,
)
from tydomio.client import AsyncTydomClient
from tydomio.error import CommunicationError, InvalidResponse, InvalidWrite
from tydomio.mcp_server.writes import SET_HEATING, Writes
from tydomio.model.usages.area import AreaWrite
from tydomio.model.usages.boiler import BoilerWrite
from tydomio.model.usages.re2020_control_boiler import Re2020ControlBoilerWrite
from tydomio.requests import ClientRequest
from tydomio.response import Ok
from tydomio.state.cache import Cache

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
    return writable_cache()


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


@pytest.mark.parametrize("arguments", [{"command": "UP", "position": 50}, {}])
async def test_set_shutter_takes_exactly_one_of_command_or_position(
    cache: Cache, arguments: dict[str, object]
) -> None:
    send = FakeSend()

    result = await Writes(cache, send, 1.0).set_shutter({"target": SHUTTER} | arguments)

    assert not_sent(result) == "invalid_write"
    assert body(result)["note"] == (
        "Nothing was sent: give exactly one of command or position."
    )
    assert send.requests == []


async def test_set_shutter_checks_the_target_before_command_or_position(
    cache: Cache,
) -> None:
    result = await Writes(cache, FakeSend(), 1.0).set_shutter({"target": "e:20:1"})

    assert not_sent(result) == "wrong_usage"


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        (
            "set_shutter",
            {"target": "e:99:1", "command": "UP"},
            {
                "reason": "unknown_target",
                "note": "Nothing was sent: the target does not exist. "
                "Take its id from get_state.",
            },
        ),
        (
            "set_shutter",
            {"target": "e:20:1", "command": "UP"},
            {
                "reason": "wrong_usage",
                "note": "Nothing was sent: the target is not a shutter or a "
                "shutter group.",
            },
        ),
        (
            "set_heating",
            {"target": "g:6", "fields": {"setpoint": 19.5}},
            {
                "reason": "wrong_usage",
                "note": "Nothing was sent: the target is not a boiler or "
                "re2020ControlBoiler endpoint, nor a heating area.",
            },
        ),
        (
            "set_heating",
            {"target": SHUTTER, "fields": {"setpoint": 19.5}},
            {
                "reason": "wrong_usage",
                "note": "Nothing was sent: the target is not a boiler or "
                "re2020ControlBoiler endpoint, nor a heating area.",
            },
        ),
        (
            "set_shutter",
            {"target": SHUTTER, "position": 101},
            {
                "reason": "invalid_write",
                "note": "Nothing was sent: ShutterWrite: invalid fields position.",
            },
        ),
    ],
)
async def test_a_refusal_is_reported_with_its_note(
    cache: Cache, tool: str, arguments: dict[str, object], expected: dict[str, str]
) -> None:
    writes = Writes(cache, FakeSend(), 1.0, heating=True)

    result = await writes.call(tool, arguments)

    assert result.is_error
    assert (
        body(result)
        == {
            "target": arguments["target"],
            "outcome": "not_sent",
            "fields": {},
        }
        | expected
    )


async def test_a_write_while_not_synced_is_reported_with_its_note(
    cache: Cache,
) -> None:
    cache.mark_disconnected()

    result = await Writes(cache, FakeSend(), 1.0).set_shutter(
        {"target": SHUTTER, "command": "UP"}
    )

    assert body(result)["note"] == (
        "Nothing was sent: the cache is not synced with the Tydom. "
        "See the cache block of get_state."
    )


async def test_a_rate_limited_write_is_reported_with_its_retry(cache: Cache) -> None:
    writes = Writes(cache, FakeSend(), 2.0, FakeClock())
    await writes.set_shutter({"target": SHUTTER, "command": "UP"})

    result = await writes.set_shutter({"target": SHUTTER, "command": "UP"})

    assert body(result) == {
        "target": SHUTTER,
        "outcome": "not_sent",
        "reason": "rate_limited",
        "retry_after_s": 0.5,
        "fields": {},
        "note": "Nothing was sent: too many writes. Retry after retry_after_s.",
    }


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


async def test_logs_at_info_hold_no_id_nor_value(
    cache: Cache, caplog: pytest.LogCaptureFixture
) -> None:
    writes = Writes(cache, FakeSend(), 1.0, FakeClock())
    with caplog.at_level("INFO", logger="tydomio.state.writer"):
        await writes.set_shutter({"target": "e:99:1", "command": "UP"})
        await writes.set_shutter({"target": SHUTTER_GROUP, "position": 37})
        await writes.set_shutter({"target": SHUTTER, "position": 37})

    assert [r.getMessage() for r in caplog.records] == [
        "tool set_shutter on an endpoint: unknown_target",
        "tool set_shutter on a group: acknowledged",
        "tool set_shutter on an endpoint: rate_limited",
    ]


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
    with caplog.at_level("INFO", logger="tydomio.state.writer"):
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
