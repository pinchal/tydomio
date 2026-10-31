"""Minimal doubles of the network boundary, shared by the tests."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

AUTHORIZATION = "Digest test"


class ScriptExhaustedError(Exception):
    """Raised by a double asked for more than its script holds."""


class FakeWebsocket:
    """Replays scripted messages, records what is sent and whether it is closed."""

    def __init__(self, *messages: bytes) -> None:
        """Replay the messages, then hold the connection open until closed."""
        self._messages = messages
        self._closed = asyncio.Event()
        self.sent: list[bytes] = []

    @property
    def closed(self) -> bool:
        """Whether close was called."""
        return self._closed.is_set()

    async def send(self, message: bytes, /) -> None:
        """Record the message."""
        self.sent.append(message)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield the scripted messages, then end as websockets does."""
        for message in self._messages:
            yield message
        # websockets ends the iteration once the connection is closed locally
        await self._closed.wait()

    async def close(self) -> None:
        """Record the closing."""
        self._closed.set()


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

    async def __call__(self, delay: float) -> None:
        """Record the delay."""
        self.delays.append(delay)
