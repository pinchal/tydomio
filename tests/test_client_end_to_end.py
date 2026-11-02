import asyncio
from contextlib import suppress

from websockets.exceptions import ConnectionClosed

from tests.conftest import (
    CONFIG_CONTENT,
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeSleep,
    FakeWebsocket,
    ScriptExhaustedError,
    put,
    tydom_reply,
)
from tydomio.client import AsyncTydomClient
from tydomio.installation import Installation
from tydomio.model.config import Config
from tydomio.model.data import DevicesData
from tydomio.model.usages.shutter import Shutter, ShutterWrite
from tydomio.requests import GetConfigFile, TydomPutDevicesDataRequest, WriteEndpoint
from tydomio.response import Ok


async def test_user_reads_the_config_and_receives_pushed_devices_data() -> None:
    configs: list[Config] = []
    devices_data: list[DevicesData] = []
    both_received = asyncio.Event()

    def check_both_received() -> None:
        if configs and devices_data:
            both_received.set()

    async def read_config(client: AsyncTydomClient) -> None:
        configs.append((await client.send(GetConfigFile())).config)
        check_both_received()

    async def on_devices_data(request: TydomPutDevicesDataRequest) -> None:
        devices_data.append(request.data)
        check_both_received()

    websocket = FakeWebsocket(
        put("/devices/data", b'[{"id": 1, "endpoints": []}]'), reply=tydom_reply
    )
    client = AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        on_connection_routines=(read_config,),
        authorization_provider=FakeAuthorizationProvider(),
        connection_factory=FakeConnectionFactory(websocket),
        sleep=FakeSleep(),
    )
    client.subscribe(TydomPutDevicesDataRequest, on_devices_data)
    run = asyncio.create_task(client.run_websocket())
    async with asyncio.timeout(1):
        await both_received.wait()
    run.cancel()
    await run

    assert [type(configs[0]), type(devices_data[0])] == [Config, DevicesData]


def close_on_ping(sent: bytes) -> bytes:
    """Lose the connection as soon as the client pings."""
    raise ConnectionClosed(None, None)


async def test_user_is_disconnected_then_reconnected_after_connection_lost() -> None:
    connection_factory = FakeConnectionFactory(
        FakeWebsocket(reply=close_on_ping), FakeWebsocket(reply=close_on_ping)
    )
    # number of connections opened when each disconnection routine runs
    disconnections: list[int] = []

    async def on_disconnection() -> None:
        disconnections.append(len(connection_factory.authorizations))

    client = AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        on_disconnection_routines=(on_disconnection,),
        authorization_provider=FakeAuthorizationProvider(),
        connection_factory=connection_factory,
        sleep=FakeSleep(),
    )
    # the script ends at the third connection
    with suppress(ScriptExhaustedError):
        await client.run_websocket()

    assert disconnections == [1, 2]


SHUTTER_ENDPOINT = (
    b'{"id_endpoint": 1, "first_usage": "shutter", "skill": "skill", "id_device": 10,'
    b' "name": "name", "anticipation_start": false, "picto": "picto",'
    b' "last_usage": "shutter", "widget_behavior": {"tutorial_id": "tutorial"}}'
)


def reply_with_a_shutter(sent: bytes) -> bytes:
    """Answer as tydom_reply, with one shutter endpoint in the config."""
    return tydom_reply(sent).replace(
        CONFIG_CONTENT,
        CONFIG_CONTENT.replace(
            b'"endpoints": []', b'"endpoints": [' + SHUTTER_ENDPOINT + b"]"
        ),
    )


async def test_user_writes_a_shutter_position_found_in_the_config() -> None:
    acknowledgements: list[Ok] = []
    written = asyncio.Event()

    async def write_position(client: AsyncTydomClient) -> None:
        installation = Installation.from_config(
            (await client.send(GetConfigFile())).config
        )
        ref = installation.endpoint(10, 1, Shutter)
        acknowledgements.append(
            await client.send(WriteEndpoint(ref, ShutterWrite(position=42)))
        )
        written.set()

    websocket = FakeWebsocket(reply=reply_with_a_shutter)
    client = AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        on_connection_routines=(write_position,),
        authorization_provider=FakeAuthorizationProvider(),
        connection_factory=FakeConnectionFactory(websocket),
        sleep=FakeSleep(),
    )
    run = asyncio.create_task(client.run_websocket())
    async with asyncio.timeout(1):
        await written.wait()
    run.cancel()
    await run

    assert isinstance(acknowledgements[0], Ok)
    writes = [sent for sent in websocket.sent if sent.startswith(b"PUT ")]
    assert [(w.split(b" ")[1], w.split(b"\r\n\r\n")[1]) for w in writes] == [
        (b"/devices/10/endpoints/1/data", b'[{"name": "position", "value": 42}]')
    ]
