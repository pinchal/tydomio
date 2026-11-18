"""The log lines kept for the terminal UI: no output but the buffer itself."""

import logging
import time
from collections import deque
from typing import override

from ..scrub import Secrets
from ..state.view import strip_controls


class LogBuffer(logging.Handler):
    """Keep the last log lines, oldest first, as "HH:MM:SS LEVEL message"."""

    def __init__(self, secrets: Secrets, size: int = 200) -> None:
        """Keep at most `size` lines, from the level INFO up."""
        super().__init__(logging.INFO)
        self._secrets = secrets
        self._lines: deque[str] = deque(maxlen=size)

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Keep the line, without control characters (a line is cut at drawing)."""
        clock = time.strftime("%H:%M:%S", time.localtime(record.created))
        try:
            message = record.getMessage()
        except TypeError, ValueError, KeyError:
            # a bad format or args must not reach the logging caller
            message = "<log line could not be formatted>"
        text = f"{clock} {record.levelname} {message}"
        # scrubbed again after the stripping, so a secret cannot hide behind controls
        self._lines.append(self._secrets.scrub(strip_controls(text)))

    def lines(self) -> list[str]:
        """Give the kept lines, oldest first."""
        return list(self._lines)
