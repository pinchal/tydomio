import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp import Client, MCPError
from mcp import types as mcp_types
from mcp.server.lowlevel import Server

from tests.mcp.conftest import areas, config, data, devices
from tydomio.mcp_server.cache import Cache
from tydomio.mcp_server.tools import GET_STATE, build_server, error_result
from tydomio.mcp_server.writes import SET_HEATING, SET_SHUTTER, Writes
from tydomio.requests import ClientRequest
from tydomio.response import Ok
from tydomio.scrub import Secrets

SECRETS = Secrets(
    password="".join(("hunter", "2")), ip="192.168.1.9", mac="001A2BAABBCC"
)


@pytest.fixture(name="cache")
def synced_cache() -> Cache:
    cache = Cache()
    cache.apply_config(
        config({(10, 1): ("Shutter", "shutter")}, {7: ("Living", "area")})
    )
    cache.replace_devices(devices((10, 1, [data("position", 100)])), [])
    cache.replace_areas(areas((7, [data("hygroIn", 48.0)])), [])
    cache.mark_connected()
    cache.mark_synced(datetime(2026, 11, 2, 9, 14, 5, tzinfo=UTC))
    return cache


async def call(
    cache: Cache, name: str, arguments: Any = None
) -> mcp_types.CallToolResult:
    async with Client(build_server(cache, SECRETS)) as client:
        return await client.call_tool(name, arguments)


def text(result: mcp_types.CallToolResult) -> str:
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, mcp_types.TextContent)
    return block.text


async def test_tools_list_is_get_state_alone(cache: Cache) -> None:
    async with Client(build_server(cache, SECRETS)) as client:
        listed = await client.list_tools()

    assert listed.tools == [GET_STATE]
    tool = listed.tools[0]
    assert tool.input_schema["type"] == "object"
    assert tool.input_schema["additionalProperties"] is False
    assert not {"anyOf", "oneOf"} & tool.input_schema.keys()
    assert tool.output_schema is None


async def test_no_arguments_gives_everything_as_compact_json(cache: Cache) -> None:
    result = await call(cache, "get_state")

    assert not result.is_error
    assert result.structured_content is None
    body = text(result)
    parsed = json.loads(body)
    assert [i["id"] for i in parsed["items"]] == ["e:10:1", "a:7"]
    assert parsed["cache"]["connection"] == "connected"
    assert "groups" not in parsed
    assert body == json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)


async def test_ids_filter(cache: Cache) -> None:
    result = await call(cache, "get_state", {"ids": ["a:7", "e:9:9"]})

    parsed = json.loads(text(result))
    assert [i["id"] for i in parsed["items"]] == ["a:7"]
    assert parsed["unknown_ids"] == ["e:9:9"]


async def test_before_the_first_sync() -> None:
    result = await call(Cache(), "get_state", {})

    assert json.loads(text(result)) == {
        "cache": {"connection": "connecting", "synced": False, "synced_at": None},
        "items": [],
        "unknown_ids": [],
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"ids": "e:1:2/../x"},
        {"ids": []},
        {"ids": [f"a:{n}" for n in range(51)]},
        {"ids": ["e:1:2/../x"]},
        {"ids": ["a:1", "a:1"]},
        {"ids": [1]},
        {"ids": ["a:1"], "extra": "zz-secret-value"},
        {"zz-secret-value": 1},
        {"ids": None},
    ],
)
async def test_invalid_input_is_an_error_result_without_echo(
    cache: Cache, arguments: dict[str, Any]
) -> None:
    result = await call(cache, "get_state", arguments)

    assert result.is_error
    assert json.loads(text(result)) == {
        "code": "invalid_input",
        "message": "Invalid arguments",
    }
    assert result.structured_content is None


async def test_the_submitted_value_is_not_echoed(cache: Cache) -> None:
    for arguments in ({"ids": "zz/../x"}, {"ids": ["zz/../x"]}, {"zz/../x": 1}):
        result = await call(cache, "get_state", arguments)
        assert result.is_error
        assert "zz" not in text(result)


async def test_unknown_tool_is_a_protocol_error(cache: Cache) -> None:
    async with Client(build_server(cache, SECRETS)) as client:
        # caught inside the client: leaving it with the error would wrap it in a group
        with pytest.raises(MCPError) as raised:
            await client.call_tool("set_shutter", {"ids": ["zz-name"]})

    assert raised.value.message == "Unknown tool"
    assert "set_shutter" not in str(raised.value)


async def test_logs_outcome_without_arguments(
    cache: Cache, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="tydomio.mcp_server.tools"):
        await call(cache, "get_state", {"ids": ["zz/../x"]})
        await call(cache, "get_state")

    assert [r.getMessage() for r in caplog.records] == [
        "tool get_state: invalid_input",
        "tool get_state: ok",
    ]


def test_error_result() -> None:
    result = error_result("c", "m")

    assert result.is_error
    assert json.loads(text(result)) == {"code": "c", "message": "m"}


async def never_send(_request: ClientRequest[Ok]) -> Ok:
    raise AssertionError("nothing may be sent")


def with_writes(cache: Cache) -> Server:
    return build_server(cache, SECRETS, Writes(cache, never_send, 1.0))


async def test_with_writes_set_shutter_is_listed_as_destructive_with_a_prompt(
    cache: Cache,
) -> None:
    async with Client(with_writes(cache)) as c:
        listed = await c.list_tools()

    assert listed.tools == [GET_STATE, SET_SHUTTER]
    tool = listed.tools[1]
    assert tool.annotations == mcp_types.ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
    )
    assert tool.meta == {"anthropic/requiresUserInteraction": True}
    assert tool.input_schema["additionalProperties"] is False
    assert not {"anyOf", "oneOf"} & tool.input_schema.keys()


async def test_with_writes_get_state_lists_the_groups(cache: Cache) -> None:
    cache.apply_config(
        config(
            {(10, 1): ("Shutter", "shutter")},
            {7: ("Living", "area")},
            {5: ("All shutters", "shutter")},
        )
    )
    async with Client(with_writes(cache)) as c:
        result = await c.call_tool("get_state", {})

    assert [g["id"] for g in json.loads(text(result))["groups"]] == ["g:5"]


async def test_set_shutter_does_not_echo_its_input_through_the_sdk(
    cache: Cache,
) -> None:
    async with Client(with_writes(cache)) as c:
        result = await c.call_tool("set_shutter", {"target": "e:1:2/../zz"})

    assert result.is_error
    assert json.loads(text(result))["code"] == "invalid_input"
    assert "zz" not in text(result)


async def test_with_heating_set_heating_is_listed_as_destructive_with_a_prompt(
    cache: Cache,
) -> None:
    server = build_server(cache, SECRETS, Writes(cache, never_send, 1.0, heating=True))
    async with Client(server) as c:
        listed = await c.list_tools()

    assert listed.tools == [GET_STATE, SET_SHUTTER, SET_HEATING]
    tool = listed.tools[2]
    assert tool.annotations == mcp_types.ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
    )
    assert tool.meta == {"anthropic/requiresUserInteraction": True}
    assert tool.input_schema["additionalProperties"] is False
    assert not {"anyOf", "oneOf"} & tool.input_schema.keys()


async def test_without_heating_set_heating_is_an_unknown_tool(cache: Cache) -> None:
    async with Client(with_writes(cache)) as c:
        with pytest.raises(MCPError) as raised:
            await c.call_tool("set_heating", {"target": "a:7", "fields": {}})

    assert raised.value.message == "Unknown tool"
