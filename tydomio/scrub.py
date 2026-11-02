"""Scrubbing of the secrets (password, IP, MAC) out of text and log records."""

import logging
import re
from dataclasses import dataclass
from typing import Final, override

REDACTED: Final = "<redacted>"


@dataclass(frozen=True)
class Secrets:
    """The secrets of the run: the password, the IP and the MAC as 12 hex digits."""

    password: str
    ip: str | None
    mac: str

    def pattern(self) -> re.Pattern[str]:
        """Match any secret, in any case, the MAC with or without separators.

        The MAC alone in its last 6 digits too: the first 6 are the public vendor
        prefix, the last 6 are unique to the Tydom and appear in names (TYDOM-AABBCC).
        """
        mac = self.mac.lower()
        # the full MAC first, so that scrub removes it whole
        forms = [re.escape(self.password), "[:.-]?".join(mac), "[:.-]?".join(mac[-6:])]
        if self.ip is not None:
            forms.append(re.escape(self.ip))
        return re.compile("|".join(forms), re.IGNORECASE)

    def scrub(self, text: str) -> str:
        """Replace every occurrence of the secrets with REDACTED."""
        return self.pattern().sub(REDACTED, text)


class SecretFilter(logging.Filter):
    """Format each record, scrub the secrets and drop the traceback."""

    def __init__(self, secrets: Secrets) -> None:
        """Scrub the records with these secrets."""
        super().__init__()
        self._secrets = secrets

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._secrets.scrub(record.getMessage())
        record.args = None
        record.exc_info = None
        record.exc_text = None
        return True
