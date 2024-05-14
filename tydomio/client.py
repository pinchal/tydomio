"""Client implementation."""

import asyncio.exceptions
import json
import logging
import ssl
from dataclasses import dataclass, field
from typing import Any

import httpx
import websockets.auth

CLIENT_LOGGER = logging.getLogger("tydomio.client")
REQUEST_LOGGER = CLIENT_LOGGER.getChild("request")
RESPONSE_LOGGER = CLIENT_LOGGER.getChild("response")


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

        while True:
            # try to get authorization, retry every 5 seconds on failure
            try:
                authorization = await self._get_authorization()

                # open websocket
                async with websockets.connect(
                    uri=f"wss://{self._tydom_target}:443"
                    f"/mediation/client?mac={self._tydom_mac}&appli=1",
                    extra_headers={"Authorization": authorization},
                    ssl=self._ssl_context,
                    ping_timeout=None,
                ) as websocket:
                    CLIENT_LOGGER.info("Connected to tydom")

                    await websocket.send(
                        Request(
                            cmd_prefix=self._cmd_prefix,
                            method="GET",
                            url="/configs/file",
                            headers={
                                "Content-Type": "application/json; charset=UTF-8",
                                "Transac-Id": "0",
                            },
                        ).to_bytes()
                    )

                    try:
                        async for message in websocket:
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
                                CLIENT_LOGGER.info(
                                    "Got a response %s", parsed_msg.status
                                )
                                CLIENT_LOGGER.info("Headers %s", parsed_msg.headers)
                                CLIENT_LOGGER.info("Content %s", parsed_msg.content)
                                CLIENT_LOGGER.info("Content %s", parsed_msg.json)

                    except websockets.ConnectionClosed:
                        CLIENT_LOGGER.exception("Connection lost")

                    except asyncio.exceptions.CancelledError:
                        CLIENT_LOGGER.info("Exiting...")
                        return

            except httpx.ConnectError:
                # wait and retry
                CLIENT_LOGGER.warning("Unable to reach %s", self._tydom_target)
                await asyncio.sleep(wait_between_retry)
                continue

            except websockets.exceptions.InvalidStatusCode:
                # this is probably due to a 401, break
                CLIENT_LOGGER.error("Invalid authentication")
                break


@dataclass
class Request:
    """The Request.

    - sent by the client to the Tydom
    - but also from the Tydom to the client 🤷
    """

    cmd_prefix: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None

    def _make_headers(self) -> dict[str, str]:
        auto_headers: dict[str, str] = {}

        has_content_length = (
            "Content-Length" in self.headers or "Transfer-Encoding" in self.headers
        )

        if not has_content_length and self.method in ("POST", "PUT", "PATCH"):
            auto_headers["Content-Length"] = "0"

        return {**self.headers, **auto_headers}

    def to_bytes(self) -> bytes:
        """Format this request to an array of bytes to send on the websocket."""
        return (
            f"{self.cmd_prefix}{self.method} {self.url} HTTP/1.1\r\n"
            + "\r\n".join(
                (f"{key}: {value}" for key, value in self._make_headers().items())
            )
            + "\r\n\r\n"
        ).encode("ascii")

    @property
    def parsed_content(self) -> tuple[str, Any, str]:
        """Parse the request content."""
        if self.content is None:
            raise RuntimeError("No content to parse")

        REQUEST_LOGGER.debug("Parse json from %s", self.content)

        str_content = self.content.decode("utf-8")
        number_of_seperator_in_weird_content = 2

        if str_content.count("\r\n") == number_of_seperator_in_weird_content:
            # parse this weird content format from Tydom
            json_start = str_content.find("\r\n") + 2
            json_end = str_content.find("\r\n", json_start)
            stuff_before = str_content[:json_start].replace("\r\n", "")
            stuff_after = str_content[json_end:].replace("\r\n", "")

            REQUEST_LOGGER.debug(
                "Parsing request from Tydom, "
                "weird stuffs are %s (before) and %s (after)",
                stuff_before,
                stuff_after,
            )

            return (
                stuff_before,
                json.loads(str_content[json_start:json_end]),
                stuff_after,
            )

        # Parse a normal content build from our code
        return "", json.loads(str_content), ""

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
        cmd_prefix=cmd_prefix,
        method=method,
        url=url,
        headers=headers,
        content=message[body_position + 4 :],
    )
