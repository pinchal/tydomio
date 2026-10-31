import logging
import signal

import pytest

from tests.conftest import (
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeWebsocket,
    put,
    tydom_reply,
)
from tydomio.cli import main


def reply_then_ctrl_c(sent: bytes) -> bytes:
    """Answer as the Tydom, then press Ctrl-C once the run refreshes all devices."""
    if sent.startswith(b"POST /refresh/all "):
        signal.raise_signal(signal.SIGINT)
    return tydom_reply(sent)


def run_cli(*pushed: bytes) -> int:
    """Run the command against a Tydom pushing these messages, until Ctrl-C."""
    websocket = FakeWebsocket(*pushed, reply=reply_then_ctrl_c)
    try:
        status = main(
            [
                "run",
                "--tydom-ip",
                "192.0.2.1",
                "--tydom-mac",
                "000000000000",
                "--tydom-password",
                "",
            ],
            authorization_provider=FakeAuthorizationProvider(),
            connection_factory=FakeConnectionFactory(websocket),
        )
    except KeyboardInterrupt:
        # escaping, it would interrupt the whole pytest session
        pytest.fail("Ctrl-C escaped the command")
    return status


def test_run_ends_with_status_0_on_ctrl_c() -> None:
    assert run_cli() == 0


def test_run_logs_the_config(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    run_cli()

    assert any("'id_catalog': 'catalog'" in message for message in caplog.messages)


@pytest.mark.parametrize(
    ("url", "content"),
    [
        ("/devices/data", b'[{"id": 4242, "endpoints": []}]'),
        ("/areas/data", b'[{"id": 4242, "error": 0, "data": []}]'),
    ],
)
def test_run_logs_pushed_data(
    url: str, content: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    run_cli(put(url, content))

    assert any("id=4242" in message for message in caplog.messages)
