import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import override

import pytest

from tests.conftest import (
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeSleep,
    FakeWebsocket,
    ScriptExhaustedError,
    answer_gets,
    put,
)
from tests.mcp.conftest import areas, config, devices
from tests.mcp.conftest import data as field
from tydomio.client import AsyncTydomClient
from tydomio.mcp_server import sync
from tydomio.mcp_server.cache import Cache, CacheError
from tydomio.model.data import AreasData, DevicesData
from tydomio.requests import TydomPutAreasDataRequest, TydomPutDevicesDataRequest

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
SHUTTER = (10, 1)
AREA = 7
CONFIG = config({SHUTTER: ("Shutter", "shutter")}, {AREA: ("Living", "area")})
SNAPSHOT = devices((*SHUTTER, [field("position", 0)]))
PUSHED = devices((*SHUTTER, [field("position", 100)]))
AREAS = areas((AREA, [field("hygroIn", 50)]))
ANSWERS = {
    "/configs/file": (200, CONFIG.model_dump_json().encode()),
    "/devices/data": (200, SNAPSHOT.model_dump_json().encode()),
    "/areas/data": (200, AREAS.model_dump_json().encode()),
    "/refresh/all": (200, b""),
}
DEVICES_PUSH = put("/devices/data", PUSHED.model_dump_json().encode())


def tydom(**overrides: tuple[int, bytes]) -> FakeWebsocket:
    """Give a websocket answering the bootstrap GETs, some overridden by url."""
    answers = ANSWERS | {f"/{key.replace('_', '/')}": v for key, v in overrides.items()}
    return FakeWebsocket(reply=answer_gets(answers))


def start(
    factory: FakeConnectionFactory, cache: Cache, sleep: FakeSleep | None = None
) -> tuple[AsyncTydomClient, asyncio.Task[None]]:
    """Run a client with the routine and the handlers of a Sync over the cache."""
    syncing = sync.Sync(cache, sleep or FakeSleep(), lambda: NOW)
    client = AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        on_connection_routines=(syncing.routine,),
        authorization_provider=FakeAuthorizationProvider(),
        connection_factory=factory,
        sleep=FakeSleep(),
    )
    client.subscribe(TydomPutDevicesDataRequest, syncing.on_devices_push)
    client.subscribe(TydomPutAreasDataRequest, syncing.on_areas_push)
    return client, asyncio.create_task(client.run_websocket())


async def until(condition: Callable[[], bool]) -> None:
    # a guard against a hang only: every delay of the code under test is faked
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0)


async def stop(run: asyncio.Task[None]) -> None:
    run.cancel()
    with suppress(ScriptExhaustedError):
        await run


def position(cache: Cache) -> float | str | None:
    return cache.endpoints[SHUTTER].fields["position"].value


def messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == sync.__name__]


def start_lines(*websockets: FakeWebsocket) -> list[bytes]:
    return [m.split(b"\r\n")[0] for w in websockets for m in w.sent]


REFRESH = b"POST /refresh/all HTTP/1.1"


async def test_bootstrap_gets_config_devices_areas_then_is_synced_and_refreshes() -> (
    None
):
    cache = Cache()
    websocket = tydom()
    _, run = start(FakeConnectionFactory(websocket), cache)

    await until(lambda: cache.synced and REFRESH in start_lines(websocket))
    await stop(run)

    assert [line for line in start_lines(websocket) if b"/ping" not in line] == [
        b"GET /configs/file HTTP/1.1",
        b"GET /devices/data HTTP/1.1",
        b"GET /areas/data HTTP/1.1",
        REFRESH,
    ]
    assert cache.synced_at == NOW
    assert cache.config == CONFIG
    assert position(cache) == 0
    assert cache.areas[AREA].fields["hygroIn"].value == 50


@pytest.mark.parametrize(("push_first", "expected"), [(True, 0), (False, 100)])
async def test_a_push_before_the_snapshot_is_superseded_one_after_wins(
    *, push_first: bool, expected: float
) -> None:
    cache = Cache()
    answer = answer_gets(ANSWERS)

    def reply(sent: bytes) -> bytes | None:
        response = answer(sent)
        if response is None or not sent.startswith(b"GET /devices/data "):
            return response
        # both frames buffered before the reader runs, in the Tydom's order
        first, second = (
            (DEVICES_PUSH, response) if push_first else (response, DEVICES_PUSH)
        )
        websocket.push(first)
        return second

    websocket = FakeWebsocket(reply=reply)
    _, run = start(FakeConnectionFactory(websocket), cache)

    await until(lambda: cache.synced)
    await asyncio.sleep(0)  # let a handler started after the response run
    await stop(run)

    assert position(cache) == expected


async def test_a_failing_get_backs_off_without_reconnecting() -> None:
    cache = Cache()
    factory = FakeConnectionFactory(tydom(devices_data=(404, b"{}")))
    sleep = FakeSleep()
    _, run = start(factory, cache, sleep)

    await until(lambda: len(sleep.delays) >= 7)
    await stop(run)

    assert sleep.delays[:7] == [10, 20, 40, 80, 160, 300, 300]
    assert len(factory.authorizations) == 1
    assert cache.synced is False


async def test_a_get_timing_out_backs_off_without_reconnecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sync, "REQUEST_TIMEOUT", 0)
    cache = Cache()
    factory = FakeConnectionFactory(FakeWebsocket(reply=answer_gets({})))
    sleep = FakeSleep()
    _, run = start(factory, cache, sleep)

    await until(lambda: len(sleep.delays) >= 3)
    await stop(run)

    assert sleep.delays[:3] == [10, 20, 40]
    assert len(factory.authorizations) == 1
    assert cache.synced is False


async def test_a_failure_logs_the_step_and_the_error_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = Cache()
    sleep = FakeSleep()
    _, run = start(FakeConnectionFactory(tydom(areas_data=(404, b"{}"))), cache, sleep)

    with caplog.at_level(logging.WARNING, logger=sync.__name__):
        await until(lambda: bool(sleep.delays))
    await stop(run)

    assert messages(caplog)[0] == "Sync failed at /areas/data: InvalidResponse"


async def test_a_failing_refresh_is_logged_and_the_cache_stays_synced(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = Cache()
    sleep = FakeSleep()
    websocket = tydom(refresh_all=(404, b""))
    _, run = start(FakeConnectionFactory(websocket), cache, sleep)

    with caplog.at_level(logging.WARNING, logger=sync.__name__):
        await until(lambda: bool(messages(caplog)))
    synced = cache.synced
    await stop(run)

    assert messages(caplog) == ["Refresh failed: InvalidResponse"]
    assert synced is True
    assert sleep.delays == []
    assert start_lines(websocket).count(REFRESH) == 1


async def test_losing_the_connection_keeps_the_data_disconnected() -> None:
    cache = Cache()
    websocket = tydom()
    _, run = start(FakeConnectionFactory(websocket), cache)

    await until(lambda: cache.synced)
    await websocket.close()
    with pytest.raises(ScriptExhaustedError):
        await run

    assert (cache.connection, cache.synced) == ("disconnected", False)
    assert position(cache) == 0


async def test_a_new_config_date_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    cache = Cache()
    changed = CONFIG.model_copy(update={"date": 2}).model_dump_json().encode()
    first, second = tydom(), tydom(configs_file=(200, changed))
    factory = FakeConnectionFactory(first, second)
    _, run = start(factory, cache)

    with caplog.at_level(logging.INFO, logger=sync.__name__):
        await until(lambda: cache.synced)
        assert not messages(caplog)
        await first.close()
        await until(lambda: len(factory.authorizations) == 2 and cache.synced)
    await stop(run)

    assert messages(caplog) == ["Config changed"]


class BrokenCache(Cache):
    """Fails every merge."""

    @override
    def merge_devices(self, data: DevicesData) -> None:
        raise KeyError(data)

    @override
    def merge_areas(self, data: AreasData) -> None:
        raise KeyError(data)


@pytest.mark.parametrize(
    "push",
    [DEVICES_PUSH, put("/areas/data", AREAS.model_dump_json().encode())],
    ids=["devices", "areas"],
)
async def test_a_failing_handler_ends_the_client_with_a_cache_error(
    push: bytes,
) -> None:
    websocket = tydom()
    websocket.push(push)
    _, run = start(FakeConnectionFactory(websocket), BrokenCache())

    with pytest.raises(ExceptionGroup) as raised:
        await run

    assert raised.group_contains(CacheError)
    error = next(e for e in raised.value.exceptions if isinstance(e, CacheError))
    assert (error.__cause__, error.__suppress_context__) == (None, True)


async def test_each_connection_sends_only_the_bootstrap_gets_a_refresh_and_pings() -> (
    None
):
    cache = Cache()
    first, second = tydom(), tydom()
    factory = FakeConnectionFactory(first, second)
    _, run = start(factory, cache)

    for websocket in (first, second):
        await until(lambda: cache.synced)
        websocket.push(DEVICES_PUSH)
        await until(lambda: position(cache) == 100)
        await websocket.close()
        await until(lambda: not cache.synced)
    with pytest.raises(ScriptExhaustedError):
        await run

    assert len(factory.authorizations) == 3
    for websocket in (first, second):
        assert set(start_lines(websocket)) == {
            b"GET /ping HTTP/1.1",
            b"GET /configs/file HTTP/1.1",
            b"GET /devices/data HTTP/1.1",
            b"GET /areas/data HTTP/1.1",
            REFRESH,
        }
