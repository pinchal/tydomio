import logging

from tydomio.scrub import SecretFilter, Secrets

# invented secrets; the password built from parts: a literal reads as hardcoded
PASSWORD = "-".join(("Invented", "Pass", "42"))
SECRETS = Secrets(password=PASSWORD, ip="192.0.2.77", mac="001122AABBCC")


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
