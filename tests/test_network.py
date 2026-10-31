import ssl

from tydomio.network import tydom_ssl_context


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
