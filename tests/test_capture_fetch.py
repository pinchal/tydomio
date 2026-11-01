import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest

from tests.conftest import (
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeSleep,
    FakeWebsocket,
    ScriptExhaustedError,
    answer_gets,
)
from tydomio.capture.fetch import (
    ENDPOINTS,
    ClientStopped,
    EndpointResult,
    Fetcher,
    NeverConnected,
    Outcome,
    _Get,
    _RawResponse,
    run_capture,
)
from tydomio.client import AsyncTydomClient, ConnectionFactory, Sleep
from tydomio.error import CommunicationError
from tydomio.response import Response

URLS = [endpoint.url for endpoint in ENDPOINTS]


class Sender:
    """Answers each GET with {} and 200, or with the scripted response or error."""

    def __init__(self, **answers: _RawResponse | Exception) -> None:
        """Script the answers by endpoint, its url with "_" for "/"."""
        self._answers = answers
        self.urls: list[str] = []

    async def send(self, request: _Get, timeout: int | None = None) -> _RawResponse:
        """Record the url and give its scripted answer."""
        self.urls.append(request.url)
        answer = self._answers.get(request.url.strip("/").replace("/", "_"))
        if isinstance(answer, Exception):
            raise answer
        return answer or raw(200, b"{}")


def raw(status: int, content: bytes | None) -> _RawResponse:
    return _RawResponse(Response(status=status, headers={}, content=content))


def timeout_error() -> CommunicationError:
    error = CommunicationError("Request timeout")
    error.__cause__ = TimeoutError()
    return error


async def fetch(sender: Sender) -> Fetcher:
    fetcher = Fetcher()
    await fetcher(sender)
    return fetcher


async def test_endpoints_are_requested_once_in_order_and_done_is_set() -> None:
    sender = Sender()

    fetcher = await fetch(sender)

    assert (sender.urls, fetcher.done.is_set()) == (URLS, True)


async def test_timeout_is_recorded_and_the_next_endpoints_are_fetched() -> None:
    fetcher = await fetch(Sender(devices_meta=timeout_error()))

    outcomes = [result.outcome for result in fetcher.results.values()]
    assert outcomes == [Outcome.OK, Outcome.TIMEOUT, *[Outcome.OK] * 10]


async def test_other_communication_error_is_recorded_and_ends_the_pass() -> None:
    lost = CommunicationError("Connection closed")
    sender = Sender(devices_cmeta=lost, devices_data=lost, info=lost)
    fetcher = Fetcher()

    with pytest.raises(CommunicationError):
        await fetcher(sender)

    assert (
        sender.urls,
        fetcher.results[URLS[2]].outcome,
        fetcher.done.is_set(),
    ) == (URLS[:3], Outcome.COMMUNICATION_ERROR, False)


async def test_non_200_status_is_recorded_with_its_body_and_not_requested_again() -> (
    None
):
    sender = Sender(info=raw(404, b'{"error": 1}'))
    fetcher = Fetcher()

    await fetcher(sender)
    await fetcher(sender)

    assert (fetcher.results["/info"], sender.urls.count("/info")) == (
        EndpointResult(
            endpoint=ENDPOINTS[-1],
            outcome=Outcome.HTTP_ERROR,
            status=404,
            body={"error": 1},
            body_length=12,
        ),
        1,
    )


async def test_non_json_content_is_recorded_without_body() -> None:
    fetcher = await fetch(Sender(info=raw(200, b"<html>")))

    assert fetcher.results["/info"] == EndpointResult(
        endpoint=ENDPOINTS[-1], outcome=Outcome.NOT_JSON, status=200, body_length=6
    )


async def test_other_errors_propagate_out_of_the_routine() -> None:
    with pytest.raises(ValueError, match="bug"):
        await fetch(Sender(info=ValueError("bug")))


def make_client(
    connection_factory: ConnectionFactory,
    fetcher: Fetcher,
    sleep: Sleep = asyncio.sleep,
) -> AsyncTydomClient:
    return AsyncTydomClient(
        tydom_password="",
        tydom_ip="192.0.2.1",
        tydom_mac="000000000000",
        on_connection_routines=(fetcher,),
        authorization_provider=FakeAuthorizationProvider(),
        connection_factory=connection_factory,
        sleep=sleep,
    )


def sent_urls(websocket: FakeWebsocket) -> list[str]:
    urls = [message.split(b" ")[1].decode() for message in websocket.sent]
    return [url for url in urls if url != "/ping"]


async def test_a_new_connection_requests_only_endpoints_without_a_final_result() -> (
    None
):
    answers = {url: (200, b"{}") for url in URLS}
    first_reply = answer_gets(answers)

    def lose_connection_on_the_second(sent: bytes) -> bytes | None:
        if sent.startswith(f"GET {URLS[1]} ".encode()):
            raise CommunicationError("Connection closed")
        return first_reply(sent)

    first = FakeWebsocket(reply=lose_connection_on_the_second)
    second = FakeWebsocket(reply=answer_gets(answers))
    fetcher = Fetcher()

    await run_capture(
        make_client(FakeConnectionFactory(first, second), fetcher, FakeSleep()),
        fetcher,
        deadline=5,
    )

    assert sent_urls(second) == URLS[1:]


async def test_run_capture_raises_client_stopped_when_the_client_stops() -> None:
    fetcher = Fetcher()

    with pytest.raises(ClientStopped) as raised:
        await run_capture(
            make_client(FakeConnectionFactory(), fetcher), fetcher, deadline=5
        )

    assert isinstance(raised.value.__cause__, ScriptExhaustedError)


class RefusingConnectionFactory:
    """Fails every connection with a network error."""

    @asynccontextmanager
    async def __call__(self, authorization: str) -> AsyncGenerator[FakeWebsocket]:
        """Raise a network error."""
        raise OSError("unreachable")
        yield FakeWebsocket()


async def yield_once(_delay: float) -> None:
    await asyncio.sleep(0)


async def test_run_capture_raises_never_connected_at_the_deadline() -> None:
    fetcher = Fetcher()
    client = make_client(RefusingConnectionFactory(), fetcher, yield_once)

    with pytest.raises(NeverConnected):
        await run_capture(client, fetcher, deadline=0.05)


async def test_deadline_after_connection_gives_the_results_and_not_fetched() -> None:
    answers = {url: (200, b"{}") for url in URLS[:2]}
    fetcher = Fetcher(request_timeout=60)
    websocket = FakeWebsocket(reply=answer_gets(answers))

    results = await run_capture(
        make_client(FakeConnectionFactory(websocket), fetcher), fetcher, deadline=0.2
    )

    assert [result.outcome for result in results.values()] == [
        Outcome.OK,
        Outcome.OK,
        *[Outcome.NOT_FETCHED] * 10,
    ]
