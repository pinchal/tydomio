import pytest

from tydomio.capture.jsonvalue import MAX_DEPTH, NotJson, parse_json


@pytest.mark.parametrize(
    "content",
    [
        b'{"name": "\xff"}',
        b'{"value": NaN}',
        b'{"value": Infinity}',
        b'{"value": -Infinity}',
        b'{"value": 1e400}',
        b'{"value": ',
        b"",
        b"[" * (MAX_DEPTH + 1) + b"]" * (MAX_DEPTH + 1),
        b"[" * 100000 + b"]" * 100000,
    ],
    ids=[
        "not-utf8",
        "nan",
        "infinity",
        "minus-infinity",
        "overflow",
        "truncated",
        "empty",
        "too-deep",
        "recursion",
    ],
)
def test_parse_json_rejects_content_that_is_not_strict_json(content: bytes) -> None:
    with pytest.raises(NotJson):
        parse_json(content)


def test_parse_json_accepts_the_maximum_depth() -> None:
    content = b"[" * MAX_DEPTH + b"]" * MAX_DEPTH

    value = parse_json(content)

    assert isinstance(value, list)


def test_parse_json_keeps_int_and_float_apart() -> None:
    value = parse_json(b"[1, 1.0]")

    assert isinstance(value, list)
    assert [type(item) for item in value] == [int, float]
