"""Client implementation."""

import asyncio.exceptions
import functools
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any, Protocol

import pydantic
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from . import network
from .error import (
    AuthorizationError,
    CommunicationError,
    InvalidRequest,
    InvalidResponse,
    TydomIoError,
)
from .pending_requests import PendingRequests
from .protocol import parse_message
from .requests import ClientRequest, PingRequest, TydomRequest
from .response import ParsedResponse, Response

CLIENT_LOGGER = logging.getLogger("tydomio.client")
REQUEST_LOGGER = CLIENT_LOGGER.getChild("request")
RESPONSE_LOGGER = CLIENT_LOGGER.getChild("response")

OnConnectionRoutine = Callable[["AsyncTydomClient"], Coroutine[Any, Any, None]]
OnDisconnectionRoutine = Callable[[], Coroutine[Any, Any, None]]
type TydomRequestHandler[HandledT: TydomRequest[pydantic.BaseModel]] = Callable[
    [HandledT], Awaitable[None]
]
# gives the handling of a pushed request, or None when not subscribed to its type
_Subscription = Callable[
    [TydomRequest[pydantic.BaseModel]], Callable[[], Awaitable[None]] | None
]


class WebsocketConnection(Protocol):
    """What the client uses of a websocket connection."""

    async def send(self, message: bytes, /) -> None:
        """Send a binary message."""

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        """Iterate over the received messages until the connection closes."""

    async def close(self) -> None:
        """Close the connection."""


# returns the Authorization header; signals any failure by raising AuthorizationError
AuthorizationProvider = Callable[[], Awaitable[str]]
ConnectionFactory = Callable[[str], AbstractAsyncContextManager[WebsocketConnection]]
Sleep = Callable[[float], Awaitable[None]]


class AsyncTydomClient:
    """Asynchronous client for Tydom.

    The authentication process is heavily inspired by tydom2mqtt
    - https://github.com/fmartinou/tydom2mqtt


    """

    def __init__(
        self,
        *,
        tydom_password: str,
        tydom_ip: str | None,
        tydom_mac: str,
        deltadore_remote: str = "mediation.tydom.com",
        default_timeout: int = 5,
        ping_interval: int = 30,
        on_connection_routines: tuple[OnConnectionRoutine, ...] = (),
        on_disconnection_routines: tuple[OnDisconnectionRoutine, ...] = (),
        authorization_provider: AuthorizationProvider | None = None,
        connection_factory: ConnectionFactory | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        """AsyncTydomClient constructor.

        :param tydom_password: Tydom password,
        It can be retrieved using 'tydomio-cli get-tydom-password'
        :param tydom_ip: The local access to your Tydom (optional)
        :param tydom_mac: The mac address of your Tydom
        :param deltadore_remote: hostname for remote access, used when no ip is provided
        :param authorization_provider: gets the authorization header, from the Tydom
        by default
        :param connection_factory: opens the websocket with an authorization, on the
        Tydom by default
        :param sleep: waits between reconnections and pings, asyncio.sleep by default
        """
        self._tydom_password = tydom_password
        self._tydom_ip = tydom_ip
        self._tydom_mac = tydom_mac

        self._local_access = self._tydom_ip is not None
        self._tydom_target = tydom_ip if self._local_access else deltadore_remote
        self._cmd_prefix = b"" if self._local_access else b"\x02"
        self._ssl_context = network.tydom_ssl_context(local_access=self._local_access)
        self._authorization_provider: AuthorizationProvider = (
            authorization_provider
            or functools.partial(
                network.get_authorization,
                url=f"https://{self._tydom_target}/mediation/client?mac={self._tydom_mac}",
                username=self._tydom_mac,
                password=self._tydom_password,
                ssl_context=self._ssl_context,
            )
        )
        self._connection_factory: ConnectionFactory = (
            connection_factory
            or functools.partial(
                network.connect_websocket,
                uri=f"wss://{self._tydom_target}:443"
                f"/mediation/client?mac={self._tydom_mac}&appli=1",
                ssl_context=self._ssl_context,
                close_timeout=default_timeout,
            )
        )
        self._sleep = sleep

        self._websocket: WebsocketConnection | None = None
        self._pending_requests = PendingRequests()
        self._connected = asyncio.Event()

        self._default_timeout = default_timeout
        self._ping_interval = ping_interval

        self._on_connection_routines = on_connection_routines
        self._on_disconnection_routines = on_disconnection_routines

        self._subscriptions: list[_Subscription] = []

    def subscribe[RequestT: TydomRequest[pydantic.BaseModel]](
        self, request_type: type[RequestT], handler: TydomRequestHandler[RequestT]
    ) -> Callable[[], None]:
        """Call the handler with each pushed request of request_type or a subclass.

        Subscribing to TydomRequest receives every pushed request. Each subscription
        is a separate registration: a handler subscribed twice, or to two types that
        both match a request, is called once per matching subscription. Subscribing
        is allowed before or while run_websocket runs. Handlers run as tasks of the
        connection; their RuntimeError is logged without ending the connection.

        :param request_type: type of the pushed requests to receive
        :param handler: coroutine function called with each matching request
        :return: unsubscribe, removing this subscription; calling it again is harmless
        """

        def subscription(
            request: TydomRequest[pydantic.BaseModel],
        ) -> Callable[[], Awaitable[None]] | None:
            # the handler is called in its task, so its errors never stop the reading
            if isinstance(request, request_type):
                return functools.partial(handler, request)
            return None

        self._subscriptions.append(subscription)

        def unsubscribe() -> None:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

        return unsubscribe

    async def _get_authorization(self) -> str:
        CLIENT_LOGGER.info("Get authorization from Tydom")
        return await self._authorization_provider()

    async def run_websocket(self) -> None:
        """Authenticate initiate the connection on the Tydom websocket."""
        wait_between_retry = 5
        exiting = False

        while True:
            # try to get authorization, retry every 5 seconds on failure
            try:
                authorization = await self._get_authorization()

            except AuthorizationError as error:
                CLIENT_LOGGER.warning("Unable to get the authorization: %s", error)
                await self._sleep(wait_between_retry)
                continue

            retry_after_pause = False
            try:
                async with AsyncExitStack() as connection:
                    # only the opening may fail with a network or handshake error
                    self._websocket = await self._open_websocket(
                        connection, authorization
                    )
                    if self._websocket is None:
                        retry_after_pause = True
                    else:
                        CLIENT_LOGGER.info("Connected to tydom")
                        self._connected.set()

                        async with asyncio.TaskGroup() as task_group:
                            task_group.create_task(self._read_messages(task_group))
                            task_group.create_task(self._ping())
                            for connection_routine in self._on_connection_routines:
                                task_group.create_task(connection_routine(self))

            except* asyncio.CancelledError:
                CLIENT_LOGGER.info("Exiting...")
                exiting = True

            except* CommunicationError as error_group:
                for error in error_group.exceptions:
                    CLIENT_LOGGER.warning("Connection lost: %s", error)

            await self._disconnect()

            if exiting:
                return

            if retry_after_pause:
                await self._sleep(wait_between_retry)

    async def _open_websocket(
        self, connection: AsyncExitStack, authorization: str
    ) -> WebsocketConnection | None:
        """Open the websocket, closed with the connection; None if it failed."""
        try:
            return await connection.enter_async_context(
                self._connection_factory(authorization)
            )
        except InvalidStatus:
            # this is probably due to a 401
            CLIENT_LOGGER.error("Invalid authentication")
        # network (OSError, TimeoutError included) or handshake failure
        except (OSError, InvalidHandshake) as error:
            CLIENT_LOGGER.warning("Unable to open the websocket: %r", error)
        return None

    async def _disconnect(self) -> None:
        self._connected.clear()
        # fail the requests still waiting at once, rather than at their timeout
        self._pending_requests.flush(CommunicationError("Connection lost"))

        try:
            async with asyncio.TaskGroup() as task_group:
                for disconnection_routine in self._on_disconnection_routines:
                    task_group.create_task(disconnection_routine())
        except* RuntimeError:
            CLIENT_LOGGER.exception("Error in disconnection routine")

        if self._websocket is not None:
            await self._websocket.close()
            self._websocket = None

    async def _read_messages(self, task_group: asyncio.TaskGroup) -> None:
        """Read the messages, handlers run in the task group to keep reading."""
        if self._websocket is None:
            raise RuntimeError("Not connected")

        try:
            async for message in self._websocket:
                if not isinstance(message, bytes):
                    raise CommunicationError("Unexpected text message from Tydom")
                try:
                    parsed_msg = parse_message(message, self._cmd_prefix)
                except (InvalidRequest, InvalidResponse) as error:
                    CLIENT_LOGGER.warning(
                        "Rejected a malformed message: %s", type(error).__name__
                    )
                    CLIENT_LOGGER.debug("Malformed message %r", message, exc_info=True)
                    continue

                if isinstance(parsed_msg, TydomRequest):
                    # a snapshot, so subscribing meanwhile cannot alter the iteration
                    for subscription in tuple(self._subscriptions):
                        if (handling := subscription(parsed_msg)) is not None:
                            task_group.create_task(_log_handler_error(handling))

                elif isinstance(parsed_msg, Response):
                    if not self._pending_requests.resolve(parsed_msg):
                        CLIENT_LOGGER.warning(
                            "Got an unexpected response %s", parsed_msg.status
                        )
                        CLIENT_LOGGER.debug("Headers %s", parsed_msg.headers)
                        CLIENT_LOGGER.debug("Content %s", parsed_msg.content)

        except ConnectionClosed as error:
            raise CommunicationError("Connection closed") from error

        # websockets ends the iteration when the Tydom closes the connection cleanly
        raise CommunicationError("Connection closed")

    async def _ping(self) -> None:
        """Send a ping message to the Tydom device.

        This method is started automatically when the connection is established.
        It sends a ping message every `self._ping_interval` seconds.

        Raises:
            asyncio.CancelledError: If the task is cancelled while waiting.

        """
        while True:
            await self.send(PingRequest())
            await self._sleep(self._ping_interval)

    async def wait_unit_connected(self) -> None:
        """Wait asynchronously until the unit is connected.

        It returns at once when the connection is already established.

        Raises:
            asyncio.CancelledError: If the task is cancelled while waiting.

        """
        await self._connected.wait()

    async def send[ResponseT: ParsedResponse](
        self, request: ClientRequest[ResponseT], timeout: int | None = None
    ) -> ResponseT:
        """Send a request over the websocket connection and waits for the response.

        Args:
            request (Request): The request object to be sent. It must have a unique
                transaction_id.
            timeout (int  | None): The timeout for the request in seconds.
                If None or not set, the default timeout is used.

        Returns:
            Response: The response object received for the given request.

        Raises:
            RuntimeError: If the websocket connection is not established.
            AssertionError: If a request with the same transaction_id already exists.
            asyncio.TimeoutError: If the request times out.

        Notes:
            - The method ensures that the transaction_id of the request is unique
              among the requests currently in progress.
            - The response is awaited asynchronously, and the request is removed
              from the in-progress list once completed or in case of an exception.

        """
        if timeout is None:
            timeout = self._default_timeout

        if (
            request.transaction_id is None
            or request.transaction_id in self._pending_requests
        ):
            raise TydomIoError("The request needs a unique transaction id")

        if self._websocket is None:
            raise RuntimeError("Not connected")

        future_response = self._pending_requests.register(request.transaction_id)

        CLIENT_LOGGER.debug(
            "Sending request '%s' with transaction id %s",
            type(request).__name__,
            request.transaction_id,
        )
        try:
            await self._websocket.send(request.to_bytes(cmd_prefix=self._cmd_prefix))
        except ConnectionClosed as error:
            # no response will come, and nobody would retrieve the future's error
            self._pending_requests.abandon(request.transaction_id)
            raise CommunicationError("Connection closed") from error

        try:
            async with asyncio.timeout(timeout):
                response = request.parse_response(await future_response)

            CLIENT_LOGGER.debug(
                "Received response '%s' with transaction id %s",
                type(response).__name__,
                response.transac_id,
            )
            return response

        except asyncio.exceptions.TimeoutError as timeout_error:
            self._pending_requests.abandon(request.transaction_id)
            raise CommunicationError("Request timeout") from timeout_error

    def get_number_of_requests_in_progress(self) -> int:
        """Get the number of requests in progress."""
        return len(self._pending_requests)


async def _log_handler_error(handling: Callable[[], Awaitable[None]]) -> None:
    """Run a request handler, logging its error rather than ending the connection."""
    try:
        await handling()
    except RuntimeError:
        CLIENT_LOGGER.exception("Error in request handler")
