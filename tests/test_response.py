import json
import logging

import pytest

from tests.conftest import CONFIG_CONTENT
from tydomio.error import InvalidResponse
from tydomio.model.data import AreasData, DevicesData
from tydomio.response import (
    AreasDataResponse,
    ConfigResponse,
    DevicesDataResponse,
    JsonResponse,
    Ok,
    Response,
)

JSON_HEADERS = {"content-type": "application/json"}


def config_response(
    *,
    status: int = 200,
    uri_origin: str = "/configs/file",
    content: bytes | None = CONFIG_CONTENT,
) -> Response:
    return Response(status=status, headers={"uri-origin": uri_origin}, content=content)


def test_transac_id_is_read_from_headers() -> None:
    response = Response(status=200, headers={"transac-id": "42"}, content=None)

    assert response.transac_id == 42


def test_transac_id_is_none_without_header() -> None:
    response = Response(status=200, headers={}, content=None)

    assert response.transac_id is None


def test_ok_accepts_status_200_without_content() -> None:
    response = Ok(Response(status=200, headers={}, content=None))

    assert response.status == 200


def test_ok_rejects_other_status() -> None:
    with pytest.raises(InvalidResponse):
        Ok(Response(status=500, headers={}, content=None))


def test_ok_rejects_content() -> None:
    with pytest.raises(InvalidResponse):
        Ok(Response(status=200, headers={}, content=b"{}"))


def test_config_response_validates_config() -> None:
    response = ConfigResponse(config_response())

    assert response.config.version == "2.0"


def test_config_response_rejects_other_status() -> None:
    with pytest.raises(InvalidResponse):
        ConfigResponse(config_response(status=404))


def test_config_response_rejects_other_uri_origin() -> None:
    with pytest.raises(InvalidResponse):
        ConfigResponse(config_response(uri_origin="/devices/data"))


def test_config_response_rejects_missing_content() -> None:
    with pytest.raises(InvalidResponse):
        ConfigResponse(config_response(content=None))


def test_config_response_rejects_content_not_matching_model() -> None:
    with pytest.raises(InvalidResponse):
        ConfigResponse(config_response(content=b'{"date": 1}'))


def test_json_response_parses_content() -> None:
    response = JsonResponse(
        Response(status=200, headers=JSON_HEADERS, content=b'{"key": "value"}')
    )

    assert response.json_content == {"key": "value"}


def test_json_response_without_content_has_no_json() -> None:
    response = JsonResponse(Response(status=200, headers=JSON_HEADERS, content=None))

    assert response.json_content is None


def test_json_response_rejects_status_not_listed() -> None:
    with pytest.raises(InvalidResponse):
        JsonResponse(Response(status=404, headers=JSON_HEADERS, content=b"{}"))


def test_json_response_accepts_listed_status() -> None:
    response = JsonResponse(
        Response(status=201, headers=JSON_HEADERS, content=b"{}"),
        valid_status_codes=(201,),
    )

    assert response.status == 201


def test_json_response_accepts_json_media_type_with_parameters() -> None:
    headers = {"content-type": "Application/JSON; charset=UTF-8"}

    response = JsonResponse(Response(status=200, headers=headers, content=b"{}"))

    assert response.json_content == {}


def test_json_response_rejects_other_content_type() -> None:
    with pytest.raises(InvalidResponse):
        JsonResponse(
            Response(status=200, headers={"content-type": "text/plain"}, content=b"{}")
        )


def test_json_response_rejects_invalid_json() -> None:
    with pytest.raises(InvalidResponse):
        JsonResponse(Response(status=200, headers=JSON_HEADERS, content=b"{"))


def test_json_response_error_carries_no_content() -> None:
    with pytest.raises(InvalidResponse) as raised:
        JsonResponse(
            Response(status=200, headers=JSON_HEADERS, content=b'{"room": "Kitchen"')
        )

    assert "Kitchen" not in str(raised.value)
    # not chained: the decoding or validation error holds the content
    assert raised.value.__cause__ is None


def test_json_response_status_error_carries_no_content() -> None:
    with pytest.raises(InvalidResponse) as raised:
        JsonResponse(
            Response(status=404, headers=JSON_HEADERS, content=b'{"room": "Kitchen"}')
        )

    assert "Kitchen" not in str(raised.value)


def test_config_response_error_carries_no_value() -> None:
    with pytest.raises(InvalidResponse) as raised:
        ConfigResponse(config_response(content=b'{"date": "Kitchen"}'))

    assert "Kitchen" not in str(raised.value)
    # not chained: the decoding or validation error holds the content
    assert raised.value.__cause__ is None


def test_ok_error_carries_no_content() -> None:
    with pytest.raises(InvalidResponse) as raised:
        Ok(Response(status=500, headers={}, content=b'{"room": "Kitchen"}'))

    assert "Kitchen" not in str(raised.value)


def data_item(name: str = "level", value: object = 50) -> dict[str, object]:
    return {"name": name, "validity": "upToDate", "value": value}


def endpoint(endpoint_id: int, value: object = 50) -> dict[str, object]:
    return {"id": endpoint_id, "error": 0, "data": [data_item(value=value)]}


def data_response(uri_origin: str, content: object, *, status: int = 200) -> Response:
    raw = content if content is None else json.dumps(content).encode()
    return Response(status=status, headers={"uri-origin": uri_origin}, content=raw)


def test_devices_data_response_keeps_valid_endpoints() -> None:
    content = [
        {"id": 1, "endpoints": [endpoint(10), endpoint(11)]},
        {"id": 2, "endpoints": [endpoint(20)]},
    ]
    raw = json.dumps(content).encode()

    response = DevicesDataResponse(
        Response(status=200, headers={"uri-origin": "/devices/data"}, content=raw)
    )

    assert response.data.root == DevicesData.model_validate_json(raw).root
    assert response.invalid == ()


def test_devices_data_response_lists_an_invalid_endpoint_without_its_value() -> None:
    content = [
        {"id": 1, "endpoints": [endpoint(10), endpoint(11, value=[1, 2])]},
        {"id": 2, "endpoints": [endpoint(20)]},
    ]

    response = DevicesDataResponse(data_response("/devices/data", content))

    assert response.invalid == ((1, 11),)
    assert [
        (device.id, [e.id for e in device.endpoints]) for device in response.data.root
    ] == [(1, [10]), (2, [20])]


def test_devices_data_response_keeps_a_device_whose_endpoints_all_failed() -> None:
    content = [{"id": 1, "endpoints": [endpoint(10, value=[1])]}]

    response = DevicesDataResponse(data_response("/devices/data", content))

    assert response.invalid == ((1, 10),)
    assert [(d.id, d.endpoints) for d in response.data.root] == [(1, [])]


@pytest.mark.parametrize(
    "content",
    [
        {"a": 1},
        [{"endpoints": []}],
        [{"id": 1, "endpoints": [{"id": "x", "error": 0, "data": []}]}],
    ],
)
def test_devices_data_response_refuses_an_unreadable_envelope(content: object) -> None:
    with pytest.raises(InvalidResponse) as raised:
        DevicesDataResponse(data_response("/devices/data", content))

    assert str(raised.value) == "Invalid /devices/data content"
    assert raised.value.__cause__ is None


def test_devices_data_response_refuses_not_json_content() -> None:
    with pytest.raises(InvalidResponse) as raised:
        DevicesDataResponse(
            Response(
                status=200, headers={"uri-origin": "/devices/data"}, content=b"{Kitchen"
            )
        )

    assert str(raised.value) == "Invalid /devices/data content"
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("status", "uri_origin", "content"),
    [
        (404, "/devices/data", []),
        (200, "/areas/data", []),
        (200, "/devices/data", None),
    ],
)
def test_devices_data_response_refuses_a_bad_status_or_origin(
    status: int, uri_origin: str, content: object
) -> None:
    with pytest.raises(InvalidResponse) as raised:
        DevicesDataResponse(data_response(uri_origin, content, status=status))

    assert str(raised.value) == f"Unexpected response with status {status}"


def test_areas_data_response_keeps_valid_areas() -> None:
    content = [endpoint(1), endpoint(2)]
    raw = json.dumps(content).encode()

    response = AreasDataResponse(
        Response(status=200, headers={"uri-origin": "/areas/data"}, content=raw)
    )

    assert response.data.root == AreasData.model_validate_json(raw).root
    assert response.invalid == ()


def test_areas_data_response_lists_an_invalid_area_without_its_value() -> None:
    content = [endpoint(1), endpoint(2, value=[1, 2]), endpoint(3)]

    response = AreasDataResponse(data_response("/areas/data", content))

    assert response.invalid == (2,)
    assert [area.id for area in response.data.root] == [1, 3]


@pytest.mark.parametrize("content", [{"a": 1}, [{"error": 0}], [{"id": "x"}], [1]])
def test_areas_data_response_refuses_an_unreadable_envelope(content: object) -> None:
    with pytest.raises(InvalidResponse) as raised:
        AreasDataResponse(data_response("/areas/data", content))

    assert str(raised.value) == "Invalid /areas/data content"
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("status", "uri_origin", "content"),
    [
        (404, "/areas/data", []),
        (200, "/devices/data", []),
        (200, "/areas/data", None),
    ],
)
def test_areas_data_response_refuses_a_bad_status_or_origin(
    status: int, uri_origin: str, content: object
) -> None:
    with pytest.raises(InvalidResponse) as raised:
        AreasDataResponse(data_response(uri_origin, content, status=status))

    assert str(raised.value) == f"Unexpected response with status {status}"


def test_data_response_logs_counts_at_warning_and_ids_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="tydomio.response")
    content = [{"id": 7, "endpoints": [endpoint(8, value=[1])]}]

    DevicesDataResponse(data_response("/devices/data", content))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == [
        "1 invalid endpoints in /devices/data"
    ]
    debugs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("7" in m and "8" in m for m in debugs)
