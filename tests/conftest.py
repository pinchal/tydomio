"""Minimal doubles of the network boundary, shared by the tests."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager

AUTHORIZATION = "Digest test"

# synthetic /configs/file payload, every list left empty
CONFIG_CONTENT = (
    b'{"date": 1, "version_application": "1.0", "endpoints": [],'
    b' "old_tycam": false, "moments": [], "os": "tydom", "groups": [],'
    b' "areas": [], "scenarios": [], "id_catalog": "catalog",'
    b' "version": "2.0", "zigbee_networks": []}'
)


class ScriptExhaustedError(Exception):
    """Raised by a double asked for more than its script holds."""


class FakeWebsocket:
    """Replays scripted messages, records what is sent and whether it is closed."""

    def __init__(
        self, *messages: bytes, reply: Callable[[bytes], bytes | None] | None = None
    ) -> None:
        """Replay the messages, then hold the connection open until closed.

        :param reply: called with each message sent, gives the message to receive in
        return, if any, or raises the error of the send
        """
        # None ends the iteration, as websockets does once closed locally
        self._incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
        for message in messages:
            self._incoming.put_nowait(message)
        self._reply = reply
        self.sent: list[bytes] = []
        self.closed = False

    async def send(self, message: bytes, /) -> None:
        """Record the message and receive its reply."""
        self.sent.append(message)
        if self._reply is not None and (reply := self._reply(message)) is not None:
            self._incoming.put_nowait(reply)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield the scripted messages and replies, then end as websockets does."""
        while (message := await self._incoming.get()) is not None:
            yield message

    async def close(self) -> None:
        """Record the closing."""
        self.closed = True
        self._incoming.put_nowait(None)


class FakeConnectionFactory:
    """Hands out the scripted websockets, then raises ScriptExhaustedError."""

    def __init__(self, *websockets: FakeWebsocket) -> None:
        """Give each websocket to one connection, in order."""
        self._websockets = list(websockets)
        self.authorizations: list[str] = []
        self.connected = asyncio.Event()

    @asynccontextmanager
    async def __call__(self, authorization: str) -> AsyncGenerator[FakeWebsocket]:
        """Record the authorization and open the next websocket."""
        self.authorizations.append(authorization)
        if not self._websockets:
            raise ScriptExhaustedError
        self.connected.set()
        yield self._websockets.pop(0)


class FakeAuthorizationProvider:
    """Raises the scripted errors in order, then gives AUTHORIZATION."""

    def __init__(self, *errors: Exception) -> None:
        """Raise each error on one call, in order."""
        self._errors = list(errors)
        self.calls = 0

    async def __call__(self) -> str:
        """Record the call and give the next outcome."""
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return AUTHORIZATION


class FakeSleep:
    """Records the delays without sleeping."""

    def __init__(self) -> None:
        """Start with no delay."""
        self.delays: list[float] = []
        self.called = asyncio.Event()

    async def __call__(self, delay: float) -> None:
        """Record the delay."""
        self.delays.append(delay)
        self.called.set()


def tydom_reply(sent: bytes) -> bytes:
    """Answer as the Tydom: the config to GET /configs/file, an empty 200 otherwise."""
    transac_id = sent.split(b"transac-id: ")[1].split(b"\r\n")[0]
    headers = b"HTTP/1.1 200 OK\r\ntransac-id: " + transac_id + b"\r\n"
    if not sent.startswith(b"GET /configs/file "):
        return headers + b"\r\n"
    return headers + b"uri-origin: /configs/file\r\n\r\n" + CONFIG_CONTENT


def put(url: str, content: bytes) -> bytes:
    """Build a JSON PUT pushed by the Tydom."""
    return (
        f"PUT {url} HTTP/1.1\r\ncontent-type: application/json\r\n\r\n".encode()
        + content
    )
