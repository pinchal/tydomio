import asyncio
from contextlib import suppress

from tests.conftest import (
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeSleep,
    FakeWebsocket,
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
    # how the loop ends on cancellation is not what this scenario asserts
    with suppress(asyncio.CancelledError):
        await run

    assert [type(configs[0]), type(devices_data[0])] == [Config, DevicesData]
