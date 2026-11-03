"""Logging setup of the entry points (capture, MCP); never imported by the library."""

import logging
import sys
from typing import Final

_NOISY_LOGGERS: Final = ("httpx", "httpcore", "websockets", "asyncio")


def log_to_stderr() -> logging.Handler:
    """Send INFO and above to stderr, the noisy libraries at WARNING.

    The caller adds a SecretFilter to the handler returned, and removes it when done.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler
