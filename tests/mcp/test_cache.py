from datetime import UTC, datetime

import pytest

from tests.mcp.conftest import areas, config, data, devices
from tydomio.mcp_server.cache import Cache, Connection, Entity

CONFIG = config(
    {(10, 1): ("Shutter", "shutter"), (20, 1): ("Boiler", "boiler")},
    {7: ("Living", "area")},
)


@pytest.fixture(name="cache")
def cache_with_config() -> Cache:
    cache = Cache()
    cache.apply_config(CONFIG)
    return cache


def names(entity: Entity) -> set[str]:
    return set(entity.fields)


def test_starts_connecting_and_not_synced() -> None:
    cache = Cache()

    assert (cache.connection, cache.synced, cache.synced_at) == (
        "connecting",
        False,
        None,
    )
    assert (cache.config, cache.installation) == (None, None)


def test_apply_config_builds_the_installation(cache: Cache) -> None:
    assert cache.config is CONFIG
    assert cache.installation is not None


def test_push_merges_field_by_field_and_keeps_absent_fields(cache: Cache) -> None:
    cache.replace_devices(
        devices((10, 1, [data("position", 10), data("thermicDefect", 0)])), []
    )

    cache.merge_devices(devices((10, 1, [data("position", 80)]), error=3))

    entity = cache.endpoints[(10, 1)]
    assert entity.fields["position"].value == 80
    assert entity.fields["thermicDefect"].value == 0
    assert entity.error == 3


def test_push_of_an_unseen_endpoint_creates_it(cache: Cache) -> None:
    cache.merge_devices(devices((20, 1, [data("temperature", 19.5)])))

    assert names(cache.endpoints[(20, 1)]) == {"temperature"}


def test_snapshot_replaces_the_map(cache: Cache) -> None:
    cache.replace_devices(
        devices((10, 1, [data("position", 10), data("thermicDefect", 0)])), []
    )

    cache.replace_devices(devices((20, 1, [data("temperature", 19)])), [])

    assert set(cache.endpoints) == {(20, 1)}
    cache.replace_devices(devices((20, 1, [data("setpoint", 20)])), [])
    assert names(cache.endpoints[(20, 1)]) == {"setpoint"}


def test_snapshot_drops_ids_not_in_the_config(cache: Cache) -> None:
    cache.replace_devices(
        devices((10, 1, [data("position", 10)]), (99, 9, [data("position", 1)])),
        [(98, 8)],
    )

    assert set(cache.endpoints) == {(10, 1)}


def test_push_drops_ids_not_in_the_config(cache: Cache, caplog) -> None:
    caplog.set_level("DEBUG")

    cache.merge_devices(devices((99, 9, [data("position", 1)])))

    assert cache.endpoints == {}
    assert "99" in caplog.text


def test_area_snapshot_replaces_and_push_merges(cache: Cache) -> None:
    cache.replace_areas(areas((7, [data("hygroIn", 40), data("a", 1)]), (8, [])), [9])
    assert set(cache.areas) == {7}

    cache.merge_areas(areas((7, [data("hygroIn", 45)]), (8, [data("a", 1)])))

    assert {k: v.value for k, v in cache.areas[7].fields.items()} == {
        "hygroIn": 45,
        "a": 1,
    }
    assert set(cache.areas) == {7}


def test_invalid_endpoint_from_a_snapshot_has_no_fields_and_a_push_clears_it(
    cache: Cache,
) -> None:
    cache.replace_devices(devices(), [(10, 1)])
    assert cache.endpoints[(10, 1)] == Entity({}, invalid=True)

    cache.merge_devices(devices((10, 1, [data("position", 5)])))

    entity = cache.endpoints[(10, 1)]
    assert not entity.invalid
    assert names(entity) == {"position"}


def test_invalid_area_from_a_snapshot_is_cleared_by_a_push(cache: Cache) -> None:
    cache.replace_areas(areas(), [7])
    assert cache.areas[7].invalid

    cache.merge_areas(areas((7, [data("hygroIn", 1)])))

    assert not cache.areas[7].invalid


def test_apply_config_drops_a_removed_id(cache: Cache) -> None:
    cache.replace_devices(
        devices((10, 1, [data("position", 1)]), (20, 1, [data("temperature", 1)])), []
    )
    cache.replace_areas(areas((7, [data("hygroIn", 1)])), [])

    cache.apply_config(config({(20, 1): ("Boiler", "boiler")}))

    assert set(cache.endpoints) == {(20, 1)}
    assert cache.areas == {}


def test_apply_config_drops_an_id_whose_usage_changed(cache: Cache) -> None:
    cache.replace_devices(
        devices((10, 1, [data("position", 1)]), (20, 1, [data("temperature", 1)])), []
    )
    cache.replace_areas(areas((7, [data("hygroIn", 1)])), [])

    cache.apply_config(
        config(
            {(10, 1): ("Shutter", "light"), (20, 1): ("Boiler", "boiler")},
            {7: ("Living", "other")},
        )
    )

    assert set(cache.endpoints) == {(20, 1)}
    assert cache.areas == {}


def test_apply_config_keeps_a_renamed_id(cache: Cache) -> None:
    cache.replace_devices(devices((10, 1, [data("position", 1)])), [])

    cache.apply_config(config({(10, 1): ("Renamed", "shutter")}))

    assert names(cache.endpoints[(10, 1)]) == {"position"}


@pytest.mark.parametrize(
    ("connect", "sync", "fresh"),
    [
        (False, False, False),
        (True, False, False),
        (True, True, True),
        (False, True, False),
    ],
)
def test_fresh_truth_table(cache: Cache, connect: bool, sync: bool, fresh: bool):
    if connect:
        cache.mark_connected()
    if sync:
        cache.mark_synced(datetime(2026, 1, 1, tzinfo=UTC))
    if connect:
        expected: Connection = "connected"
    else:
        expected = "connecting"

    assert cache.connection == expected
    assert cache.fresh is fresh


def test_mark_disconnected_keeps_the_data_and_unsyncs(cache: Cache) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache.replace_devices(devices((10, 1, [data("position", 1)])), [])
    cache.mark_connected()
    cache.mark_synced(now)

    cache.mark_disconnected()

    assert (cache.connection, cache.synced, cache.fresh) == (
        "disconnected",
        False,
        False,
    )
    assert names(cache.endpoints[(10, 1)]) == {"position"}
    assert cache.synced_at == now


def test_merge_edge_cases_raise_nothing(cache: Cache) -> None:
    cache.merge_devices(devices())
    cache.merge_areas(areas())
    cache.merge_devices(
        devices((10, 1, [data("position", 1)]), (10, 1, [data("position", 2)]))
    )
    cache.merge_areas(areas((7, [data("a", 1)]), (7, [data("a", 2)])))
    cache.merge_devices(devices((99, 9, [data("x", 1)])))
    cache.merge_areas(areas((99, [data("x", 1)])))

    assert cache.endpoints[(10, 1)].fields["position"].value == 2
    assert cache.areas[7].fields["a"].value == 2
