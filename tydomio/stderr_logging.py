"""Logging setup of the entry points (capture, MCP, TUI), never used by the library."""

import logging
import sys
from typing import Final

_NOISY_LOGGERS: Final = ("httpx", "httpcore", "websockets", "asyncio")


def set_levels() -> None:
    """Set the root logger to INFO and the noisy libraries to WARNING."""
    logging.getLogger().setLevel(logging.INFO)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def log_to_stderr() -> logging.Handler:
    """Send INFO and above to stderr, the noisy libraries at WARNING.

    The caller adds a SecretFilter to the handler returned, and removes it when done.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    set_levels()
    return handler
