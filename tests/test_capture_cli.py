import asyncio
import io
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import tydomio.capture.cli
import tydomio.globals
import tydomio.network
from tests.conftest import (
    AUTHORIZATION,
    FakeAuthorizationProvider,
    FakeConnectionFactory,
    FakeWebsocket,
    answer_gets,
)
from tydomio.capture.cli import main
from tydomio.capture.fetch import ENDPOINTS
from tydomio.client import ConnectionFactory, Sleep

# invented secrets and bodies
IP = "192.0.2.77"
MAC = "001122AABBCC"
MAC_FORMS = ["001122aabbcc", "00:11:22:aa:bb:cc"]
# invented, built from parts: a literal reads as a hardcoded password
PASSWORD = "-".join(("Invented", "Pass", "42"))

BODIES: dict[str, object] = {
    "/configs/file": {
        "date": 1700000000,
        "version": "2.0",
        "endpoints": [{"id_endpoint": 1, "id_device": 1700000001, "name": "Attic"}],
        "groups": [],
        "moments": [],
        "scenarios": [],
        "areas": [],
        "zigbee_networks": [],
    },
    "/devices/meta": [
        {
            "id": 1700000001,
            "endpoints": [
                {
                    "id": 1,
                    "error": 0,
                    "metadata": [{"name": "mode", "enum_values": ["ECO", "AUTO"]}],
                }
            ],
        }
    ],
    "/devices/cmeta": [{"id": 1700000001, "endpoints": [{"id": 1, "cmetadata": []}]}],
    "/devices/data": [
        {
            "id": 1700000001,
            "endpoints": [{"id": 1, "data": [{"name": "mode", "value": "ECO"}]}],
        }
    ],
    "/areas/meta": [],
    "/areas/cmeta": [],
    "/areas/data": [],
    "/groups/file": {"groups": []},
    "/scenarios/file": {"scn": []},
    "/moments/file": {"prog": []},
    "/protocols": {"protocols": ["zigbee"]},
    "/info": {"productName": "TYWELL", "mac": "001122AABBCC", "updateAvailable": False},
}


def answers(**overrides: tuple[int, object]) -> dict[str, tuple[int, bytes]]:
    statuses = {url: (200, body) for url, body in BODIES.items()}
    for name, answer in overrides.items():
        statuses[f"/{name.replace('_', '/')}"] = answer
    return {
        url: (status, json.dumps(body).encode())
        for url, (status, body) in statuses.items()
    }


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYDOM_PASSWORD", PASSWORD)
    monkeypatch.delenv("TYDOM_IP", raising=False)
    monkeypatch.delenv("TYDOM_MAC_ADDRESS", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO())


def capture(
    output: Path,
    connection_factory: ConnectionFactory,
    sleep: Sleep = asyncio.sleep,
    *,
    access: tuple[str, ...] = ("--tydom-ip", IP),
    mac: str = MAC,
    default_authorization: bool = False,
) -> int:
    """Run the CLI; default_authorization keeps the library's digest request."""
    return main(
        ["-o", str(output), *access, "--tydom-mac", mac],
        authorization_provider=None
        if default_authorization
        else FakeAuthorizationProvider(),
        connection_factory=connection_factory,
        sleep=sleep,
    )


def test_full_run_writes_every_endpoint_with_get_requests_only(tmp_path: Path) -> None:
    websocket = FakeWebsocket(reply=answer_gets(answers()))
    output = tmp_path / "capture"

    status = capture(output, FakeConnectionFactory(websocket))

    allowed = {f"GET {endpoint.url} HTTP/1.1" for endpoint in ENDPOINTS}
    allowed.add("GET /ping HTTP/1.1")
    assert (
        status,
        {path.name for path in output.iterdir()},
        {message.split(b"\r\n")[0].decode() for message in websocket.sent} <= allowed,
    ) == (
        0,
        {endpoint.file_name for endpoint in ENDPOINTS} | {"manifest.json"},
        True,
    )


def test_one_endpoint_answering_404_gives_a_partial_capture(tmp_path: Path) -> None:
    websocket = FakeWebsocket(reply=answer_gets(answers(protocols=(404, {}))))
    output = tmp_path / "capture"

    status = capture(output, FakeConnectionFactory(websocket))

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert (status, manifest["complete"]) == (1, False)


def test_a_secret_in_a_kept_value_aborts_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    leaking = {"productName": "TYDOM-00-11-22-AA-BB-CC"}
    websocket = FakeWebsocket(reply=answer_gets(answers(info=(200, leaking))))
    output = tmp_path / "capture"

    status = capture(output, FakeConnectionFactory(websocket))

    err = capsys.readouterr().err
    assert (
        status,
        output.exists(),
        "secret found: info.json body.productName" in err,
    ) == (
        2,
        False,
        True,
    )


def test_an_existing_output_directory_gives_status_2_without_connecting(
    tmp_path: Path,
) -> None:
    connection_factory = FakeConnectionFactory()

    status = capture(tmp_path, connection_factory)

    assert (status, connection_factory.authorizations) == (2, [])


def test_dev_mode_gives_status_2_without_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tydomio.globals, "DEV_MODE", True)
    connection_factory = FakeConnectionFactory()

    status = capture(tmp_path / "capture", connection_factory)

    assert (status, connection_factory.authorizations) == (2, [])


def test_a_missing_password_without_terminal_gives_status_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TYDOM_PASSWORD")

    assert capture(tmp_path / "capture", FakeConnectionFactory()) == 2


class RefusingConnectionFactory:
    """Fails every connection with an error naming the IP and the MAC."""

    @asynccontextmanager
    async def __call__(self, authorization: str) -> AsyncGenerator[FakeWebsocket]:
        """Raise the error naming the IP and the MAC."""
        raise OSError(f"cannot reach {IP} for mac={MAC}")
        yield FakeWebsocket()


async def yield_once(_delay: float) -> None:
    await asyncio.sleep(0)


def test_terminal_output_holds_no_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(tydomio.capture.cli, "DEADLINE", 0.05)

    status = capture(
        tmp_path / "capture", RefusingConnectionFactory(), sleep=yield_once
    )

    out, err = capsys.readouterr()
    terminal = (out + err).lower()
    assert status == 2
    assert [
        form for form in (IP, PASSWORD.lower(), *MAC_FORMS) if form in terminal
    ] == []


def test_an_unexpected_exception_prints_its_type_only_and_removes_the_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise LookupError(f"value of {IP}")

    monkeypatch.setattr(tydomio.capture.cli, "find_secrets", fail)
    websocket = FakeWebsocket(reply=answer_gets(answers()))
    output = tmp_path / "capture"

    status = capture(output, FakeConnectionFactory(websocket))

    err = capsys.readouterr().err
    assert (status, err.splitlines()[-1], IP in err, output.exists()) == (
        2,
        "tydomio-capture failed: LookupError",
        False,
        False,
    )


def record_authorization_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str]]:
    """Replace the digest request to the box; record its url and username."""
    requests: list[tuple[str, str]] = []

    async def get_authorization(*, url: str, username: str, **_: object) -> str:
        requests.append((url, username))
        return AUTHORIZATION

    monkeypatch.setattr(tydomio.network, "get_authorization", get_authorization)
    return requests


def test_a_mac_with_separators_reaches_the_box_as_12_bare_digits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = record_authorization_requests(monkeypatch)
    websocket = FakeWebsocket(reply=answer_gets(answers()))

    capture(
        tmp_path / "capture",
        FakeConnectionFactory(websocket),
        mac="00:11:22:aa:bb:cc",
        default_authorization=True,
    )

    assert requests == [(f"https://{IP}/mediation/client?mac={MAC}", MAC)]


def test_an_empty_tydom_ip_gives_remote_access_to_the_box_and_in_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TYDOM_IP", "")
    requests = record_authorization_requests(monkeypatch)
    websocket = FakeWebsocket(reply=answer_gets(answers()))
    output = tmp_path / "capture"

    capture(
        output, FakeConnectionFactory(websocket), access=(), default_authorization=True
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert ([url for url, _ in requests], manifest["access"]) == (
        [f"https://mediation.tydom.com/mediation/client?mac={MAC}"],
        "remote",
    )


def test_a_failure_after_writing_keeps_the_capture_and_its_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def print_failing_on_stdout(*_args: object, file: object = None) -> None:
        if file is None:
            raise UnicodeEncodeError("ascii", "x", 0, 1, "invented")

    monkeypatch.setattr(
        tydomio.capture.cli, "print", print_failing_on_stdout, raising=False
    )
    websocket = FakeWebsocket(reply=answer_gets(answers()))
    output = tmp_path / "capture"

    status = capture(output, FakeConnectionFactory(websocket))

    assert (status, (output / "manifest.json").exists()) == (0, True)
