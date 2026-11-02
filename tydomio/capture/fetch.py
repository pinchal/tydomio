"""Read-only fetching of the captured endpoints over an AsyncTydomClient."""

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol

from ..client import AsyncTydomClient
from ..error import CommunicationError, TydomIoError
from ..requests import ClientRequest
from ..response import Response
from .jsonvalue import JsonValue, NotJson, parse_json

LOGGER = logging.getLogger(__name__)

_STATUS_OK: Final = 200


@dataclass(frozen=True)
class Endpoint:
    """A captured endpoint; its file name is a constant, never built from data."""

    url: str
    file_name: str


# /configs/file first: it defines the ids the other endpoints reference
ENDPOINTS: Final[tuple[Endpoint, ...]] = (
    Endpoint("/configs/file", "configs_file.json"),
    Endpoint("/devices/meta", "devices_meta.json"),
    Endpoint("/devices/cmeta", "devices_cmeta.json"),
    Endpoint("/devices/data", "devices_data.json"),
    Endpoint("/areas/meta", "areas_meta.json"),
    Endpoint("/areas/cmeta", "areas_cmeta.json"),
    Endpoint("/areas/data", "areas_data.json"),
    Endpoint("/groups/file", "groups_file.json"),
    Endpoint("/scenarios/file", "scenarios_file.json"),
    Endpoint("/moments/file", "moments_file.json"),
    Endpoint("/protocols", "protocols.json"),
    Endpoint("/info", "info.json"),
)

REQUEST_TIMEOUT: Final = 10  # seconds, per GET
CONNECT_BUDGET: Final = 60  # seconds, authorization plus websocket opening
# the 20 s margin covers the digest exchange
DEADLINE: Final = CONNECT_BUDGET + len(ENDPOINTS) * REQUEST_TIMEOUT + 20


class _RawResponse(Response):
    """Any status, raw bytes; not a JsonResponse, which needs JSON content."""

    def __init__(self, response: Response) -> None:
        """Keep the response as received."""
        super().__init__(
            status=response.status, headers=response.headers, content=response.content
        )


class _Get(ClientRequest[_RawResponse]):
    """GET of a listed endpoint; no free URL nor method can reach it."""

    response_type = _RawResponse

    def __init__(self, endpoint: Endpoint) -> None:
        """Build the GET of the endpoint."""
        super().__init__(method="GET", url=endpoint.url)


class Outcome(StrEnum):
    """The only outcome values written anywhere."""

    OK = "ok"
    HTTP_ERROR = "http-error"
    NOT_JSON = "not-json"
    TIMEOUT = "timeout"
    COMMUNICATION_ERROR = "communication-error"
    NOT_FETCHED = "not-fetched"


@dataclass(frozen=True, kw_only=True)
class EndpointResult:
    """What a GET gave; body_length is set whenever a body was received."""

    endpoint: Endpoint
    outcome: Outcome
    status: int | None = None
    body: JsonValue | None = None
    body_length: int | None = None


class Sender(Protocol):
    """The only part of AsyncTydomClient the Fetcher uses."""

    async def send(self, request: _Get, timeout: int | None = None) -> _RawResponse:
        """Send the request and give its response."""
        ...


class ClientStopped(TydomIoError):
    """run_websocket returned or raised before the capture ended."""


class NeverConnected(TydomIoError):
    """The deadline passed before any connection."""


class Fetcher:
    """On-connection routine fetching the endpoints, resumed on each connection."""

    def __init__(self, *, request_timeout: int = REQUEST_TIMEOUT) -> None:
        """Start with no result."""
        self._request_timeout = request_timeout
        self._results: dict[str, EndpointResult] = {}
        self.connected = False
        self.done = asyncio.Event()

    @property
    def results(self) -> Mapping[str, EndpointResult]:
        """The endpoints with a recorded result."""
        return MappingProxyType(self._results)

    async def __call__(self, client: Sender) -> None:
        """Make one pass, in table order, over the endpoints still to fetch.

        A communication error ends the pass without setting done: the client
        reconnects and the next pass requests that endpoint again.
        """
        self.connected = True
        for endpoint in ENDPOINTS:
            previous = self._results.get(endpoint.url)
            if previous is not None and previous.outcome != Outcome.COMMUNICATION_ERROR:
                continue
            result = await self._fetch(client, endpoint)
            self._results[endpoint.url] = result
            LOGGER.info(
                "GET %s: %s, %s, %s bytes",
                endpoint.url,
                result.outcome,
                result.status,
                result.body_length,
            )
            if result.outcome == Outcome.COMMUNICATION_ERROR:
                raise CommunicationError("Connection lost during the capture")
        self.done.set()

    async def _fetch(self, client: Sender, endpoint: Endpoint) -> EndpointResult:
        try:
            response = await client.send(_Get(endpoint), timeout=self._request_timeout)
        except CommunicationError as error:
            # client.send raises it from the TimeoutError; no message is compared
            timed_out = isinstance(error.__cause__, TimeoutError)
            return EndpointResult(
                endpoint=endpoint,
                outcome=Outcome.TIMEOUT if timed_out else Outcome.COMMUNICATION_ERROR,
            )
        content = response.content or b""
        try:
            body = parse_json(content)
        except NotJson:
            body = None
            outcome = Outcome.NOT_JSON
        else:
            outcome = Outcome.OK
        if response.status != _STATUS_OK:
            outcome = Outcome.HTTP_ERROR
        return EndpointResult(
            endpoint=endpoint,
            outcome=outcome,
            status=response.status,
            body=body,
            body_length=len(content),
        )


async def run_capture(
    client: AsyncTydomClient, fetcher: Fetcher, *, deadline: float = DEADLINE
) -> Mapping[str, EndpointResult]:
    """Run the client until the fetcher's pass ends or the deadline passes.

    Return every endpoint, NOT_FETCHED when never recorded. Raise ClientStopped,
    with the cause, when run_websocket ends first, and NeverConnected when the
    deadline passes with no connection.
    """
    run_task = asyncio.create_task(client.run_websocket())
    done_task = asyncio.create_task(fetcher.done.wait())
    finished, _ = await asyncio.wait(
        {run_task, done_task}, timeout=deadline, return_when=asyncio.FIRST_COMPLETED
    )
    done_task.cancel()
    if run_task in finished:
        raise ClientStopped("The client stopped before the capture ended") from (
            run_task.exception()
        )
    run_task.cancel()
    # run_websocket re-raises the cancellation from its retry sleeps
    with suppress(asyncio.CancelledError):
        await run_task
    if not fetcher.connected:
        raise NeverConnected(
            "Never connected before the deadline:"
            " check the password, the IP, the MAC address and the network"
        )
    results = fetcher.results
    return {
        endpoint.url: results.get(endpoint.url)
        or EndpointResult(endpoint=endpoint, outcome=Outcome.NOT_FETCHED)
        for endpoint in ENDPOINTS
    }
