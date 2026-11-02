import pytest

from tests.conftest import CONFIG_CONTENT
from tydomio.error import InvalidResponse
from tydomio.response import ConfigResponse, JsonResponse, Ok, Response

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
