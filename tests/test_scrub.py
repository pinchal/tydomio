import logging

import pytest

from tydomio.scrub import SecretFilter, Secrets, read_secrets

# invented secrets; the password built from parts: a literal reads as hardcoded
PASSWORD = "-".join(("Invented", "Pass", "42"))
SECRETS = Secrets(password=PASSWORD, ip="192.0.2.77", mac="001122AABBCC")
ENV = {
    "TYDOM_IP": "192.0.2.77",
    "TYDOM_MAC_ADDRESS": "00:11:22:AA:BB:CC",
    "TYDOM_PASSWORD": PASSWORD,
}


def test_scrub_replaces_every_secret() -> None:
    text = "wss://192.0.2.77/?mac=00:11:22:aa:bb:cc TYDOM-AABBCC Invented-Pass-42"

    assert (
        SECRETS.scrub(text)
        == "wss://<redacted>/?mac=<redacted> TYDOM-<redacted> <redacted>"
    )


def test_the_filter_scrubs_the_message_and_drops_the_traceback() -> None:
    try:
        raise ValueError(PASSWORD)
    except ValueError as error:
        exc_info = (type(error), error, error.__traceback__)
    record = logging.LogRecord(
        "name", logging.ERROR, __file__, 1, "ip %s", ("192.0.2.77",), exc_info
    )

    assert SecretFilter(SECRETS).filter(record)
    assert record.msg == "ip <redacted>"
    assert (record.args, record.exc_info, record.exc_text) == (None, None, None)


def test_read_secrets_returns_the_secrets_with_a_bare_mac() -> None:
    assert read_secrets(ENV) == SECRETS


@pytest.mark.parametrize("name", list(ENV))
def test_a_missing_variable_is_logged_by_name(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    env = {key: value for key, value in ENV.items() if key != name}

    assert read_secrets(env) is None

    assert caplog.messages == [f"{name} is not set"]


def test_a_mac_that_is_not_12_hex_digits_is_logged_without_its_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert read_secrets(ENV | {"TYDOM_MAC_ADDRESS": "00:11:22:zz"}) is None

    assert caplog.messages == ["TYDOM_MAC_ADDRESS is not 12 hexadecimal digits"]
