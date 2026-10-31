import asyncio
from contextlib import suppress

import httpx
import pytest

from tests.conftest import (
    AUTHORIZATION,
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeSleep,
    FakeWebsocket,
    ScriptExhaustedError,
)
from tydomio.client import (
    AsyncTydomClient,
    OnConnectionRoutine,
    OnDisconnectionRoutine,
    TydomRequestHandler,
    parse_message,
    parse_request,
    parse_response,
)
from tydomio.error import InvalidRequest, TydomIoError
from tydomio.requests import (
    JsonRequest,
    PingRequest,
    Request,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
)
from tydomio.response import Response

DEVICES_DATA = b'[{"id": 1, "endpoints": []}]'
AREAS_DATA = b'[{"id": 1, "error": 0, "data": []}]'


def put(url: str, content: bytes) -> bytes:
    return (
        f"PUT {url} HTTP/1.1\r\ncontent-type: application/json\r\n\r\n".encode()
        + content
    )


def make_client(*handlers: TydomRequestHandler) -> AsyncTydomClient:
    return AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        tydom_request_handlers=handlers,
    )


def make_client_with_doubles(
    connection_factory: FakeConnectionFactory,
    *,
    authorization_provider: FakeAuthorizationProvider | None = None,
    sleep: FakeSleep | None = None,
    on_connection: tuple[OnConnectionRoutine, ...] = (),
    on_disconnection: tuple[OnDisconnectionRoutine, ...] = (),
) -> AsyncTydomClient:
    return AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        on_connection_routines=on_connection,
        on_disconnection_routines=on_disconnection,
        authorization_provider=authorization_provider or FakeAuthorizationProvider(),
        connection_factory=connection_factory,
        sleep=sleep or FakeSleep(),
    )


async def run_until_script_ends(client: AsyncTydomClient) -> None:
    with suppress(ScriptExhaustedError):
        await client.run_websocket()


async def run_then_cancel(client: AsyncTydomClient, reached: asyncio.Event) -> None:
    run = asyncio.create_task(cancellable_run(client))
    wait_reached = asyncio.create_task(reached.wait())
    await asyncio.wait((run, wait_reached), return_when=asyncio.FIRST_COMPLETED)
    wait_reached.cancel()
    run.cancel()
    await run


async def cancellable_run(client: AsyncTydomClient) -> None:
    # how the loop ends on cancellation is not what these tests assert
    with suppress(asyncio.CancelledError):
        await client.run_websocket()


def test_parse_response_reads_status() -> None:
    response = parse_response(b"HTTP/1.1 404 Not Found\r\n\r\n")

    assert response.status == 404


def test_parse_response_lowercases_header_names() -> None:
    response = parse_response(b"HTTP/1.1 200 OK\r\nTransac-Id: 12\r\n\r\n")

    assert response.headers == {"transac-id": "12"}


def test_parse_response_keeps_content() -> None:
    response = parse_response(b"HTTP/1.1 200 OK\r\n\r\n{}")

    assert response.content == b"{}"


def test_parse_response_empty_content_is_none() -> None:
    response = parse_response(b"HTTP/1.1 200 OK\r\n\r\n")

    assert response.content is None


def test_parse_message_reads_response() -> None:
    message = parse_message(b"HTTP/1.1 200 OK\r\n\r\n", cmd_prefix=b"")

    assert isinstance(message, Response)


def test_parse_message_reads_prefixed_response() -> None:
    message = parse_message(b"\x02HTTP/1.1 200 OK\r\n\r\n", cmd_prefix=b"\x02")

    assert isinstance(message, Response)


def test_parse_message_reads_request() -> None:
    message = parse_message(put("/other", b"{}"), cmd_prefix=b"")

    assert isinstance(message, Request)


def test_parse_request_reads_method_url_and_headers() -> None:
    request = parse_request(put("/other", b"{}"))

    assert (request.method, request.url, request.headers) == (
        "PUT",
        "/other",
        {"content-type": "application/json"},
    )


def test_parse_request_empty_content_is_none() -> None:
    request = parse_request(
        b"GET /a HTTP/1.1\r\nContent-Type: application/json\r\n\r\n"
    )

    assert request.content is None


def test_parse_request_types_devices_data() -> None:
    request = parse_request(put("/devices/data", DEVICES_DATA))

    assert isinstance(request, TydomPutDevicesDataRequest)


def test_parse_request_types_areas_data() -> None:
    request = parse_request(put("/areas/data", AREAS_DATA))

    assert isinstance(request, TydomPutAreasDataRequest)


def test_parse_request_falls_back_to_json_on_invalid_data() -> None:
    request = parse_request(put("/devices/data", b'{"unexpected": true}'))

    assert type(request) is JsonRequest


def test_parse_request_other_url_is_json_request() -> None:
    request = parse_request(put("/other", b"{}"))

    assert type(request) is JsonRequest


def test_parse_request_rejects_invalid_request_line() -> None:
    with pytest.raises(InvalidRequest):
        parse_request(b"PUT /devices/data HTTP/1.0\r\n\r\n")


def test_parse_request_removes_chunk_framing() -> None:
    chunk = b"{}"
    framed = f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n0\r\n\r\n"

    request = parse_request(put("/other", framed))

    assert request.content == chunk


def test_client_accepts_handler_of_tydom_request_subclass() -> None:
    async def handler(_request: TydomPutDevicesDataRequest) -> None:
        return None

    make_client(handler)


def test_client_rejects_handler_of_other_type() -> None:
    async def handler(_request: int) -> None:
        return None

    with pytest.raises(TydomIoError):
        make_client(handler)


async def test_send_rejects_request_without_transaction_id() -> None:
    request = PingRequest()
    request.transaction_id = None

    with pytest.raises(TydomIoError):
        await make_client().send(request)


async def test_send_requires_connection() -> None:
    with pytest.raises(RuntimeError):
        await make_client().send(PingRequest())


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("unreachable"), httpx.ConnectTimeout("timeout")]
)
async def test_run_websocket_retries_authorization_after_a_pause_when_unreachable(
    error: Exception,
) -> None:
    authorization_provider = FakeAuthorizationProvider(error)
    sleep = FakeSleep()
    client = make_client_with_doubles(
        FakeConnectionFactory(),
        authorization_provider=authorization_provider,
        sleep=sleep,
    )

    await run_until_script_ends(client)

    assert (sleep.delays, authorization_provider.calls) == ([5], 2)


async def test_run_websocket_connects_with_the_authorization() -> None:
    connection_factory = FakeConnectionFactory()

    await run_until_script_ends(make_client_with_doubles(connection_factory))

    assert connection_factory.authorizations == [AUTHORIZATION]


async def test_run_websocket_gives_the_client_to_connection_routines() -> None:
    received: list[AsyncTydomClient] = []
    routine_ran = asyncio.Event()

    async def routine(client: AsyncTydomClient) -> None:
        received.append(client)
        routine_ran.set()

    client = make_client_with_doubles(
        FakeConnectionFactory(FakeWebsocket()), on_connection=(routine,)
    )

    await run_then_cancel(client, routine_ran)

    assert received == [client]


async def test_run_websocket_does_not_reconnect_when_cancelled() -> None:
    connection_factory = FakeConnectionFactory(FakeWebsocket(), FakeWebsocket())
    client = make_client_with_doubles(connection_factory)

    await run_then_cancel(client, connection_factory.connected)

    assert len(connection_factory.authorizations) == 1


async def test_run_websocket_runs_disconnection_routines_when_cancelled() -> None:
    connection_factory = FakeConnectionFactory(FakeWebsocket())
    disconnections: list[None] = []

    async def routine() -> None:
        disconnections.append(None)

    client = make_client_with_doubles(connection_factory, on_disconnection=(routine,))

    await run_then_cancel(client, connection_factory.connected)

    assert len(disconnections) == 1


async def test_run_websocket_closes_the_connection_when_cancelled() -> None:
    websocket = FakeWebsocket()
    connection_factory = FakeConnectionFactory(websocket)
    client = make_client_with_doubles(connection_factory)

    await run_then_cancel(client, connection_factory.connected)

    assert websocket.closed
