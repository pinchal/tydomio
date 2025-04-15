"""Client implementation."""

import asyncio.exceptions
import itertools
import json
import logging
import random
import re
import ssl
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import httpx
import websockets.auth
from websockets import WebSocketClientProtocol

CLIENT_LOGGER = logging.getLogger("tydomio.client")
REQUEST_LOGGER = CLIENT_LOGGER.getChild("request")
RESPONSE_LOGGER = CLIENT_LOGGER.getChild("response")


OnConnectionRoutine = Callable[["AsyncTydomClient"], Coroutine[Any, Any, None]]
OnDisconnectionRoutine = Callable[[], Coroutine[Any, Any, None]]


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
        on_connection_routines: tuple[OnConnectionRoutine, ...] = (),
        on_disconnection_routines: tuple[OnDisconnectionRoutine, ...] = (),
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
        self._cmd_prefix = "" if self._local_access else "\x02"
        self._ssl_context = ssl._create_unverified_context()
        if self._local_access:
            self._ssl_context.options |= 0x4

        self._websocket: WebSocketClientProtocol | None = None
        self._requests_in_progress: dict[int, asyncio.Future[Response]] = {}
        self._connexion_st_cond = asyncio.Condition()

        self._on_connection_routines = on_connection_routines
        self._on_disconnection_routines = on_disconnection_routines

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
                self._websocket = None
                authorization = await self._get_authorization()

            except httpx.ConnectError:
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
                    ping_timeout=None,
                ) as self._websocket:
                    CLIENT_LOGGER.info("Connected to tydom")

                    async with asyncio.TaskGroup() as task_group:
                        task_group.create_task(self._read_messages())
                        for connection_routine in self._on_connection_routines:
                            task_group.create_task(connection_routine(self))

            except* (asyncio.exceptions.CancelledError, websockets.ConnectionClosedOK):
                CLIENT_LOGGER.info("Exiting...")
                exiting = True

            except* (websockets.ConnectionClosed, websockets.ConnectionClosedError):
                CLIENT_LOGGER.exception("Connection lost")

            except* websockets.exceptions.InvalidStatusCode:
                # this is probably due to a 401, break
                CLIENT_LOGGER.error("Invalid authentication")
                self._websocket = None

            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(self._read_messages())
                for disconnection_routine in self._on_disconnection_routines:
                    task_group.create_task(disconnection_routine())

            if exiting:
                return

    async def _read_messages(self) -> None:
        assert self._websocket is not None

        async for message in self._websocket:
            CLIENT_LOGGER.debug("raw content: %s", message)
            parsed_msg = parse_message(message, self._cmd_prefix)

            if isinstance(parsed_msg, Request):
                CLIENT_LOGGER.info(
                    "Got a request %s %s",
                    parsed_msg.method,
                    parsed_msg.url,
                )
                CLIENT_LOGGER.info("Headers %s", parsed_msg.headers)
                CLIENT_LOGGER.info("Content %s", parsed_msg.json)

            if isinstance(parsed_msg, Response):
                response_transac_id = parsed_msg.transac_id
                if response_transac_id not in self._requests_in_progress:
                    CLIENT_LOGGER.warning(
                        "Got an unexpected response %s", parsed_msg.status
                    )
                    CLIENT_LOGGER.warning("Headers %s", parsed_msg.headers)
                    CLIENT_LOGGER.warning("Content %s", parsed_msg.content)
                else:
                    self._requests_in_progress[response_transac_id].set_result(
                        parsed_msg
                    )

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

    async def send(self, request: "Request") -> "Response":
        """Send a request over the websocket connection and waits for the response.

        Args:
            request (Request): The request object to be sent. It must have a unique
                transaction_id.

        Returns:
            Response: The response object received for the given request.

        Raises:
            RuntimeError: If the websocket connection is not established.
            AssertionError: If a request with the same transaction_id already exists.

        Notes:
            - The method ensures that the transaction_id of the request is unique
              among the requests currently in progress.
            - The response is awaited asynchronously, and the request is removed
              from the in-progress list once completed or in case of an exception.

        """
        assert request.transaction_id not in self._requests_in_progress

        if self._websocket is None:
            raise RuntimeError("Not connected")

        future_response: asyncio.Future[Response] = asyncio.Future()
        self._requests_in_progress[request.transaction_id] = future_response

        try:
            await self._websocket.send(request.to_bytes(cmd_prefix=self._cmd_prefix))
            response = await future_response
            return response
        finally:
            del self._requests_in_progress[request.transaction_id]


TRANSACTION_COUNTER = itertools.count(random.randint(1, 100000000))


@dataclass
class Request:
    """The Request.

    - sent by the client to the Tydom
    - but also from the Tydom to the client 🤷
    """

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    transaction_id: int = field(default_factory=lambda: next(TRANSACTION_COUNTER))

    def _make_headers(self) -> dict[str, str]:
        auto_headers: dict[str, str] = {}

        has_content_length = (
            "Content-Length" in self.headers or "Transfer-Encoding" in self.headers
        )

        has_transac_id = "Transac-Id" in self.headers

        if not has_content_length and self.method in ("POST", "PUT", "PATCH"):
            auto_headers["Content-Length"] = "0"

        if not has_transac_id:
            auto_headers["Transac-Id"] = str(self.transaction_id)

        return {**self.headers, **auto_headers}

    def to_bytes(self, cmd_prefix: str) -> bytes:
        """Format this request to an array of bytes to send on the websocket."""
        return (
            f"{cmd_prefix}{self.method} {self.url} HTTP/1.1\r\n"
            + "\r\n".join(
                (f"{key}: {value}" for key, value in self._make_headers().items())
            )
            + "\r\n\r\n"
        ).encode("ascii")

    @property
    def parsed_content(self) -> str:
        """Parse the request content."""
        if self.content is None:
            raise RuntimeError("No content to parse")

        REQUEST_LOGGER.debug("Parse json from %s", self.content)

        str_content = self.content.decode("utf-8")
        str_content_clean = re.sub(r"[0-9A-F]+\r\n", "\r\n", str_content).rstrip()

        return str_content_clean

    @property
    def json(self) -> Any:
        """Get the parsed json content."""
        return self.parsed_content[1]


@dataclass
class Response:
    """The Response.

    It's sent by the Tydom following a client's Request
    """

    status: int
    headers: dict[str, str]
    content: bytes | None
    logger = logging.getLogger("tydomio.client.request")

    @property
    def json(self) -> Any:
        """Get the parsed json content."""
        if self.content is None:
            raise RuntimeError("No content to parse")

        RESPONSE_LOGGER.debug("Parse json from %s", self.content)
        return json.loads(self.content.decode("utf-8"))

    @property
    def transac_id(self) -> int | None:
        """Get the transaction id."""
        if "Transac-Id" not in self.headers:
            return None
        return int(self.headers["Transac-Id"])


def parse_message(message: bytes, cmd_prefix: str) -> Response | Request:
    """Parse messages received from the websocket into Request or Response."""
    is_response = message.startswith(f"{cmd_prefix}HTTP/1.1 ".encode("ascii"))

    body_position = message.find(b"\r\n\r\n")

    if is_response:
        header_parts = message[:body_position].split(b"\r\n")
        status = int(header_parts[0].split(b" ")[1])
        headers = {}
        for keyval in header_parts[1:]:
            key, val = keyval.decode("ascii").split(":")
            headers[key.strip()] = val.strip()

        return Response(
            status=status, headers=headers, content=message[body_position + 4 :]
        )

    header_parts = message[:body_position].split(b"\r\n")
    method_url = header_parts[0].decode("ascii").split(" ")
    first_line_expected_size = 3
    assert len(method_url) == first_line_expected_size and method_url[2] == "HTTP/1.1"
    method = method_url[0]
    url = method_url[1]
    headers = {}
    for keyval in header_parts[1:]:
        key, val = keyval.decode("ascii").split(":")
        headers[key.strip()] = val.strip()

    return Request(
        method=method,
        url=url,
        headers=headers,
        content=message[body_position + 4 :],
    )
