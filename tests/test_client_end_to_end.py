import asyncio
from contextlib import suppress

from websockets.exceptions import ConnectionClosed

from tests.conftest import (
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeSleep,
    FakeWebsocket,
    ScriptExhaustedError,
    put,
    tydom_reply,
)
from tydomio.client import AsyncTydomClient
from tydomio.model.config import Config
from tydomio.model.data import DevicesData
from tydomio.requests import GetConfigFile, TydomPutDevicesDataRequest


async def test_user_reads_the_config_and_receives_pushed_devices_data() -> None:
    configs: list[Config] = []
    # TydomRequest.data is typed with a TypeVar of __init__, not DevicesData
    devices_data: list[object] = []
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
        tydom_request_handlers=(on_devices_data,),
        authorization_provider=FakeAuthorizationProvider(),
        connection_factory=FakeConnectionFactory(websocket),
        sleep=FakeSleep(),
    )
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
