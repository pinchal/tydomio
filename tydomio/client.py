"""Client implementation."""

import asyncio.exceptions
import logging
import re
import ssl
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, get_type_hints

import httpx
import websockets.auth
from websockets import WebSocketClientProtocol

from .error import CommunicationError, InvalidRequest
from .requests import (
    ClientRequest,
    JsonRequest,
    PingRequest,
    Request,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
    TydomRequest,
)
from .response import Response, ResponseT

CLIENT_LOGGER = logging.getLogger("tydomio.client")
REQUEST_LOGGER = CLIENT_LOGGER.getChild("request")
RESPONSE_LOGGER = CLIENT_LOGGER.getChild("response")

TydomRequestT = TypeVar("TydomRequestT", bound=TydomRequest)

OnConnectionRoutine = Callable[["AsyncTydomClient"], Coroutine[Any, Any, None]]
OnDisconnectionRoutine = Callable[[], Coroutine[Any, Any, None]]
TydomRequestHandler = Callable[[TydomRequestT], Coroutine[Any, Any, None]]


class AsyncTydomClient:
    """Asynchronous client for Tydom.

    The authentication process is heavily inspired by tydom2mqtt
    - https://github.com/fmartinou/tydom2mqtt


    """

    def __init__(
        self,
        tydom_password: str,
        tydom_ip: str | None,
        tydom_mac: str,
        deltadore_remote: str = "mediation.tydom.com",
        default_timeout: int = 5,
        ping_interval: int = 30,
        on_connection_routines: tuple[OnConnectionRoutine, ...] = (),
        on_disconnection_routines: tuple[OnDisconnectionRoutine, ...] = (),
        tydom_request_handlers: tuple[TydomRequestHandler[TydomRequestT], ...] = (),
    ):
        """AsyncTydomClient constructor.

        :param tydom_password: Tydom password,
        It can be retrieved using 'tydomio get-tydom-password'
        :param tydom_ip: The local access to your Tydom (optional)
        :param tydom_mac: The mac address of your Tydom
        :param deltadore_remote: hostname for remote access, used when no ip is provided
        """
        self._tydom_password = tydom_password
        self._tydom_ip = tydom_ip
        self._tydom_mac = tydom_mac

        self._local_access = self._tydom_ip is not None
        self._tydom_target = tydom_ip if self._local_access else deltadore_remote
        self._cmd_prefix = b"" if self._local_access else b"\x02"
        self._ssl_context = ssl._create_unverified_context()
        if self._local_access:
            self._ssl_context.options |= 0x4

        self._websocket: WebSocketClientProtocol | None = None
        self._requests_in_progress: dict[int, asyncio.Future[Response]] = {}
        self._connexion_st_cond = asyncio.Condition()

        self._default_timeout = default_timeout
        self._ping_interval = ping_interval

        self._on_connection_routines = on_connection_routines
        self._on_disconnection_routines = on_disconnection_routines

        def _get_handler_request_type(
            handler: TydomRequestHandler[TydomRequestT],
        ) -> type[TydomRequest]:
            return next(iter(get_type_hints(handler).values()))  # type: ignore

        type_of_requests_handled = {
            _get_handler_request_type(handler) for handler in tydom_request_handlers
        }

        self._on_tydom_request_routines: dict[
            type[TydomRequest], tuple[TydomRequestHandler[TydomRequestT], ...]
        ] = {
            request_type: tuple(
                handler
                for handler in tydom_request_handlers
                if request_type is _get_handler_request_type(handler)
            )
            for request_type in type_of_requests_handled
        }

    async def _get_authorization(self) -> str:
        CLIENT_LOGGER.info(
            "Get authorization from Tydom #%s on %s",
            self._tydom_mac,
            self._tydom_target,
        )

        auth = httpx.DigestAuth(username=self._tydom_mac, password=self._tydom_password)

        async with httpx.AsyncClient(verify=self._ssl_context) as client:
            # build the get request on the endpoint
            request = client.build_request(
                method="GET",
                url=f"https://{self._tydom_target}/mediation/client?mac={self._tydom_mac}",
            )

            # initiate auth flow
            auth_flow = auth.auth_flow(request)

            # send the first request to get the challenge
            response = await client.send(next(auth_flow))
            # forward the rejected response to the auth flow to parse the challenge
            request = auth_flow.send(response)

            # do not perform the authentication at this point,
            # instead, keep the authorization for the websocket and drop the connection
            authorization = request.headers["authorization"]
            assert isinstance(authorization, str)
            return authorization

    async def run_websocket(self) -> None:
        """Authenticate initiate the connection on the Tydom websocket."""
        wait_between_retry = 5
        exiting = False

        while True:
            # try to get authorization, retry every 5 seconds on failure
            try:
                authorization = await self._get_authorization()

            except (httpx.ConnectError, httpx.ConnectTimeout):
                # wait and retry
                CLIENT_LOGGER.warning("Unable to reach %s", self._tydom_target)
                await asyncio.sleep(wait_between_retry)
                continue

            try:
                # open websocket
                async with websockets.connect(
                    uri=f"wss://{self._tydom_target}:443"
                    f"/mediation/client?mac={self._tydom_mac}&appli=1",
                    extra_headers={"Authorization": authorization},
                    ssl=self._ssl_context,
                    ping_interval=None,  # the tydom does not answer to ping
                    close_timeout=self._default_timeout,
                ) as self._websocket:
                    CLIENT_LOGGER.info("Connected to tydom")

                    async with asyncio.TaskGroup() as task_group:
                        task_group.create_task(self._read_messages())
                        task_group.create_task(self._ping())
                        for connection_routine in self._on_connection_routines:
                            task_group.create_task(connection_routine(self))

            except* asyncio.CancelledError:
                CLIENT_LOGGER.info("Exiting...")
                exiting = True

            except* CommunicationError as error_group:
                for error in error_group.exceptions:  # pylint: disable=no-member
                    CLIENT_LOGGER.warning("Connection lost: %s", error)

            except* websockets.exceptions.InvalidStatusCode:
                # this is probably due to a 401, break
                CLIENT_LOGGER.error("Invalid authentication")
                self._websocket = None

            try:
                async with asyncio.TaskGroup() as task_group:
                    for disconnection_routine in self._on_disconnection_routines:
                        task_group.create_task(disconnection_routine())
            except* RuntimeError:
                CLIENT_LOGGER.exception("Error in disconnection routine")

            if self._websocket is not None:
                await self._websocket.close()
                self._websocket = None

            await self._flush_request_in_progress()

            if exiting:
                return

    async def _read_messages(self) -> None:
        assert self._websocket is not None

        try:
            async for message in self._websocket:
                assert isinstance(message, bytes), (
                    "This implementation expects bytes messages from websocket"
                )
                parsed_msg = parse_message(message, self._cmd_prefix)

                if isinstance(parsed_msg, TydomRequest):
                    try:
                        async with asyncio.TaskGroup() as task_group:
                            for handler in self._on_tydom_request_routines.get(
                                type(parsed_msg), ()
                            ):
                                task_group.create_task(
                                    handler(parsed_msg)  # type: ignore
                                )

                    except* RuntimeError:
                        CLIENT_LOGGER.exception("Error in request handler")

                elif isinstance(parsed_msg, Response):
                    response_transac_id = parsed_msg.transac_id
                    if response_transac_id not in self._requests_in_progress:
                        CLIENT_LOGGER.warning(
                            "Got an unexpected response %s", parsed_msg.status
                        )
                        CLIENT_LOGGER.warning("Headers %s", parsed_msg.headers)
                        CLIENT_LOGGER.warning("Content %s", parsed_msg.content)
                    else:
                        future_result = self._requests_in_progress[response_transac_id]
                        future_result.set_result(parsed_msg)
                        del self._requests_in_progress[response_transac_id]

        except websockets.ConnectionClosed as error:
            raise CommunicationError("Connection closed") from error

    async def _ping(self) -> None:
        """Send a ping message to the Tydom device.

        This method is started automatically when the connection is established.
        It sends a ping message every `self._ping_interval` seconds.

        Raises:
            asyncio.CancelledError: If the task is cancelled while waiting.

        """
        while True:
            await self.send(PingRequest())
            await asyncio.sleep(self._ping_interval)

    async def _flush_request_in_progress(self) -> None:
        """Asynchronously flushes all pending requests in progress.

        This method iterates through all futures in `_requests_in_progress`,
        ensuring that any exceptions raised by these futures are retrieved
        and ignored, avoiding asyncio error logs.

        After processing, it clears the `_requests_in_progress` dictionary.
        """
        for future in self._requests_in_progress.values():
            future.exception()
        self._requests_in_progress = {}

    async def wait_unit_connected(self) -> None:
        """Wait asynchronously until the unit is connected.

        This method uses a condition variable to pause execution until the
        `_websocket` attribute is no longer `None`, indicating that the connection
        has been established.

        Raises:
            asyncio.CancelledError: If the task is cancelled while waiting.

        """
        async with self._connexion_st_cond:
            await self._connexion_st_cond.wait_for(lambda: self._websocket is not None)

    async def send(
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

        assert (
            request.transaction_id
            and request.transaction_id not in self._requests_in_progress
        )

        if self._websocket is None:
            raise RuntimeError("Not connected")

        future_response: asyncio.Future[Response] = asyncio.Future()
        self._requests_in_progress[request.transaction_id] = future_response

        CLIENT_LOGGER.debug(
            "Sending request '%s' with transaction id %s",
            type(request).__name__,
            request.transaction_id,
        )
        try:
            await self._websocket.send(request.to_bytes(cmd_prefix=self._cmd_prefix))
        except websockets.ConnectionClosed as error:
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
            del self._requests_in_progress[request.transaction_id]
            raise CommunicationError("Request timeout") from timeout_error

    def get_number_of_requests_in_progress(self) -> int:
        """Get the number of requests in progress."""
        return len(self._requests_in_progress)


def parse_message(message: bytes, cmd_prefix: bytes) -> Response | Request:
    """Parse messages received from the websocket into Request or Response."""
    is_response = message.startswith(cmd_prefix + b"HTTP/1.1 ")

    if is_response:
        return parse_response(message)

    return parse_request(message)


def parse_response(message: bytes) -> Response:
    """Parse messages received from the websocket into Response."""
    body_position = message.find(b"\r\n\r\n")
    header_parts = message[:body_position].split(b"\r\n")
    status = int(header_parts[0].split(b" ")[1])
    headers = {}
    for keyval in header_parts[1:]:
        key, val = keyval.decode("ascii").split(":")
        headers[key.strip().lower()] = val.strip().lower()

    # extract the content, if any
    content: bytes | None = message[body_position + 4 :]
    if not content:
        content = None

    return Response(
        status=status,
        headers=headers,
        content=content,
    )


def parse_request(message: bytes) -> Request:
    """Parse messages received from the websocket into Request."""
    body_position = message.find(b"\r\n\r\n")
    header_parts = message[:body_position].split(b"\r\n")
    method_url = header_parts[0].decode("ascii").split(" ")
    first_line_expected_size = 3
    assert len(method_url) == first_line_expected_size and method_url[2] == "HTTP/1.1"
    method = method_url[0]
    url = method_url[1]
    headers = {}
    for keyval in header_parts[1:]:
        key, val = keyval.decode("ascii").split(":")
        headers[key.strip().lower()] = val.strip().lower()

    raw_content = message[body_position + 4 :]
    # there are some hexa numbers in between the content, between new lines
    # I don't know what they are, but they are not useful
    cleaned_content = re.sub(b"(\r\n)?[0-9a-fA-F]{0,2}\r\n", b"", raw_content)

    if method == "PUT":
        try:
            match url:
                case "/devices/data":
                    return TydomPutDevicesDataRequest(
                        method=method,
                        url=url,
                        headers=headers,
                        content=cleaned_content,
                    )
                case "/areas/data":
                    return TydomPutAreasDataRequest(
                        method=method,
                        url=url,
                        headers=headers,
                        content=cleaned_content,
                    )

        except InvalidRequest:
            logging.exception("Failed to create TydomPutDataRequest")

    return JsonRequest(
        method=method,
        url=url,
        headers=headers,
        content=cleaned_content,
    )
