import logging

import pytest

from tests.conftest import put
from tydomio.error import CommunicationError, InvalidRequest, InvalidResponse
from tydomio.protocol import parse_message, parse_request, parse_response
from tydomio.requests import (
    JsonRequest,
    Request,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
)
from tydomio.response import Response

DEVICES_DATA = b'[{"id": 1, "endpoints": []}]'
AREAS_DATA = b'[{"id": 1, "error": 0, "data": []}]'
DATE = "Sat, 31 Oct 2026 10:00:00 GMT"


def chunked(*chunks: bytes) -> bytes:
    """Frame the chunks as HTTP/1.1 chunked content, with the final chunk."""
    framed = (f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n" for chunk in chunks)
    return b"".join(framed) + b"0\r\n\r\n"


def chunked_put(url: str, framed: bytes) -> bytes:
    """Build a JSON PUT pushed by the Tydom with chunked content."""
    return (
        f"PUT {url} HTTP/1.1\r\ncontent-type: application/json\r\n"
        "transfer-encoding: chunked\r\n\r\n"
    ).encode() + framed


def chunked_response(framed: bytes, transfer_encoding: bytes = b"chunked") -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\ntransfer-encoding: "
        + transfer_encoding
        + b"\r\n\r\n"
        + framed
    )


def test_parse_response_reads_status() -> None:
    response = parse_response(b"HTTP/1.1 404 Not Found\r\n\r\n")

    assert response.status == 404


def test_parse_response_lowercases_header_names() -> None:
    response = parse_response(b"HTTP/1.1 200 OK\r\nTransac-Id: 12\r\n\r\n")

    assert response.headers == {"transac-id": "12"}


def test_parse_response_keeps_content() -> None:
    response = parse_response(b"HTTP/1.1 200 OK\r\n\r\n{}")

    assert response.content == b"{}"


def test_parse_response_empty_content_is_none() -> None:
    response = parse_response(b"HTTP/1.1 200 OK\r\n\r\n")

    assert response.content is None


def test_parse_message_reads_response() -> None:
    message = parse_message(b"HTTP/1.1 200 OK\r\n\r\n", cmd_prefix=b"")

    assert isinstance(message, Response)


def test_parse_message_reads_prefixed_response() -> None:
    message = parse_message(b"\x02HTTP/1.1 200 OK\r\n\r\n", cmd_prefix=b"\x02")

    assert isinstance(message, Response)


def test_parse_message_reads_request() -> None:
    message = parse_message(put("/other", b"{}"), cmd_prefix=b"")

    assert isinstance(message, Request)


def test_parse_request_reads_method_url_and_headers() -> None:
    request = parse_request(put("/other", b"{}"))

    assert (request.method, request.url, request.headers) == (
        "PUT",
        "/other",
        {"content-type": "application/json"},
    )


def test_parse_request_empty_content_is_none() -> None:
    request = parse_request(
        b"GET /a HTTP/1.1\r\nContent-Type: application/json\r\n\r\n"
    )

    assert request.content is None


def test_parse_request_types_devices_data() -> None:
    request = parse_request(put("/devices/data", DEVICES_DATA))

    assert isinstance(request, TydomPutDevicesDataRequest)


def test_parse_request_types_areas_data() -> None:
    request = parse_request(put("/areas/data", AREAS_DATA))

    assert isinstance(request, TydomPutAreasDataRequest)


def test_parse_request_falls_back_to_json_on_invalid_data() -> None:
    request = parse_request(put("/devices/data", b'{"unexpected": true}'))

    assert type(request) is JsonRequest


def test_parse_request_other_url_is_json_request() -> None:
    request = parse_request(put("/other", b"{}"))

    assert type(request) is JsonRequest


def test_parse_request_rejects_invalid_request_line() -> None:
    with pytest.raises(InvalidRequest):
        parse_request(b"PUT /devices/data HTTP/1.0\r\n\r\n")


def test_parse_request_removes_chunk_framing() -> None:
    chunk = b"{}"
    framed = f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n0\r\n\r\n"

    request = parse_request(chunked_put("/other", framed))

    assert request.content == chunk


def test_parse_message_removes_prefix_of_request() -> None:
    message = parse_message(b"\x02" + put("/other", b"{}"), cmd_prefix=b"\x02")

    assert isinstance(message, Request)
    assert message.method == "PUT"


def test_parse_request_decodes_chunks_longer_than_ff() -> None:
    content = b'{"key": "' + b"x" * 300 + b'"}'

    request = parse_request(
        chunked_put("/other", chunked(content[:280], content[280:]))
    )

    assert request.content == content


def test_parse_request_ignores_chunk_extensions() -> None:
    request = parse_request(chunked_put("/other", b"2;name=value\r\n{}\r\n0\r\n\r\n"))

    assert request.content == b"{}"


def test_parse_request_without_transfer_encoding_keeps_content() -> None:
    content = b'{\r\n"key": 1\r\n}'

    request = parse_request(put("/other", content))

    assert request.content == content


def test_parse_response_decodes_chunked_content() -> None:
    response = parse_response(chunked_response(chunked(b"{}")))

    assert response.content == b"{}"


def test_parse_response_decodes_chunked_content_whatever_the_case() -> None:
    response = parse_response(chunked_response(chunked(b"{}"), b"Chunked"))

    assert response.content == b"{}"


@pytest.mark.parametrize(
    "framed",
    [
        b"zz\r\n{}\r\n0\r\n\r\n",
        b"+2\r\n{}\r\n0\r\n\r\n",
        b"5\r\n{}\r\n0\r\n\r\n",
        b"2\r\n{}0\r\n\r\n",
        b"2\r\n{}\r\n",
        b"2\r\n{}\r\n0\r\n\r\nextra",
    ],
)
def test_parse_response_rejects_invalid_chunked_content(framed: bytes) -> None:
    with pytest.raises(InvalidResponse):
        parse_response(chunked_response(framed))


def test_parse_response_rejects_unsupported_transfer_encoding() -> None:
    with pytest.raises(InvalidResponse):
        parse_response(chunked_response(chunked(b"{}"), b"gzip, chunked"))


@pytest.mark.parametrize(
    "message",
    [
        f"HTTP/1.1 200 OK\r\nDate: {DATE}\r\n\r\n".encode(),
        put("/a", b"").replace(b"\r\n\r\n", f"\r\nDate: {DATE}\r\n\r\n".encode()),
    ],
)
def test_parse_message_keeps_header_value_as_is(message: bytes) -> None:
    parsed = parse_message(message, cmd_prefix=b"")

    assert parsed.headers["date"] == DATE


@pytest.mark.parametrize(
    ("message", "error"),
    [
        (b"HTTP/1.1 200 OK\r\nno colon\r\n\r\n", InvalidResponse),
        (b"PUT /a HTTP/1.1\r\nno colon\r\n\r\n", InvalidRequest),
        (b"HTTP/1.1 200 OK\r\nname: \xe9\r\n\r\n", InvalidResponse),
        (b"PUT /\xe9 HTTP/1.1\r\n\r\n", InvalidRequest),
        (b"HTTP/1.1 200 OK\r\ntransac-id: 1", InvalidResponse),
    ],
)
def test_parse_message_rejects_malformed_head(
    message: bytes, error: type[CommunicationError]
) -> None:
    with pytest.raises(error):
        parse_message(message, cmd_prefix=b"")


@pytest.mark.parametrize(
    "status_line", [b"HTTP/1.1 abc OK", b"HTTP/1.1 ", b"HTTP/1.1 2000 OK"]
)
def test_parse_response_rejects_invalid_status_line(status_line: bytes) -> None:
    with pytest.raises(InvalidResponse):
        parse_response(status_line + b"\r\n\r\n")


def test_parse_request_logs_invalid_data_without_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        parse_request(put("/devices/data", b'[{"id": "marker", "endpoints": []}]'))

    formatter = logging.Formatter()
    above_debug = [r for r in caplog.records if r.levelno > logging.DEBUG]
    assert not any("marker" in formatter.format(r) for r in above_debug)
