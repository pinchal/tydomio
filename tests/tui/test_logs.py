import logging

from tydomio.scrub import REDACTED, Secrets
from tydomio.tui.logs import LogBuffer

PASSWORD = "".join(("hunter", "2"))
SECRETS = Secrets(password=PASSWORD, ip="192.168.1.9", mac="001A2BAABBCC")


def record(
    message: str, level: int = logging.INFO, created: float = 0.0
) -> logging.LogRecord:
    made = logging.LogRecord("t", level, "f.py", 1, message, None, None)
    made.created = created
    return made


def test_keeps_the_last_size_lines() -> None:
    buffer = LogBuffer(SECRETS, size=3)
    for number in range(5):
        buffer.handle(record(f"m{number}"))
    assert [line.split(" ", 2)[2] for line in buffer.lines()] == ["m2", "m3", "m4"]


def test_strips_control_characters() -> None:
    buffer = LogBuffer(SECRETS)
    buffer.handle(record("a\x1b[2Jb"))
    assert "\x1b" not in buffer.lines()[0]


def test_debug_is_not_kept() -> None:
    buffer = LogBuffer(SECRETS)
    logger = logging.getLogger("tests.tui.debug")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(buffer)
    logger.debug("quiet")
    assert not buffer.lines()


def test_line_has_time_level_and_message() -> None:
    buffer = LogBuffer(SECRETS)
    created = 3600.0 * 5 + 61
    buffer.handle(record("hello", logging.WARNING, created))
    time, level, message = buffer.lines()[0].split(" ", 2)
    assert (level, message) == ("WARNING", "hello")
    assert len(time) == 8


def test_does_not_cut_long_lines() -> None:
    buffer = LogBuffer(SECRETS)
    buffer.handle(record("x" * 200))
    assert buffer.lines()[0].endswith("x" * 200)


def test_scrubs_secrets_hidden_behind_control_characters() -> None:
    buffer = LogBuffer(SECRETS)
    buffer.handle(record("hunter\x1b2 here"))
    assert REDACTED in buffer.lines()[0]
    assert PASSWORD not in buffer.lines()[0]


def test_a_malformed_record_does_not_raise_and_leaves_a_placeholder() -> None:
    buffer = LogBuffer(SECRETS)
    bad = logging.LogRecord("t", logging.INFO, "f.py", 1, "%s %s", ("one",), None)
    buffer.handle(bad)
    assert buffer.lines()[0].endswith(" INFO <log line could not be formatted>")
