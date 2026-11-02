import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

import pydantic
import pytest
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed, InvalidMessage, InvalidStatus
from websockets.http11 import Response as HandshakeResponse

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
)
from tydomio.error import AuthorizationError, CommunicationError, TydomIoError
from tydomio.installation import EndpointRef
from tydomio.model.usages.shutter import Shutter, ShutterWrite
from tydomio.requests import (
    ClientRequest,
    GetDevicesData,
    PingRequest,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
    TydomRequest,
    WriteEndpoint,
)
from tydomio.response import Ok

DEVICES_DATA = b'[{"id": 1, "endpoints": []}]'
AREAS_DATA = b'[{"id": 1, "error": 0, "data": []}]'


def make_client() -> AsyncTydomClient:
    return AsyncTydomClient(
        tydom_password="", tydom_ip="192.0.2.1", tydom_mac="000000000000"
    )


def make_client_with_doubles(
    connection_factory: FakeConnectionFactory,
    *,
    authorization_provider: FakeAuthorizationProvider | None = None,
    sleep: FakeSleep | None = None,
    ping_interval: int = 30,
    on_connection: tuple[OnConnectionRoutine, ...] = (),
    on_disconnection: tuple[OnDisconnectionRoutine, ...] = (),
) -> AsyncTydomClient:
    return AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        ping_interval=ping_interval,
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
    run = asyncio.create_task(client.run_websocket())
    wait_reached = asyncio.create_task(reached.wait())
    await asyncio.wait((run, wait_reached), return_when=asyncio.FIRST_COMPLETED)
    wait_reached.cancel()
    run.cancel()
    await run


def ok_reply(sent: bytes) -> bytes:
    """Answer any request with a 200 carrying its transaction id."""
    transac_id = sent.split(b"transac-id: ")[1].split(b"\r\n")[0]
    return b"HTTP/1.1 200 OK\r\ntransac-id: " + transac_id + b"\r\n\r\n"


async def send_once_connected(
    websocket: FakeWebsocket,
    request: ClientRequest[Ok],
    timeout: int | None = None,
    send: Callable[[AsyncTydomClient, ClientRequest[Ok]], Awaitable[Ok]] | None = None,
) -> Ok | TydomIoError:
    """Send the request from a connection routine, give its response or error.

    :param send: sends the request, client.send with the timeout by default
    """
    outcome: list[Ok | TydomIoError] = []
    done = asyncio.Event()

    async def routine(client: AsyncTydomClient) -> None:
        try:
            if send is None:
                outcome.append(await client.send(request, timeout))
            else:
                outcome.append(await send(client, request))
        except TydomIoError as error:
            outcome.append(error)
        done.set()

    client = make_client_with_doubles(
        FakeConnectionFactory(websocket), on_connection=(routine,)
    )
    await run_then_cancel(client, done)
    return outcome[0]


def client_pushing(*messages: bytes) -> AsyncTydomClient:
    """Give a client whose websocket pushes the messages."""
    return make_client_with_doubles(FakeConnectionFactory(FakeWebsocket(*messages)))


async def push_to_handler(
    *messages: bytes,
    handler: TydomRequestHandler[TydomPutDevicesDataRequest],
    handled: asyncio.Event,
) -> None:
    """Push the messages to a client with the handler until handled is set."""
    client = client_pushing(*messages)
    client.subscribe(TydomPutDevicesDataRequest, handler)
    await run_then_cancel(client, handled)


class Recorder:
    """Handler recording the requests, sets handled once it has the expected number."""

    def __init__(self, expected: int = 1) -> None:
        """Expect that number of requests."""
        self._expected = expected
        self.received: list[TydomRequest[pydantic.BaseModel]] = []
        self.handled = asyncio.Event()

    async def __call__(self, request: TydomRequest[pydantic.BaseModel]) -> None:
        """Record the request."""
        self.received.append(request)
        if len(self.received) >= self._expected:
            self.handled.set()


async def test_send_rejects_request_without_transaction_id() -> None:
    request = PingRequest()
    request.transaction_id = None

    with pytest.raises(TydomIoError):
        await make_client().send(request)


async def test_send_requires_connection() -> None:
    with pytest.raises(CommunicationError):
        await make_client().send(PingRequest())


async def test_write_refused_when_not_connected_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG), pytest.raises(CommunicationError):
        await make_client().send(shutter_write())

    assert not [r for r in caplog.records if r.name == "tydomio.requests"]


async def test_run_websocket_retries_authorization_after_a_pause_on_failure() -> None:
    authorization_provider = FakeAuthorizationProvider(AuthorizationError("failed"))
    sleep = FakeSleep()
    client = make_client_with_doubles(
        FakeConnectionFactory(),
        authorization_provider=authorization_provider,
        sleep=sleep,
    )

    await run_until_script_ends(client)

    assert (sleep.delays, authorization_provider.calls) == ([5], 2)


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError("refused"),
        TimeoutError("timeout"),
        InvalidMessage("did not receive a valid HTTP response"),
    ],
)
async def test_run_websocket_retries_after_a_pause_when_the_websocket_fails_to_open(
    error: Exception,
) -> None:
    connection_factory = FakeConnectionFactory(error)
    sleep = FakeSleep()
    client = make_client_with_doubles(connection_factory, sleep=sleep)

    await run_until_script_ends(client)

    assert (sleep.delays, len(connection_factory.authorizations)) == ([5], 2)


async def test_run_websocket_retries_after_a_pause_when_authentication_refused() -> (
    None
):
    refused = InvalidStatus(HandshakeResponse(401, "Unauthorized", Headers()))
    connection_factory = FakeConnectionFactory(refused)
    sleep = FakeSleep()
    client = make_client_with_doubles(connection_factory, sleep=sleep)

    await run_until_script_ends(client)

    assert (sleep.delays, len(connection_factory.authorizations)) == ([5], 2)


async def test_run_websocket_fails_pending_requests_when_connection_lost() -> None:
    request = PingRequest()
    sent: list[bytes] = []

    def reply(message: bytes) -> bytes | None:
        sent.append(message)
        if message == request.to_bytes(cmd_prefix=b""):
            return None  # the response never comes
        if request.to_bytes(cmd_prefix=b"") in sent:
            raise ConnectionClosed(None, None)
        return ok_reply(message)

    connection_factory = FakeConnectionFactory(FakeWebsocket(reply=reply))
    client = make_client_with_doubles(connection_factory)
    run = asyncio.create_task(run_until_script_ends(client))
    await connection_factory.connected.wait()

    # well before the request timeout
    async with asyncio.timeout(1):
        with pytest.raises(CommunicationError):
            await client.send(request)
    await run


async def test_run_websocket_raises_os_error_of_a_connection_routine() -> None:
    async def routine(_client: AsyncTydomClient) -> None:
        raise OSError("routine failed")

    client = make_client_with_doubles(
        FakeConnectionFactory(FakeWebsocket(), FakeWebsocket()),
        on_connection=(routine,),
    )

    with pytest.raises(ExceptionGroup) as raised:
        await client.run_websocket()

    assert raised.group_contains(OSError)


async def test_run_websocket_reconnects_when_the_tydom_closes_the_connection() -> None:
    connection_factory = FakeConnectionFactory(
        FakeWebsocket(closed_by_tydom=True), FakeWebsocket(closed_by_tydom=True)
    )
    # number of connections opened when each disconnection routine runs
    disconnections: list[int] = []

    async def routine() -> None:
        disconnections.append(len(connection_factory.authorizations))

    client = make_client_with_doubles(connection_factory, on_disconnection=(routine,))

    # well before the timeout of the unanswered ping
    async with asyncio.timeout(1):
        await run_until_script_ends(client)

    assert disconnections == [1, 2]


async def test_wait_unit_connected_returns_once_connected() -> None:
    client = make_client_with_doubles(FakeConnectionFactory(FakeWebsocket()))
    run = asyncio.create_task(client.run_websocket())

    async with asyncio.timeout(1):
        await client.wait_unit_connected()
    run.cancel()
    await run


async def test_wait_unit_connected_waits_again_once_disconnected() -> None:
    client = make_client_with_doubles(FakeConnectionFactory(FakeWebsocket()))
    run = asyncio.create_task(client.run_websocket())
    async with asyncio.timeout(1):
        await client.wait_unit_connected()
    run.cancel()
    await run

    waiting = asyncio.create_task(client.wait_unit_connected())
    await asyncio.sleep(0)

    assert not waiting.done()
    waiting.cancel()


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


async def test_send_accepts_again_a_request_whose_sending_failed() -> None:
    request = PingRequest()
    failures: list[None] = []

    def close_on_first_request(sent: bytes) -> bytes:
        if sent == request.to_bytes(cmd_prefix=b"") and not failures:
            failures.append(None)
            raise ConnectionClosed(None, None)
        return ok_reply(sent)

    async def send_twice(client: AsyncTydomClient, request: ClientRequest[Ok]) -> Ok:
        with suppress(CommunicationError):
            await client.send(request)
        return await client.send(request)

    outcome = await send_once_connected(
        FakeWebsocket(reply=close_on_first_request), request, send=send_twice
    )

    assert isinstance(outcome, Ok)


async def test_send_raises_communication_error_when_connection_closed() -> None:
    request = PingRequest()

    def close_on_request(sent: bytes) -> None:
        if sent == request.to_bytes(cmd_prefix=b""):
            raise ConnectionClosed(None, None)

    outcome = await send_once_connected(FakeWebsocket(reply=close_on_request), request)

    assert isinstance(outcome, CommunicationError)


async def start_unanswered_send(
    request: PingRequest,
) -> tuple[AsyncTydomClient, FakeWebsocket, asyncio.Task[None], asyncio.Task[Ok]]:
    """Connect a client and send it the request, which gets no response.

    Gives the client, its websocket, the running connection and the sending, once
    the request is sent.
    """
    request_sent = asyncio.Event()

    def reply(message: bytes) -> bytes | None:
        if message == request.to_bytes(cmd_prefix=b""):
            request_sent.set()
            return None
        return ok_reply(message)

    websocket = FakeWebsocket(reply=reply)
    connection_factory = FakeConnectionFactory(websocket)
    client = make_client_with_doubles(connection_factory)
    run = asyncio.create_task(run_until_script_ends(client))
    await connection_factory.connected.wait()
    sending = asyncio.create_task(client.send(request))
    await request_sent.wait()
    return client, websocket, run, sending


async def test_send_cancelled_while_waiting_leaves_no_pending_request() -> None:
    request = PingRequest()
    client, _websocket, run, sending = await start_unanswered_send(request)

    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending

    assert client.get_number_of_requests_in_progress() == 0
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)


async def test_response_after_cancelled_send_is_dropped_and_reading_goes_on() -> None:
    request = PingRequest()
    client, websocket, run, sending = await start_unanswered_send(request)
    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending

    websocket.push(ok_reply(request.to_bytes(cmd_prefix=b"")))
    async with asyncio.timeout(1):
        response = await client.send(PingRequest())

    assert isinstance(response, Ok)
    assert not run.done()
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)


@pytest.mark.parametrize(
    ("message", "request_type"),
    [
        (put("/devices/data", DEVICES_DATA), TydomPutDevicesDataRequest),
        (put("/areas/data", AREAS_DATA), TydomPutAreasDataRequest),
    ],
)
async def test_subscriber_receives_pushed_request_of_its_type(
    message: bytes, request_type: type[TydomRequest[pydantic.BaseModel]]
) -> None:
    recorder = Recorder()
    client = client_pushing(message)
    client.subscribe(request_type, recorder)

    await run_then_cancel(client, recorder.handled)

    assert [type(request) for request in recorder.received] == [request_type]


async def test_subscriber_receives_pushed_requests_of_subclasses() -> None:
    recorder = Recorder(expected=2)
    client = client_pushing(
        put("/devices/data", DEVICES_DATA), put("/areas/data", AREAS_DATA)
    )
    client.subscribe(TydomRequest, recorder)

    await run_then_cancel(client, recorder.handled)

    assert [type(request) for request in recorder.received] == [
        TydomPutDevicesDataRequest,
        TydomPutAreasDataRequest,
    ]


async def test_subscriber_does_not_receive_pushed_request_of_other_type() -> None:
    recorder = Recorder()
    client = client_pushing(
        put("/areas/data", AREAS_DATA), put("/devices/data", DEVICES_DATA)
    )
    client.subscribe(TydomPutDevicesDataRequest, recorder)

    await run_then_cancel(client, recorder.handled)

    assert [type(request) for request in recorder.received] == [
        TydomPutDevicesDataRequest
    ]


@pytest.mark.parametrize(
    "make_handler",
    [
        lambda recorder: recorder,
        lambda recorder: functools.partial(Recorder.__call__, recorder),
        lambda recorder: lambda request: recorder(request=request),
    ],
    ids=["callable-object", "partial", "lambda"],
)
async def test_subscribe_accepts_any_callable_handler(
    make_handler: Callable[
        [Recorder], Callable[[TydomPutDevicesDataRequest], Awaitable[None]]
    ],
) -> None:
    recorder = Recorder()
    client = client_pushing(put("/devices/data", DEVICES_DATA))
    client.subscribe(TydomPutDevicesDataRequest, make_handler(recorder))

    await run_then_cancel(client, recorder.handled)

    assert len(recorder.received) == 1


async def test_each_subscriber_of_a_type_receives_the_pushed_request() -> None:
    first, second = Recorder(), Recorder()
    client = client_pushing(put("/devices/data", DEVICES_DATA))
    client.subscribe(TydomPutDevicesDataRequest, first)
    client.subscribe(TydomPutDevicesDataRequest, second)

    await run_then_cancel(client, second.handled)

    assert (len(first.received), len(second.received)) == (1, 1)


async def test_unsubscribed_handler_receives_no_pushed_request() -> None:
    unsubscribed, subscribed = Recorder(), Recorder()
    client = client_pushing(put("/devices/data", DEVICES_DATA))
    unsubscribe = client.subscribe(TydomPutDevicesDataRequest, unsubscribed)
    client.subscribe(TydomPutDevicesDataRequest, subscribed)

    unsubscribe()
    await run_then_cancel(client, subscribed.handled)

    assert not unsubscribed.received


async def test_unsubscribing_twice_keeps_the_other_subscriptions() -> None:
    recorder = Recorder()
    client = client_pushing(put("/devices/data", DEVICES_DATA))
    unsubscribe = client.subscribe(TydomPutDevicesDataRequest, recorder)
    client.subscribe(TydomPutDevicesDataRequest, recorder)

    unsubscribe()
    unsubscribe()
    # well before the timeout of the unanswered ping
    async with asyncio.timeout(1):
        await run_then_cancel(client, recorder.handled)

    assert len(recorder.received) == 1


async def test_read_loop_logs_handler_error(caplog: pytest.LogCaptureFixture) -> None:
    handled = asyncio.Event()

    async def failing_handler(_request: TydomPutDevicesDataRequest) -> None:
        handled.set()
        raise TydomIoError("handler failed")

    with caplog.at_level(logging.ERROR, logger="tydomio.client"):
        await push_to_handler(
            put("/devices/data", DEVICES_DATA),
            handler=failing_handler,
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

    await push_to_handler(
        put("/devices/data", DEVICES_DATA),
        put("/devices/data", DEVICES_DATA),
        handler=failing_handler,
        handled=second_handled,
    )

    assert len(calls) == 2


async def test_read_loop_logs_synchronous_handler_error_and_keeps_handling(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = Recorder()
    calls: list[None] = []

    def raising_on_first_call(request: TydomPutDevicesDataRequest) -> Awaitable[None]:
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("handler failed before returning an awaitable")
        return recorder(request)

    with caplog.at_level(logging.ERROR, logger="tydomio.client"):
        await push_to_handler(
            put("/devices/data", DEVICES_DATA),
            put("/devices/data", DEVICES_DATA),
            handler=raising_on_first_call,
            handled=recorder.handled,
        )

    assert ("Error in request handler" in caplog.messages, len(recorder.received)) == (
        True,
        1,
    )


@pytest.mark.parametrize(
    "malformed",
    [
        put("/other", b"not json"),
        b"PUT /a HTTP/1.1\r\nDate: Sat, 31 Oct 2026 10:00:00 GMT\r\n\r\n",
        b"PUT /a HTTP/1.1\r\nno colon\r\n\r\n",
        b"HTTP/1.1 abc OK\r\n\r\n",
        b"HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\nzz\r\n",
    ],
)
async def test_read_loop_keeps_reading_after_malformed_message(
    malformed: bytes,
) -> None:
    handled = asyncio.Event()

    async def handler(_request: TydomPutDevicesDataRequest) -> None:
        handled.set()

    # well before the timeout of the unanswered ping
    async with asyncio.timeout(1):
        await push_to_handler(
            malformed,
            put("/devices/data", DEVICES_DATA),
            handler=handler,
            handled=handled,
        )

    assert handled.is_set()


@pytest.mark.parametrize(
    "message",
    [
        put("/other", b"marker"),
        b"HTTP/1.1 200 OK\r\ntransac-id: 0\r\nname: marker\r\n\r\nmarker",
    ],
    ids=["malformed", "unexpected-response"],
)
async def test_read_loop_logs_no_content_above_debug(
    message: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    handled = asyncio.Event()

    async def handler(_request: TydomPutDevicesDataRequest) -> None:
        handled.set()

    with caplog.at_level(logging.DEBUG):
        async with asyncio.timeout(1):
            await push_to_handler(
                message,
                put("/devices/data", DEVICES_DATA),
                handler=handler,
                handled=handled,
            )

    formatter = logging.Formatter()
    above_debug = [r for r in caplog.records if r.levelno > logging.DEBUG]
    assert not any("marker" in formatter.format(r) for r in above_debug)


async def test_handler_receives_the_response_to_its_request() -> None:
    responses: list[Ok] = []
    handled = asyncio.Event()

    async def handler(_request: TydomPutDevicesDataRequest) -> None:
        responses.append(await client.send(PingRequest()))
        handled.set()

    client = make_client_with_doubles(
        FakeConnectionFactory(
            FakeWebsocket(put("/devices/data", DEVICES_DATA), reply=ok_reply)
        )
    )
    client.subscribe(TydomPutDevicesDataRequest, handler)

    # well before the request timeout
    async with asyncio.timeout(1):
        await run_then_cancel(client, handled)

    assert isinstance(responses[0], Ok)


async def test_run_websocket_cancels_running_handlers_on_disconnection() -> None:
    started = asyncio.Event()
    cancelled: list[None] = []

    async def handler(_request: TydomPutDevicesDataRequest) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(None)
            raise

    await push_to_handler(
        put("/devices/data", DEVICES_DATA), handler=handler, handled=started
    )

    assert cancelled == [None]


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


def shutter_write() -> WriteEndpoint[Shutter]:
    return WriteEndpoint(EndpointRef(10, 1, Shutter), ShutterWrite(position=42))


def start_lines(*websockets: FakeWebsocket) -> list[bytes]:
    return [sent.split(b"\r\n")[0] for ws in websockets for sent in ws.sent]


async def test_connection_reconnection_and_pushes_send_nothing_but_pings() -> None:
    websockets = [
        FakeWebsocket(
            put("/devices/data", DEVICES_DATA), reply=ok_reply, closed_by_tydom=True
        )
        for _ in range(2)
    ]
    client = make_client_with_doubles(FakeConnectionFactory(*websockets))
    client.subscribe(TydomRequest, Recorder())

    async with asyncio.timeout(1):
        await run_until_script_ends(client)

    assert set(start_lines(*websockets)) == {b"GET /ping HTTP/1.1"}


async def test_sent_write_logs_its_field_names_without_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    websocket = FakeWebsocket(reply=ok_reply)

    with caplog.at_level(logging.DEBUG):
        await send_once_connected(websocket, shutter_write())

    above_debug = [r.getMessage() for r in caplog.records if r.levelno > logging.DEBUG]
    assert "Write to a Shutter: position" in above_debug
    assert not any("42" in message for message in above_debug)
    assert any(
        "42" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.DEBUG and r.name == "tydomio.requests"
    )


async def test_write_lost_with_the_connection_is_not_resent() -> None:
    def lose_after_write(sent: bytes) -> bytes | None:
        if sent.startswith(b"PUT "):
            return None  # the acknowledgement never comes
        if any(message.startswith(b"PUT ") for message in first.sent):
            raise ConnectionClosed(None, None)
        return ok_reply(sent)

    first = FakeWebsocket(reply=lose_after_write)
    second = FakeWebsocket(reply=ok_reply, closed_by_tydom=True)
    connection_factory = FakeConnectionFactory(first, second)
    client = make_client_with_doubles(connection_factory)
    run = asyncio.create_task(run_until_script_ends(client))
    await connection_factory.connected.wait()

    async with asyncio.timeout(1):
        with pytest.raises(CommunicationError):
            await client.send(shutter_write())
        await run

    assert [line for line in start_lines(first, second) if b"PUT" in line] == [
        b"PUT /devices/10/endpoints/1/data HTTP/1.1"
    ]


async def test_write_timeout_raises_after_a_single_request() -> None:
    def ignore_writes(sent: bytes) -> bytes | None:
        return None if sent.startswith(b"PUT ") else ok_reply(sent)

    websocket = FakeWebsocket(reply=ignore_writes)

    outcome = await send_once_connected(websocket, shutter_write(), timeout=0)

    assert isinstance(outcome, CommunicationError)
    assert sum(line.startswith(b"PUT ") for line in start_lines(websocket)) == 1


def devices_data_response(sent: bytes) -> bytes:
    transac_id = sent.split(b"transac-id: ")[1].split(b"\r\n")[0]
    return (
        b"HTTP/1.1 200 OK\r\ntransac-id: "
        + transac_id
        + b"\r\nuri-origin: /devices/data\r\n"
        + b"content-type: application/json\r\n\r\n"
        + DEVICES_DATA
    )


async def events_of_get_with_push(*, push_first: bool) -> list[str]:
    """Order of a push handler and the sender of a GET, both buffered in one read."""
    events: list[str] = []
    done = asyncio.Event()
    push = put("/devices/data", DEVICES_DATA)

    def reply(sent: bytes) -> bytes | None:
        if not sent.startswith(b"GET /devices/data"):
            return None
        first, second = (
            (push, devices_data_response(sent))
            if push_first
            else (devices_data_response(sent), push)
        )
        websocket.push(first)
        return second

    websocket = FakeWebsocket(reply=reply)

    async def handler(_request: TydomPutDevicesDataRequest) -> None:
        events.append("push")  # no await before acting, as the contract requires

    async def routine(client: AsyncTydomClient) -> None:
        await client.send(GetDevicesData())
        events.append("response")
        done.set()

    client = make_client_with_doubles(
        FakeConnectionFactory(websocket), on_connection=(routine,)
    )
    client.subscribe(TydomPutDevicesDataRequest, handler)
    await run_then_cancel(client, done)
    # let a handler started after the response run
    await asyncio.sleep(0)
    return events


async def test_push_read_before_a_response_is_handled_before_send_returns() -> None:
    assert await events_of_get_with_push(push_first=True) == ["push", "response"]


async def test_push_read_after_a_response_is_handled_after_send_returns() -> None:
    assert await events_of_get_with_push(push_first=False) == ["response", "push"]
