"""Thin adapters to the real network, used by default by the client."""

import ssl
from contextlib import AbstractAsyncContextManager

import httpx
from websockets.asyncio.client import ClientConnection, connect


def tydom_ssl_context(*, local_access: bool) -> ssl.SSLContext:
    """Build the TLS context the Tydom requires, verified for remote access."""
    if not local_access:
        return ssl.create_default_context()
    # the Tydom serves a self-signed certificate, hence the private unverified context
    context = ssl._create_unverified_context()  # noqa: S323  # pylint: disable=protected-access
    context.options |= 0x4
    return context


async def get_authorization(
    *, url: str, username: str, password: str, ssl_context: ssl.SSLContext
) -> str:
    """Get the digest authorization header from the Tydom, without authenticating."""
    auth = httpx.DigestAuth(username=username, password=password)

    async with httpx.AsyncClient(verify=ssl_context) as client:
        # build the get request on the endpoint
        request = client.build_request(method="GET", url=url)

        # initiate auth flow
        auth_flow = auth.auth_flow(request)

        # send the first request to get the challenge
        response = await client.send(next(auth_flow))
        # forward the rejected response to the auth flow to parse the challenge
        request = auth_flow.send(response)

        # do not perform the authentication at this point,
        # instead, keep the authorization for the websocket and drop the connection
        return request.headers["authorization"]


def connect_websocket(
    authorization: str, *, uri: str, ssl_context: ssl.SSLContext, close_timeout: float
) -> AbstractAsyncContextManager[ClientConnection]:
    """Open the websocket on the Tydom with the given authorization."""
    return connect(
        uri=uri,
        additional_headers={"Authorization": authorization},
        ssl=ssl_context,
        ping_interval=None,  # the tydom does not answer to ping
        close_timeout=close_timeout,
    )
