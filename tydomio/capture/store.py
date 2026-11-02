"""The capture on disk: manifest, envelopes, secrets scan and owner-only files."""

import json
import os
import shutil
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Final, Literal

from ..scrub import Secrets
from .anonymize import Anonymized
from .fetch import ENDPOINTS, EndpointResult, Outcome
from .jsonvalue import JsonValue
from .policy import RULES_VERSION

FORMAT_VERSION: Final = 1
MANIFEST_FILE: Final = "manifest.json"
_WRITTEN: Final = (Outcome.OK, Outcome.HTTP_ERROR)


def find_secrets(
    documents: Mapping[str, JsonValue], secrets: Secrets
) -> list[tuple[str, str]]:
    """Locate the secrets in the file names, keys and strings to write.

    Return (file, path) pairs, never a value: a key holding a secret shows as <key>,
    a file name as <file>.
    """
    pattern = secrets.pattern()
    found: list[tuple[str, str]] = []

    def walk(file: str, node: JsonValue, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}".removeprefix(".")
                if pattern.search(key):
                    child = f"{path}.<key>".removeprefix(".")
                    found.append((file, child))
                walk(file, value, child)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(file, item, f"{path}[{index}]")
        elif isinstance(node, str) and pattern.search(node):
            found.append((file, path))

    for file, document in documents.items():
        shown = "<file>" if pattern.search(file) else file
        if shown != file:
            found.append((shown, ""))
        walk(shown, document, "")
    return found


def is_written(result: EndpointResult) -> bool:
    """Whether the endpoint gets a file."""
    return result.outcome in _WRITTEN


def default_directory(today: date, token: str) -> Path:
    """./tydom-capture-<YYYYMM>-<token>: the month only, as captured_on."""
    return Path(f"tydom-capture-{today:%Y%m}-{token}")


def create_directory(directory: Path) -> None:
    """Create the directory, owner-only; FileExistsError if it already exists."""
    directory.mkdir(mode=0o700)
    # the umask may have removed bits; nothing else may be granted
    directory.chmod(0o700)


def build_envelope(result: EndpointResult, body: JsonValue | None) -> JsonValue:
    """Build the file of an endpoint: its url, status and anonymized body."""
    return {"url": result.endpoint.url, "status": result.status, "body": body}


def build_manifest(
    results: Mapping[str, EndpointResult],
    anonymized: Anonymized,
    *,
    tool_version: str,
    captured_on: date,
    access: Literal["local", "remote"],
) -> JsonValue:
    """Build the manifest: outcomes per endpoint and the anonymization report."""
    ordered = [
        results[endpoint.url] for endpoint in ENDPOINTS if endpoint.url in results
    ]
    return {
        "format_version": FORMAT_VERSION,
        "tool": {"name": "tydomio-capture", "version": tool_version},
        "rules_version": RULES_VERSION,
        "captured_on": f"{captured_on:%Y-%m}",
        "access": access,
        "complete": all(result.outcome == Outcome.OK for result in ordered),
        "endpoints": [
            {
                "url": result.endpoint.url,
                "file": result.endpoint.file_name if is_written(result) else None,
                "outcome": str(result.outcome),
                "status": result.status,
                "body_length": result.body_length,
            }
            for result in ordered
        ],
        "unclassified": [
            {
                "url": item.url,
                "path": item.path,
                "type": item.json_type,
                "reason": item.reason,
            }
            for item in anonymized.unclassified
        ],
        "renamed_keys": [
            {"url": item.url, "path": item.path} for item in anonymized.renamed_keys
        ],
    }


def write_capture(directory: Path, documents: Mapping[str, JsonValue]) -> None:
    """Write each document as an owner-only JSON file, the manifest last."""
    for name in sorted(documents, key=lambda name: name == MANIFEST_FILE):
        text = json.dumps(
            documents[name], indent=2, ensure_ascii=False, allow_nan=False
        )
        descriptor = os.open(
            directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(text + "\n")


def remove_directory(directory: Path) -> None:
    """Remove the directory and everything in it, if present."""
    shutil.rmtree(directory, ignore_errors=True)
