import json
import stat
from datetime import date
from pathlib import Path

import pytest

from tydomio.capture.anonymize import anonymize
from tydomio.capture.fetch import ENDPOINTS, EndpointResult, Outcome
from tydomio.capture.jsonvalue import JsonValue
from tydomio.capture.store import (
    build_manifest,
    create_directory,
    default_directory,
    find_secrets,
    write_capture,
)
from tydomio.scrub import Secrets

CAPTURED_ON = date(2026, 3, 17)
DOCUMENTS: dict[str, JsonValue] = {"info.json": {"body": 1}, "manifest.json": {}}
# invented secrets; the password built from parts: a literal reads as hardcoded
PASSWORD = "-".join(("Invented", "Pass", "42"))
SECRETS = Secrets(password=PASSWORD, ip="192.0.2.77", mac="001122AABBCC")


def written(tmp_path: Path) -> Path:
    directory = tmp_path / "capture"
    create_directory(directory)
    write_capture(directory, DOCUMENTS)
    return directory


@pytest.mark.parametrize(
    "text",
    [
        "at 192.0.2.77",
        "invented-pass-42",
        "001122aabbcc",
        "TYDOM-001122AABBCC",
        "00:11:22:aa:bb:cc",
        "00-11-22-AA-BB-CC",
        "00.11.22.aa.bb.cc",
        "0011.22aa.bbcc",
        "TYDOM-AA:BB:cc",
    ],
    ids=[
        "ip",
        "password",
        "mac",
        "mac-upper",
        "colon",
        "dash",
        "dot",
        "dotted-quad",
        "suffix",
    ],
)
def test_a_secret_in_a_string_is_located_without_its_value(text: str) -> None:
    documents: JsonValue = {"body": {"items": [{"x": 1}, {"x": text}]}}

    assert find_secrets({"info.json": documents}, SECRETS) == [
        ("info.json", "body.items[1].x")
    ]


def test_a_secret_in_a_key_shows_as_key() -> None:
    documents: JsonValue = {"body": {"at 192.0.2.77": 0}}

    assert find_secrets({"info.json": documents}, SECRETS) == [
        ("info.json", "body.<key>")
    ]


def test_a_secret_in_a_file_name_shows_as_file() -> None:
    assert find_secrets({"001122aabbcc.json": {}}, SECRETS) == [("<file>", "")]


def test_clean_documents_give_no_secret() -> None:
    documents: JsonValue = {"body": {"productName": "TYWELL", "list": [1, None]}}

    assert find_secrets({"info.json": documents}, SECRETS) == []


@pytest.mark.parametrize(
    ("outcome", "file"),
    [
        (Outcome.OK, "info.json"),
        (Outcome.HTTP_ERROR, "info.json"),
        (Outcome.NOT_JSON, None),
        (Outcome.TIMEOUT, None),
        (Outcome.COMMUNICATION_ERROR, None),
        (Outcome.NOT_FETCHED, None),
    ],
)
def test_manifest_names_a_file_for_ok_and_http_error_endpoints_only(
    outcome: Outcome, file: str | None
) -> None:
    result = EndpointResult(endpoint=ENDPOINTS[-1], outcome=outcome)

    manifest = build_manifest(
        {"/info": result},
        anonymize({}, {}),
        tool_version="0",
        captured_on=CAPTURED_ON,
        access="local",
    )

    assert isinstance(manifest, dict)
    assert manifest["endpoints"] == [
        {
            "url": "/info",
            "file": file,
            "outcome": str(outcome),
            "status": None,
            "body_length": None,
        }
    ]


def test_write_capture_writes_each_document_as_json(tmp_path: Path) -> None:
    directory = written(tmp_path)

    assert {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in directory.iterdir()
    } == DOCUMENTS


def test_create_directory_refuses_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        create_directory(tmp_path)


def test_directory_and_files_are_owner_only(tmp_path: Path) -> None:
    directory = written(tmp_path)

    modes = {
        stat.S_IMODE(path.stat().st_mode) for path in (directory, *directory.iterdir())
    }
    assert modes == {0o700, 0o600}


def test_default_directory_names_the_month_not_the_day() -> None:
    assert default_directory(CAPTURED_ON, "a1b2c3") == Path(
        "tydom-capture-202603-a1b2c3"
    )
