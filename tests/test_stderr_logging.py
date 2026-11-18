import logging
from collections.abc import Iterator

import pytest

from tydomio.stderr_logging import set_levels

NOISY = ("httpx", "httpcore", "websockets", "asyncio")


@pytest.fixture(name="levels_restored")
def restore_levels() -> Iterator[None]:
    loggers = [logging.getLogger(), *map(logging.getLogger, NOISY)]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


@pytest.mark.usefixtures("levels_restored")
def test_set_levels_puts_the_root_at_info_and_the_noisy_libraries_at_warning() -> None:
    set_levels()

    assert logging.getLogger().level == logging.INFO
    assert [logging.getLogger(name).level for name in NOISY] == [logging.WARNING] * 4
