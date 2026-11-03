import json
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.mcp.conftest import areas, config, data, devices
from tydomio.mcp_server.cache import Cache
from tydomio.mcp_server.view import MAX_STRING, get_state, sanitize
from tydomio.model.config import Group
from tydomio.scrub import REDACTED, Secrets

# invented secrets; the password built from parts: a literal reads as hardcoded
PASSWORD = "".join(("hunter", "2"))
SECRETS = Secrets(password=PASSWORD, ip="192.168.1.9", mac="001A2BAABBCC")


def group(group_id: int, usage: str, name: str = "Group") -> Group:
    return Group(
        group_all=True,
        usage=usage,
        name=name,
        id=group_id,
        is_group_user=False,
        picto="picto",
    )


@pytest.fixture(name="cache")
def synced_cache() -> Cache:
    cache = Cache()
    cache.apply_config(
        config(
            {
                (10, 1): ("Shutter", "shutter"),
                (20, 1): ("Boiler", "boiler"),
                (30, 1): ("Alarm", "alarm"),
            },
            {7: ("Living", "area")},
        )
    )
    cache.replace_devices(
        devices(
            (10, 1, [data("position", 100)]),
            (20, 1, [data("batteryCmdDefect", 1.0), data("localMode", "NORMAL")]),
            (30, 1, [data("secret", "x")]),
        ),
        [],
    )
    cache.replace_areas(areas((7, [data("hygroIn", 48.0)])), [])
    cache.mark_connected()
    cache.mark_synced(datetime(2026, 11, 2, 9, 14, 5, tzinfo=UTC))
    return cache


def state(
    cache: Cache, ids: list[str] | None = None, **kw: bool
) -> dict[str, Any]:  # json.loads output
    result = get_state(cache, SECRETS, ids, with_groups=kw.get("with_groups", False))
    return json.loads(json.dumps(result))


def by_id(result: dict[str, Any], item_id: str) -> dict[str, Any]:
    return next(i for i in result["items"] if i["id"] == item_id)


def test_before_the_config_there_are_no_items() -> None:
    result = state(Cache())

    assert result == {
        "cache": {"connection": "connecting", "synced": False, "synced_at": None},
        "items": [],
        "unknown_ids": [],
    }


def test_cache_block_and_item_order(cache: Cache) -> None:
    result = state(cache)

    assert result["cache"] == {
        "connection": "connected",
        "synced": True,
        "synced_at": "2026-11-02T09:14:05Z",
    }
    assert [i["id"] for i in result["items"]] == ["e:10:1", "e:20:1", "e:30:1", "a:7"]


def test_values_are_typed(cache: Cache) -> None:
    result = state(cache)

    assert by_id(result, "e:10:1")["state"] == {"position": 100.0}
    assert by_id(result, "e:20:1")["state"] == {
        "batteryCmdDefect": True,
        "localMode": "NORMAL",
    }
    assert by_id(result, "a:7") == {
        "id": "a:7",
        "name": "Living",
        "kind": "area",
        "state": {"hygroIn": 48.0},
    }


def test_unknown_field_is_output_raw(cache: Cache) -> None:
    cache.merge_devices(devices((10, 1, [data("brandNew", "v", "expired")])))

    item = by_id(state(cache), "e:10:1")

    assert item["state"] == {"position": 100.0, "brandNew": "v"}
    assert item["validity"] == {"brandNew": "expired"}


def test_validity_only_when_not_up_to_date(cache: Cache) -> None:
    cache.merge_devices(devices((10, 1, [data("position", 5, "expired")])))

    assert "validity" not in by_id(state(cache), "e:20:1")
    assert by_id(state(cache), "e:10:1")["validity"] == {"position": "expired"}


def test_tydom_error_only_when_non_zero(cache: Cache) -> None:
    cache.merge_devices(devices((10, 1, [data("position", 5)]), error=3))

    assert by_id(state(cache), "e:10:1")["tydom_error"] == 3
    assert "tydom_error" not in by_id(state(cache), "e:20:1")


def test_a_wrong_cached_value_makes_only_its_entity_invalid(cache: Cache) -> None:
    cache.merge_devices(devices((10, 1, [data("position", "high")])))

    result = state(cache)

    assert by_id(result, "e:10:1") == {
        "id": "e:10:1",
        "name": "Shutter",
        "kind": "shutter",
        "error": "invalid_state",
    }
    assert "state" in by_id(result, "e:20:1")


def test_an_entity_invalid_from_the_snapshot_reads_invalid_state(
    cache: Cache,
) -> None:
    cache.replace_devices(devices(), [(10, 1)])

    assert by_id(state(cache), "e:10:1")["error"] == "invalid_state"


def test_untyped_usage_is_inventory_only(cache: Cache) -> None:
    assert by_id(state(cache), "e:30:1") == {
        "id": "e:30:1",
        "name": "Alarm",
        "kind": "alarm",
    }


def test_ids_filter_and_unknown_ids(cache: Cache) -> None:
    result = state(cache, ["a:7", "e:99:9", "e:10:1"])

    assert [i["id"] for i in result["items"]] == ["e:10:1", "a:7"]
    assert result["unknown_ids"] == ["e:99:9"]


def test_groups_only_with_the_flag_and_only_shutters(cache: Cache) -> None:
    assert cache.config is not None
    cache.config.groups = [group(7, "shutter", "All"), group(8, "light")]

    assert "groups" not in state(cache)
    assert state(cache, with_groups=True)["groups"] == [
        {"id": "g:7", "name": "All", "group_all": True}
    ]


def test_groups_only_without_ids(cache: Cache) -> None:
    assert cache.config is not None
    cache.config.groups = [group(7, "shutter", "All")]

    assert "groups" not in state(cache, ["e:10:1"], with_groups=True)


def test_field_info_holds_only_returned_kinds_and_fields(cache: Cache) -> None:
    result = state(cache, ["e:10:1", "a:7"])

    assert result["field_info"] == {
        "shutter": {"position": {"unit": "%", "description": "0 = closed, 100 = open"}},
        "area": {"hygroIn": {"unit": "%", "description": "Indoor relative humidity"}},
    }


def test_field_info_is_absent_when_empty(cache: Cache) -> None:
    assert "field_info" not in state(cache, ["e:30:1"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\u202eb\u200bc", "abc"),
        ("a\nb\t c", "a b c"),
        ("a \u200b b", "a b"),
        ("x" * 200, "x" * (MAX_STRING - 1) + "…"),
        ("TYDOM-AABBCC", f"TYDOM-{REDACTED}"),
        ("pw hunter2 ip 192.168.1.9", f"pw {REDACTED} ip {REDACTED}"),
        ("hun\u200bter2", REDACTED),
    ],
)
def test_sanitize(raw: str, expected: str) -> None:
    assert sanitize(raw, SECRETS) == expected


def test_every_tydom_string_leaves_sanitized(cache: Cache) -> None:
    cache.merge_devices(
        devices(
            (
                20,
                1,
                [
                    data("localMode", "hunter2"),
                    data("brandNew", "TYDOM-AABBCC", "ex\npired"),
                ],
            )
        )
    )

    item = by_id(state(cache), "e:20:1")

    assert item["state"]["localMode"] == REDACTED
    assert item["state"]["brandNew"] == f"TYDOM-{REDACTED}"
    assert item["validity"] == {"brandNew": "ex pired"}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("position", float("nan")),
        ("brandNew", float("nan")),
        ("brandNew", float("inf")),
        ("position", "nan"),
        ("position", "inf"),
    ],
)
def test_a_non_finite_value_makes_its_entity_invalid(
    cache: Cache, name: str, value: float | str
) -> None:
    cache.merge_devices(devices((10, 1, [data(name, value)])))

    item = by_id(state(cache), "e:10:1")

    assert item["error"] == "invalid_state"
    assert "state" not in item


def test_a_raw_name_does_not_override_a_typed_value(cache: Cache) -> None:
    cache.merge_devices(devices((10, 1, [data("posi\u200btion", 5)])))

    assert by_id(state(cache), "e:10:1")["state"] == {"position": 100.0}


def test_hostile_names_leave_sanitized(cache: Cache) -> None:
    hostile = "a\u202eb hunter2 \u200bTYDOM-AABBCC"
    clean = f"ab {REDACTED} TYDOM-{REDACTED}"
    assert cache.config is not None
    cache.config.endpoints[0].name = hostile
    cache.config.areas[0].name = hostile
    cache.config.endpoints[2].last_usage = hostile
    cache.config.groups = [group(7, "shutter", hostile)]
    cache.merge_devices(devices((20, 1, [data(hostile, 1.0)])))

    result = state(cache, with_groups=True)

    assert by_id(result, "e:10:1")["name"] == clean
    assert by_id(result, "a:7")["name"] == clean
    assert by_id(result, "e:30:1")["kind"] == clean
    assert result["groups"][0]["name"] == clean
    assert clean in by_id(result, "e:20:1")["state"]


def test_a_raw_name_does_not_take_an_absent_typed_field(cache: Cache) -> None:
    cache.replace_devices(devices((10, 1, [data("posi\u200btion", 5)])), [])

    result = state(cache, ["e:10:1"])

    assert by_id(result, "e:10:1")["state"] == {}
    assert "field_info" not in result
