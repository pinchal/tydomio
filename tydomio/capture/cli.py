"""tydomio-capture: anonymized, read-only capture of a Tydom's endpoints."""

import argparse
import asyncio
import getpass
import logging
import os
import re
import secrets
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from importlib.metadata import version
from pathlib import Path
from typing import Final, override

import tydomio.globals

from ..client import AsyncTydomClient, AuthorizationProvider, ConnectionFactory, Sleep
from .anonymize import Anonymized, anonymize
from .fetch import (
    DEADLINE,
    ClientStopped,
    EndpointResult,
    Fetcher,
    NeverConnected,
    Outcome,
    run_capture,
)
from .jsonvalue import JsonValue
from .store import (
    MANIFEST_FILE,
    Secrets,
    build_envelope,
    build_manifest,
    create_directory,
    default_directory,
    find_secrets,
    is_written,
    remove_directory,
    write_capture,
)

EXIT_COMPLETE: Final = 0
EXIT_PARTIAL: Final = 1
EXIT_NOTHING_WRITTEN: Final = 2

_MAC = re.compile(r"[0-9a-fA-F]{2}([:.-]?)(?:[0-9a-fA-F]{2}\1){4}[0-9a-fA-F]{2}")
_NOISY_LOGGERS: Final = ("httpx", "httpcore", "websockets", "asyncio")


class _Failure(Exception):
    """A failure of the run, its message free of secrets and values."""


class _SecretFilter(logging.Filter):
    """Format each record, scrub the secrets and drop the traceback."""

    def __init__(self, secrets_: Secrets) -> None:
        super().__init__()
        self._secrets = secrets_

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._secrets.scrub(record.getMessage())
        record.args = None
        record.exc_info = None
        record.exc_text = None
        return True


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tydomio-capture",
        description="Capture, anonymized, the read-only endpoints of a Tydom. "
        "The password comes from TYDOM_PASSWORD, else from a prompt.",
    )
    parser.add_argument("-o", "--output", type=Path, help="new directory to write")
    parser.add_argument(
        "--tydom-ip",
        default=os.environ.get("TYDOM_IP"),
        help="local access; remote access through mediation without it",
    )
    parser.add_argument("--tydom-mac", default=os.environ.get("TYDOM_MAC_ADDRESS"))
    return parser.parse_args(argv)


def _prompt(variable: str | None, prompt: str, missing: str) -> str:
    """Return the value given, else typed on a terminal; never echoed."""
    if variable is not None and len(variable) > 0:
        return variable
    if sys.stdin.isatty():
        if typed := getpass.getpass(prompt):
            return typed
    raise _Failure(missing)


def _secrets(args: argparse.Namespace) -> Secrets:
    """Return the MAC, the password and the IP given or typed; _Failure if missing."""
    mac = _prompt(args.tydom_mac, "Tydom MAC address: ", "no Tydom MAC address")
    if not _MAC.fullmatch(mac):
        raise _Failure("the Tydom MAC address is not 12 hexadecimal digits")
    password = _prompt(
        os.environ.get("TYDOM_PASSWORD"), "Tydom password: ", "no Tydom password"
    )
    # the box expects the 12 bare digits in the digest username and the URLs;
    # an empty TYDOM_IP means remote access, as an unset one
    return Secrets(
        password=password,
        ip=args.tydom_ip or None,
        mac=re.sub(r"[^0-9A-Fa-f]", "", mac).upper(),
    )


def _configure_logging(secrets_: Secrets) -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(_SecretFilter(secrets_))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler


def _documents(
    results: Mapping[str, EndpointResult],
    anonymized: Anonymized,
    *,
    today: date,
    local: bool,
) -> dict[str, JsonValue]:
    documents: dict[str, JsonValue] = {
        result.endpoint.file_name: build_envelope(
            result, anonymized.bodies.get(result.endpoint.url)
        )
        for result in results.values()
        if is_written(result)
    }
    documents[MANIFEST_FILE] = build_manifest(
        results,
        anonymized,
        tool_version=version("tydomio"),
        captured_on=today,
        access="local" if local else "remote",
    )
    return documents


def _summary(
    results: Mapping[str, EndpointResult], anonymized: Anonymized, directory: Path
) -> str:
    return "\n".join(
        [
            *(f"{url}: {result.outcome}" for url, result in results.items()),
            f"unclassified fields: {len(anonymized.unclassified)}",
            f"renamed keys: {len(anonymized.renamed_keys)}",
            f"written to: {directory}",
            "Captured data is personal until reviewed:"
            " review every file before sharing or committing.",
        ]
    )


def _capture(
    directory: Path,
    secrets_: Secrets,
    client: AsyncTydomClient,
    fetcher: Fetcher,
) -> tuple[int, str]:
    """Fetch, anonymize, scan for secrets and write; the status and summary to print.

    Raise _Failure when nothing is written.
    """
    try:
        results = asyncio.run(run_capture(client, fetcher, deadline=DEADLINE))
    except NeverConnected as error:
        raise _Failure(str(error)) from None
    except ClientStopped:
        raise _Failure("the client stopped before the capture ended") from None
    if not any(result.outcome == Outcome.OK for result in results.values()):
        raise _Failure("no endpoint answered with JSON")

    written = {url: result for url, result in results.items() if is_written(result)}
    anonymized = anonymize(
        {url: result.body for url, result in written.items()},
        {url: result.status or 0 for url, result in written.items()},
    )
    today = date.today()
    documents = _documents(
        results, anonymized, today=today, local=secrets_.ip is not None
    )
    if found := find_secrets(documents, secrets_):
        for file, path in found:
            print(f"secret found: {file} {path}", file=sys.stderr)
        raise _Failure("secrets found, nothing written")

    summary = _summary(results, anonymized, directory)
    write_capture(directory, documents)
    complete = all(result.outcome == Outcome.OK for result in results.values())
    return EXIT_COMPLETE if complete else EXIT_PARTIAL, summary


def main(
    argv: Sequence[str] | None = None,
    *,
    authorization_provider: AuthorizationProvider | None = None,
    connection_factory: ConnectionFactory | None = None,
    sleep: Sleep = asyncio.sleep,
) -> int:
    """Capture the endpoints; 0 complete, 1 partial, 2 nothing written."""
    args = _parse(argv)
    # read at call time: requests and response dump raw JSON in DEV_MODE
    if tydomio.globals.DEV_MODE:
        print("tydomio-capture: refused with DEV_MODE on", file=sys.stderr)
        return EXIT_NOTHING_WRITTEN
    try:
        secrets_ = _secrets(args)
    except _Failure as failure:
        print(f"tydomio-capture: {failure}", file=sys.stderr)
        return EXIT_NOTHING_WRITTEN

    directory = args.output or default_directory(date.today(), secrets.token_hex(3))
    try:
        create_directory(directory)
    except OSError as error:
        print(
            secrets_.scrub(
                "tydomio-capture: cannot create the output directory:"
                f" {type(error).__name__}"
            ),
            file=sys.stderr,
        )
        return EXIT_NOTHING_WRITTEN

    handler = _configure_logging(secrets_)
    status = EXIT_NOTHING_WRITTEN
    try:
        fetcher = Fetcher()
        client = AsyncTydomClient(
            tydom_password=secrets_.password,
            tydom_ip=secrets_.ip,
            tydom_mac=secrets_.mac,
            on_connection_routines=(fetcher,),
            authorization_provider=authorization_provider,
            connection_factory=connection_factory,
            sleep=sleep,
        )
        status, summary = _capture(directory, secrets_, client, fetcher)
        # written: a failure from here on keeps the capture
        print(secrets_.scrub(summary))
    except _Failure as failure:
        print(secrets_.scrub(f"tydomio-capture: {failure}"), file=sys.stderr)
    # any message or traceback may hold a secret or a value: the type name only
    except Exception as error:  # pylint: disable=broad-exception-caught
        print(f"tydomio-capture failed: {type(error).__name__}", file=sys.stderr)
    finally:
        logging.getLogger().removeHandler(handler)
        if status == EXIT_NOTHING_WRITTEN:
            remove_directory(directory)
    return status
