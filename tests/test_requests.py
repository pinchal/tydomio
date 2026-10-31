import pytest

from tydomio.error import InvalidRequest
from tydomio.model.data import AreasData, DevicesData
from tydomio.requests import (
    JsonRequest,
    PingRequest,
    Request,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
)
from tydomio.response import Ok, Response

JSON_HEADERS = {"content-type": "application/json"}
DEVICES_DATA = (
    b'[{"id": 1, "endpoints": [{"id": 2, "error": 0,'
    b' "data": [{"name": "level", "validity": "upToDate", "value": 50}]}]}]'
)
AREAS_DATA = (
    b'[{"id": 3, "error": 0,'
    b' "data": [{"name": "zone", "validity": "upToDate", "value": "on"}]}]'
)


def header_lines(raw: bytes) -> list[bytes]:
    return raw.split(b"\r\n\r\n")[0].split(b"\r\n")[1:]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_to_bytes_adds_zero_content_length_to_methods_with_body(method: str) -> None:
    raw = Request(method=method, url="/a").to_bytes(cmd_prefix=b"")

    assert b"content-length: 0" in header_lines(raw)


def test_to_bytes_adds_no_content_length_to_get() -> None:
    raw = Request(method="GET", url="/a").to_bytes(cmd_prefix=b"")

    assert not any(
        line.lower().startswith(b"content-length") for line in header_lines(raw)
    )


def test_to_bytes_keeps_given_content_length() -> None:
    request = Request(method="POST", url="/a", headers={"Content-Length": "0"})

    lines = header_lines(request.to_bytes(cmd_prefix=b""))

    assert [line for line in lines if line.lower().startswith(b"content-length")] == [
        b"Content-Length: 0"
    ]


def test_to_bytes_adds_no_content_length_with_transfer_encoding() -> None:
    request = Request(method="POST", url="/a", headers={"Transfer-Encoding": "chunked"})

    lines = header_lines(request.to_bytes(cmd_prefix=b""))

    assert not any(line.lower().startswith(b"content-length") for line in lines)


def test_to_bytes_keeps_given_content_length_whatever_its_case() -> None:
    request = Request(method="POST", url="/a", headers={"content-length": "5"})

    lines = header_lines(request.to_bytes(cmd_prefix=b""))

    assert [line for line in lines if line.lower().startswith(b"content-length")] == [
        b"content-length: 5"
    ]


def test_to_bytes_adds_no_content_length_with_transfer_encoding_whatever_its_case() -> (
    None
):
    request = Request(method="POST", url="/a", headers={"transfer-encoding": "chunked"})

    lines = header_lines(request.to_bytes(cmd_prefix=b""))

    assert not any(line.lower().startswith(b"content-length") for line in lines)


def test_to_bytes_writes_content_after_headers() -> None:
    raw = Request(method="PUT", url="/a", content=b"{}").to_bytes(cmd_prefix=b"")

    assert raw.endswith(b"\r\n\r\n{}")


def test_to_bytes_sets_content_length_to_content_size_in_bytes() -> None:
    raw = Request(method="PUT", url="/a", content="é".encode()).to_bytes(cmd_prefix=b"")

    assert b"content-length: 2" in header_lines(raw)


def test_to_bytes_adds_transaction_id() -> None:
    request = Request(method="GET", url="/a")

    lines = header_lines(request.to_bytes(cmd_prefix=b""))

    assert f"transac-id: {request.transaction_id}".encode() in lines


def test_to_bytes_keeps_given_transac_id() -> None:
    request = Request(method="GET", url="/a", headers={"Transac-Id": "7"})

    lines = header_lines(request.to_bytes(cmd_prefix=b""))

    assert [line for line in lines if line.lower().startswith(b"transac-id")] == [
        b"Transac-Id: 7"
    ]


def test_to_bytes_keeps_given_transac_id_whatever_its_case() -> None:
    request = Request(method="GET", url="/a", headers={"transac-id": "7"})

    lines = header_lines(request.to_bytes(cmd_prefix=b""))

    assert [line for line in lines if line.lower().startswith(b"transac-id")] == [
        b"transac-id: 7"
    ]


def test_to_bytes_writes_request_line_and_ends_headers() -> None:
    raw = Request(method="GET", url="/a").to_bytes(cmd_prefix=b"")

    assert raw.startswith(b"GET /a HTTP/1.1\r\n")
    assert raw.endswith(b"\r\n\r\n")


def test_to_bytes_starts_with_prefix() -> None:
    raw = Request(method="GET", url="/a").to_bytes(cmd_prefix=b"\x02")

    assert raw.startswith(b"\x02GET /a HTTP/1.1\r\n")


def test_requests_get_distinct_transaction_ids() -> None:
    first = Request(method="GET", url="/a")
    second = Request(method="GET", url="/a")

    assert first.transaction_id != second.transaction_id


def test_client_request_parses_response_into_its_response_type() -> None:
    response = PingRequest().parse_response(
        Response(status=200, headers={}, content=None)
    )

    assert isinstance(response, Ok)


def test_json_request_parses_content() -> None:
    request = JsonRequest("PUT", "/a", JSON_HEADERS, b'{"key": "value"}')

    assert request.json_content == {"key": "value"}


def test_json_request_without_content_has_no_json() -> None:
    request = JsonRequest("GET", "/a", JSON_HEADERS)

    assert request.json_content is None


def test_json_request_has_no_transaction_id() -> None:
    request = JsonRequest("PUT", "/a", JSON_HEADERS, b"{}")

    assert request.transaction_id is None


def test_json_request_accepts_json_media_type_with_parameters() -> None:
    headers = {"content-type": "Application/JSON; charset=UTF-8"}

    request = JsonRequest("PUT", "/a", headers, b'{"key": "value"}')

    assert request.json_content == {"key": "value"}


def test_json_request_rejects_other_content_type() -> None:
    with pytest.raises(InvalidRequest):
        JsonRequest("PUT", "/a", {"content-type": "text/plain"}, b"{}")


def test_json_request_rejects_invalid_json() -> None:
    with pytest.raises(InvalidRequest):
        JsonRequest("PUT", "/a", JSON_HEADERS, b"{")


def test_devices_data_request_validates_content() -> None:
    request = TydomPutDevicesDataRequest("PUT", "/devices/data", {}, DEVICES_DATA)

    # TydomRequest.data is typed by an unbound TypeVar: narrow it for the checker
    assert isinstance(request.data, DevicesData)
    assert request.data.root[0].endpoints[0].data[0].value == 50


def test_areas_data_request_validates_content() -> None:
    request = TydomPutAreasDataRequest("PUT", "/areas/data", {}, AREAS_DATA)

    assert isinstance(request.data, AreasData)
    assert request.data.root[0].data[0].value == "on"


def test_tydom_request_has_no_transaction_id() -> None:
    request = TydomPutAreasDataRequest("PUT", "/areas/data", {}, AREAS_DATA)

    assert request.transaction_id is None


def test_tydom_request_rejects_missing_content() -> None:
    with pytest.raises(InvalidRequest):
        TydomPutDevicesDataRequest("PUT", "/devices/data", {})


def test_tydom_request_rejects_content_not_matching_model() -> None:
    with pytest.raises(InvalidRequest):
        TydomPutDevicesDataRequest("PUT", "/devices/data", {}, b'[{"id": 1}]')


def test_tydom_request_rejects_invalid_json() -> None:
    with pytest.raises(InvalidRequest):
        TydomPutDevicesDataRequest("PUT", "/devices/data", {}, b"[")
