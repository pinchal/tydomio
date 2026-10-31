import asyncio
import logging
from contextlib import suppress

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from tests.conftest import (
    AUTHORIZATION,
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeSleep,
    FakeWebsocket,
    ScriptExhaustedError,
    put,
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
from tydomio.error import CommunicationError, InvalidRequest, TydomIoError
from tydomio.requests import (
    JsonRequest,
    PingRequest,
    Request,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
    TydomRequest,
)
from tydomio.response import Ok, Response

DEVICES_DATA = b'[{"id": 1, "endpoints": []}]'
AREAS_DATA = b'[{"id": 1, "error": 0, "data": []}]'


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
    ping_interval: int = 30,
    on_connection: tuple[OnConnectionRoutine, ...] = (),
    on_disconnection: tuple[OnDisconnectionRoutine, ...] = (),
    handlers: tuple[TydomRequestHandler, ...] = (),
) -> AsyncTydomClient:
    return AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        ping_interval=ping_interval,
        on_connection_routines=on_connection,
        on_disconnection_routines=on_disconnection,
        tydom_request_handlers=handlers,
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


def ok_reply(sent: bytes) -> bytes:
    """Answer any request with a 200 carrying its transaction id."""
    transac_id = sent.split(b"transac-id: ")[1].split(b"\r\n")[0]
    return b"HTTP/1.1 200 OK\r\ntransac-id: " + transac_id + b"\r\n\r\n"


async def send_once_connected(
    websocket: FakeWebsocket, request: PingRequest, timeout: int | None = None
) -> Ok | CommunicationError:
    """Send the request from a connection routine, give its response or error."""
    outcome: list[Ok | CommunicationError] = []
    done = asyncio.Event()

    async def routine(client: AsyncTydomClient) -> None:
        try:
            outcome.append(await client.send(request, timeout))
        except CommunicationError as error:
            outcome.append(error)
        done.set()

    client = make_client_with_doubles(
        FakeConnectionFactory(websocket), on_connection=(routine,)
    )
    await run_then_cancel(client, done)
    return outcome[0]


async def push_to_handlers(
    *messages: bytes, handlers: tuple[TydomRequestHandler, ...], handled: asyncio.Event
) -> None:
    """Push the messages to a client with the handlers until handled is set."""
    client = make_client_with_doubles(
        FakeConnectionFactory(FakeWebsocket(*messages)), handlers=handlers
    )
    await run_then_cancel(client, handled)


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


async def test_send_sends_the_request() -> None:
    websocket = FakeWebsocket(reply=ok_reply)
    request = PingRequest()

    await send_once_connected(websocket, request)

    assert request.to_bytes(cmd_prefix=b"") in websocket.sent


async def test_send_returns_the_response_with_the_same_transaction_id() -> None:
    request = PingRequest()

    response = await send_once_connected(FakeWebsocket(reply=ok_reply), request)

    assert isinstance(response, Ok)
    assert response.transac_id == request.transaction_id


async def test_send_raises_communication_error_on_timeout() -> None:
    outcome = await send_once_connected(FakeWebsocket(), PingRequest(), timeout=0)

    assert isinstance(outcome, CommunicationError)


async def test_send_raises_communication_error_when_connection_closed() -> None:
    request = PingRequest()

    def close_on_request(sent: bytes) -> None:
        if sent == request.to_bytes(cmd_prefix=b""):
            raise ConnectionClosed(None, None)

    outcome = await send_once_connected(FakeWebsocket(reply=close_on_request), request)

    assert isinstance(outcome, CommunicationError)


@pytest.mark.parametrize(
    ("message", "request_type"),
    [
        (put("/devices/data", DEVICES_DATA), TydomPutDevicesDataRequest),
        (put("/areas/data", AREAS_DATA), TydomPutAreasDataRequest),
    ],
)
async def test_read_loop_routes_pushed_request_to_the_handler_of_its_type(
    message: bytes, request_type: type[TydomRequest]
) -> None:
    received: list[type[TydomRequest]] = []
    handled = asyncio.Event()

    async def devices_handler(request: TydomPutDevicesDataRequest) -> None:
        received.append(type(request))
        handled.set()

    async def areas_handler(request: TydomPutAreasDataRequest) -> None:
        received.append(type(request))
        handled.set()

    await push_to_handlers(
        message, handlers=(devices_handler, areas_handler), handled=handled
    )

    assert received == [request_type]


async def test_read_loop_logs_handler_error(caplog: pytest.LogCaptureFixture) -> None:
    handled = asyncio.Event()

    async def failing_handler(_request: TydomPutDevicesDataRequest) -> None:
        handled.set()
        raise TydomIoError("handler failed")

    with caplog.at_level(logging.ERROR, logger="tydomio.client"):
        await push_to_handlers(
            put("/devices/data", DEVICES_DATA),
            handlers=(failing_handler,),
            handled=handled,
        )

    assert "Error in request handler" in caplog.messages


async def test_read_loop_keeps_reading_after_handler_error() -> None:
    calls: list[None] = []
    second_handled = asyncio.Event()

    async def failing_handler(_request: TydomPutDevicesDataRequest) -> None:
        calls.append(None)
        if len(calls) == 2:
            second_handled.set()
        raise TydomIoError("handler failed")

    await push_to_handlers(
        put("/devices/data", DEVICES_DATA),
        put("/devices/data", DEVICES_DATA),
        handlers=(failing_handler,),
        handled=second_handled,
    )

    assert len(calls) == 2


async def test_ping_sends_a_ping_then_waits_the_ping_interval() -> None:
    sleep = FakeSleep()
    delays_at_each_send: list[int] = []

    def reply(sent: bytes) -> bytes:
        delays_at_each_send.append(len(sleep.delays))
        return ok_reply(sent)

    websocket = FakeWebsocket(reply=reply)
    client = make_client_with_doubles(
        FakeConnectionFactory(websocket), sleep=sleep, ping_interval=7
    )

    await run_then_cancel(client, sleep.called)

    assert websocket.sent[0].startswith(b"GET /ping ")
    assert (delays_at_each_send[0], sleep.delays[0]) == (0, 7)
