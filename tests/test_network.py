import ssl
from collections.abc import Callable

import httpx
import pytest

from tydomio.error import AuthorizationError
from tydomio.network import get_authorization, tydom_ssl_context

URL = "https://tydom.test/mediation/client?mac=000000000000&appli=1"
DIGEST_CHALLENGE = 'Digest realm="protected area", nonce="abc", qop="auth"'


async def authorize(handler: Callable[[httpx.Request], httpx.Response]) -> str:
    return await get_authorization(
        url=URL,
        username="user",
        password="",
        ssl_context=ssl.create_default_context(),
        transport=httpx.MockTransport(handler),
    )


def respond(status_code: int, challenge: str | None = None) -> httpx.Response:
    headers = {} if challenge is None else {"WWW-Authenticate": challenge}
    return httpx.Response(status_code, headers=headers)


def test_remote_context_verifies_certificate() -> None:
    context = tydom_ssl_context(local_access=False)

    assert context.verify_mode == ssl.CERT_REQUIRED


def test_remote_context_checks_hostname() -> None:
    context = tydom_ssl_context(local_access=False)

    assert context.check_hostname


def test_local_context_accepts_self_signed_certificate() -> None:
    context = tydom_ssl_context(local_access=True)

    assert context.verify_mode == ssl.CERT_NONE


def test_local_context_allows_legacy_renegotiation() -> None:
    context = tydom_ssl_context(local_access=True)

    assert context.options & ssl.OP_LEGACY_SERVER_CONNECT


async def test_get_authorization_answers_the_digest_challenge() -> None:
    authorization = await authorize(lambda _request: respond(401, DIGEST_CHALLENGE))

    assert authorization.startswith("Digest ")


@pytest.mark.parametrize(
    "response",
    [
        respond(503),
        respond(404),
        respond(401, 'Basic realm="protected area"'),
    ],
)
async def test_get_authorization_fails_without_digest_challenge(
    response: httpx.Response,
) -> None:
    with pytest.raises(AuthorizationError, match=str(response.status_code)):
        await authorize(lambda _request: response)


async def test_get_authorization_fails_when_tydom_unreachable() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    with pytest.raises(AuthorizationError):
        await authorize(unreachable)


async def test_get_authorization_fails_on_malformed_digest_challenge() -> None:
    with pytest.raises(AuthorizationError):
        await authorize(lambda _request: respond(401, 'Digest qop="auth"'))


async def test_get_authorization_error_holds_no_url() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"unreachable {URL}", request=request)

    with pytest.raises(AuthorizationError) as error:
        await authorize(unreachable)

    assert URL not in str(error.value)
