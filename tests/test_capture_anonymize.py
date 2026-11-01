from collections.abc import Callable

import pytest

from tydomio.capture.anonymize import (
    NOT_IN_ENUM,
    REDACTED,
    Anonymized,
    RenamedKey,
    UnknownLeaf,
    anonymize,
)
from tydomio.capture.jsonvalue import JsonValue

CONFIGS = "/configs/file"
DEVICES_META = "/devices/meta"
DEVICES_DATA = "/devices/data"
AREAS_META = "/areas/meta"
AREAS_DATA = "/areas/data"


def run(*bodies: tuple[str, JsonValue], status: int = 200) -> Anonymized:
    return anonymize(dict(bodies), {url: status for url, _ in bodies})


def body_of(url: str, body: JsonValue) -> JsonValue:
    return run((url, body)).bodies[url]


def as_dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def as_list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def reasons(result: Anonymized) -> list[tuple[str, str]]:
    return [(leaf.path, leaf.reason) for leaf in result.unclassified]


@pytest.mark.parametrize(
    ("body", "expected"),
    [({"version": 21}, {"version": 0}), ({"version": "2 1"}, {"version": REDACTED})],
    ids=["type", "pattern"],
)
def test_kept_leaf_failing_its_constraint_is_redacted_and_reported(
    body: JsonValue, expected: JsonValue
) -> None:
    result = run((CONFIGS, body))

    assert (result.bodies[CONFIGS], reasons(result)) == (
        expected,
        [("version", "keep-constraint")],
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1234-abcd", REDACTED), (1700, 0), (1.5, 0.0), (True, False), (None, None)],
)
def test_redacted_leaf_keeps_its_type(value: JsonValue, expected: JsonValue) -> None:
    redacted = as_dict(body_of(CONFIGS, {"date": value}))["date"]

    assert (redacted, type(redacted)) == (expected, type(expected))


def test_unknown_leaf_is_redacted_and_reported_with_its_type() -> None:
    result = run((CONFIGS, {"zigbee_networks": [{"channel": "ab"}, {"channel": 3}]}))

    assert (result.bodies[CONFIGS], result.unclassified) == (
        {"zigbee_networks": [{"channel": REDACTED}, {"channel": 0}]},
        (
            UnknownLeaf(CONFIGS, "zigbee_networks[].channel", "str", "no-rule"),
            UnknownLeaf(CONFIGS, "zigbee_networks[].channel", "int", "no-rule"),
        ),
    )


def test_user_names_become_tokens_equal_for_equal_names() -> None:
    body: JsonValue = {
        "endpoints": [{"name": "Attic"}, {"name": "Den"}, {"name": "Attic"}]
    }

    assert body_of(CONFIGS, body) == {
        "endpoints": [
            {"name": "<name:endpoint:1>"},
            {"name": "<name:endpoint:2>"},
            {"name": "<name:endpoint:1>"},
        ]
    }


def first_device_id(body: JsonValue) -> JsonValue:
    return as_dict(as_list(body)[0])["id"]


@pytest.mark.parametrize(
    ("url", "body", "mapped"),
    [
        (DEVICES_META, [{"id": 4321}], first_device_id),
        (AREAS_DATA, [{"id": 4321}], first_device_id),
        (
            "/groups/file",
            {"groups": [{"id": 4321}]},
            lambda body: as_dict(as_list(as_dict(body)["groups"])[0])["id"],
        ),
        (
            "/scenarios/file",
            {"scn": [{"grpAct": [{"id": 4321}]}]},
            lambda body: as_dict(
                as_list(as_dict(as_list(as_dict(body)["scn"])[0])["grpAct"])[0]
            )["id"],
        ),
        (
            "/moments/file",
            {"event": [{"progId": 4321}]},
            lambda body: as_dict(as_list(as_dict(body)["event"])[0])["progId"],
        ),
        (
            "/info",
            {"weather": {"outdoor": {"devId": 4321}}},
            lambda body: as_dict(
                next(iter(as_dict(as_dict(body)["weather"]).values()))
            )["devId"],
        ),
    ],
)
def test_same_raw_id_gives_the_same_value_across_endpoints(
    url: str, body: JsonValue, mapped: Callable[[JsonValue], JsonValue]
) -> None:
    config: JsonValue = {"endpoints": [{"id_device": 77}, {"id_device": 4321}]}

    result = run((CONFIGS, config), (url, body))

    assert mapped(result.bodies[url]) == 2


def test_link_id_maps_like_the_area_id_it_references() -> None:
    config: JsonValue = {"areas": [{"id": 77}, {"id": 4321}]}
    meta: JsonValue = [
        {"endpoints": [{"link": {"type": "area", "subtype": "x", "id": 4321}}]}
    ]

    result = run((CONFIGS, config), (DEVICES_META, meta))

    endpoint = as_dict(
        as_list(as_dict(as_list(result.bodies[DEVICES_META])[0])["endpoints"])[0]
    )
    assert as_dict(endpoint["link"])["id"] == 2


def test_str_and_int_raw_ids_map_to_the_same_number_of_their_own_type() -> None:
    body: JsonValue = {"endpoints": [{"id_device": 123, "space_id": "123"}]}

    assert body_of(CONFIGS, body) == {"endpoints": [{"id_device": 1, "space_id": "1"}]}


def test_empty_string_id_is_kept_empty() -> None:
    body: JsonValue = {"endpoints": [{"space_id": ""}]}

    assert body_of(CONFIGS, body) == body


def test_scenario_action_name_and_value_are_kept() -> None:
    body: JsonValue = {
        "scn": [{"grpAct": [{"state": [{"name": "positionCmd", "value": "STOP"}]}]}]
    }

    assert body_of("/scenarios/file", body) == body


@pytest.mark.parametrize(
    ("body", "expected", "path"),
    [
        ({"groups": [{"id": 1.5}]}, {"groups": [{"id": 0.0}]}, "groups[].id"),
        (
            {"endpoints": [{"name": 3}]},
            {"endpoints": [{"name": 0}]},
            "endpoints[].name",
        ),
    ],
    ids=["float-id", "int-name"],
)
def test_id_or_name_of_an_unexpected_type_is_redacted_and_reported(
    body: JsonValue, expected: JsonValue, path: str
) -> None:
    result = run((CONFIGS, body))

    assert (result.bodies[CONFIGS], reasons(result)) == (
        expected,
        [(path, "keep-constraint")],
    )


DEVICE_META: JsonValue = [
    {
        "id": 7,
        "endpoints": [
            {"id": 8, "metadata": [{"name": "mode", "enum_values": ["ECO"]}]}
        ],
    }
]
AREA_META: JsonValue = [
    {"id": 3, "metadata": [{"name": "mode", "enum_values": ["ECO"]}]}
]


def device_data(name: str, value: JsonValue) -> JsonValue:
    return [
        {"id": 7, "endpoints": [{"id": 8, "data": [{"name": name, "value": value}]}]}
    ]


def area_data(name: str, value: JsonValue) -> JsonValue:
    return [{"id": 3, "data": [{"name": name, "value": value}]}]


def data_value(url: str, body: JsonValue) -> JsonValue:
    items = as_list(body)
    if url == DEVICES_DATA:
        items = as_list(as_dict(as_list(as_dict(items[0])["endpoints"])[0])["data"])
    else:
        items = as_list(as_dict(items[0])["data"])
    return as_dict(items[0])["value"]


# level -> meta url, meta body, data url, data body builder
LEVELS: dict[str, tuple[str, JsonValue, str, Callable[[str, JsonValue], JsonValue]]] = {
    "device": (DEVICES_META, DEVICE_META, DEVICES_DATA, device_data),
    "area": (AREAS_META, AREA_META, AREAS_DATA, area_data),
}


@pytest.mark.parametrize("level", list(LEVELS))
@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("mode", "ECO", "ECO"),
        ("mode", "Holiday at Ann", NOT_IN_ENUM),
        ("unknown", "Ann", REDACTED),
    ],
    ids=["in-enum", "not-in-enum", "no-enum"],
)
def test_data_string_is_kept_only_if_in_its_descriptor_enum_values(
    level: str, name: str, value: str, expected: str
) -> None:
    meta_url, meta, url, data = LEVELS[level]

    result = run((meta_url, meta), (url, data(name, value)))

    assert data_value(url, result.bodies[url]) == expected


@pytest.mark.parametrize("value", [True, False, None])
def test_bool_and_null_data_values_are_kept(value: JsonValue) -> None:
    body = body_of(DEVICES_DATA, device_data("on", value))

    assert data_value(DEVICES_DATA, body) is value


@pytest.mark.parametrize(
    ("value", "minimum", "expected"),
    [(215, 5, 5), (21.5, 5, 5.0), (215, 5.5, 5)],
    ids=["int", "float", "int-from-float-min"],
)
def test_numeric_data_value_becomes_its_descriptor_min_of_the_received_type(
    value: JsonValue, minimum: float, expected: JsonValue
) -> None:
    meta: JsonValue = [
        {
            "id": 7,
            "endpoints": [{"id": 8, "metadata": [{"name": "temp", "min": minimum}]}],
        }
    ]

    result = run((DEVICES_META, meta), (DEVICES_DATA, device_data("temp", value)))

    replaced = data_value(DEVICES_DATA, result.bodies[DEVICES_DATA])
    assert (replaced, type(replaced)) == (expected, type(expected))


@pytest.mark.parametrize(("value", "expected"), [(215, 0), (21.5, 0.0)])
def test_numeric_data_value_without_descriptor_min_is_zeroed_keeping_its_type(
    value: JsonValue, expected: JsonValue
) -> None:
    result = run(
        (DEVICES_META, DEVICE_META), (DEVICES_DATA, device_data("mode", value))
    )

    zeroed = data_value(DEVICES_DATA, result.bodies[DEVICES_DATA])
    assert (zeroed, type(zeroed)) == (expected, type(expected))


@pytest.mark.parametrize(
    ("url", "body", "out_key", "renamed"),
    [
        (CONFIGS, {"version": "1"}, "version", ()),
        (CONFIGS, {"new_Field2": 1}, "new_Field2", ()),
        (CONFIGS, {"my room": 1}, "<key-1>", (RenamedKey(CONFIGS, "<key-1>"),)),
        ("/info", {"TYDOM.dat": 1}, "TYDOM.dat", ()),
        (
            "/info",
            {"geoloc": {"latitude": 1}},
            "<key-1>",
            (RenamedKey("/info", "geoloc.<key-1>"),),
        ),
    ],
    ids=["declared", "identifier", "not-identifier", "suffix-rule", "opaque"],
)
def test_keys_are_kept_when_declared_or_identifiers_else_renamed(
    url: str, body: JsonValue, out_key: str, renamed: tuple[RenamedKey, ...]
) -> None:
    result = run((url, body))

    output = as_dict(result.bodies[url])
    if "geoloc" in output:
        output = as_dict(output["geoloc"])
    assert (list(output), result.renamed_keys) == ([out_key], renamed)


def test_http_error_body_has_every_leaf_unknown() -> None:
    result = run((CONFIGS, {"version": "2.0"}), status=404)

    assert (result.bodies[CONFIGS], reasons(result)) == (
        {"version": REDACTED},
        [("version", "no-rule")],
    )


# every string leaf and key holds a canary; those named Keep must survive, no other
CANARY_BODIES: dict[str, JsonValue] = {
    CONFIGS: {
        "date": "cnryGoneDate",
        "version": "cnryKeepVersion",
        "version_application": "cnryKeepVa",
        "os": "cnryKeepOs",
        "id_catalog": "cnryGoneCatalog",
        "Cnry_Keep_Key": "cnryGoneX",
        "endpoints": [
            {
                "id_endpoint": "cnryGoneIdE",
                "id_device": "cnryGoneIdD",
                "space_id": "cnryGoneSpace",
                "name": "cnryGoneName",
                "picto": "cnryKeepPicto",
                "first_usage": "cnryKeepFirst",
                "last_usage": "cnryKeepLast",
                "skill": "cnryKeepSkill",
                "widget_behavior": {
                    "tutorial_id": "cnryKeep+Tuto",
                    "cnryKeepKey": "cnryGoneU",
                },
            }
        ],
        "scenarios": [
            {
                "id": "cnryGoneS",
                "rule_id": "cnryGoneR",
                "name": "cnryGoneN",
                "type": "cnryKeepType",
                "picto": "cnryKeepSp",
            }
        ],
        "groups": [
            {
                "id": "cnryGoneG",
                "name": "cnryGoneGn",
                "usage": "cnryKeepUsage",
                "picto": "cnryKeepGp",
            }
        ],
        "areas": [
            {
                "id": "cnryGoneA",
                "name": "cnryGoneAn",
                "picto": "cnryKeepAp",
                "first_usage": "cnryKeepAf",
                "last_usage": "cnryKeepAl",
            }
        ],
        "moments": [{"id": "cnryGoneM", "rule_id": "cnryGoneMr", "name": "cnryGoneMn"}],
        "zigbee_networks": [
            {
                "name": "cnryGoneZ",
                "extended_pan_id": "cnryGonePan",
                "type": "cnryKeepZt",
            }
        ],
    },
    DEVICES_META: [
        {
            "id": "cnryGoneDev",
            "endpoints": [
                {
                    "id": "cnryGoneEp",
                    "link": {
                        "type": "cnryKeepLt",
                        "subtype": "cnryKeepLs",
                        "id": "cnryGoneLid",
                    },
                    "metadata": [
                        {
                            "name": "cnryKeepMeta",
                            "type": "cnryKeepMt",
                            "permission": "cnryKeepPerm",
                            "validity": "cnryKeepValid",
                            "unit": "cnryKeepUnit",
                            "enum_values": ["cnryKeepEnum"],
                            "Cnry_Keep_Key": "cnryGoneU",
                        }
                    ],
                }
            ],
        }
    ],
    "/devices/cmeta": [
        {
            "id": "cnryGoneDev",
            "endpoints": [
                {
                    "id": "cnryGoneEp",
                    "cmetadata": [
                        {
                            "name": "cnryKeepCmd",
                            "permission": "cnryKeepCp",
                            "parameters": [
                                {"name": "cnryKeepParam", "enum_values": ["cnryKeepPe"]}
                            ],
                        }
                    ],
                }
            ],
        }
    ],
    DEVICES_DATA: [
        {
            "id": "cnryGoneDev",
            "endpoints": [
                {
                    "id": "cnryGoneEp",
                    "data": [
                        {
                            "name": "cnryKeepData",
                            "validity": "cnryKeepDv",
                            "value": "cnryGoneValue",
                        }
                    ],
                }
            ],
        }
    ],
    AREAS_META: [
        {
            "id": "cnryGoneArea",
            "metadata": [{"name": "cnryKeepAm", "unit": "cnryKeepAu"}],
        }
    ],
    "/areas/cmeta": [
        {
            "id": "cnryGoneArea",
            "cmetadata": [
                {"name": "cnryKeepAc", "parameters": [{"name": "cnryKeepAcp"}]}
            ],
        }
    ],
    AREAS_DATA: [
        {"id": "cnryGoneArea", "data": [{"name": "cnryKeepAd", "value": "cnryGoneAv"}]}
    ],
    "/groups/file": {
        "groups": [
            {
                "id": "cnryGoneG",
                "devices": [{"id": "cnryGoneGd", "endpoints": [{"id": "cnryGoneGe"}]}],
                "areas": [{"id": "cnryGoneGa"}],
                "cnryKeepGk": "cnryGoneGv",
            }
        ]
    },
    "/scenarios/file": {
        "scn": [
            {
                "id": "cnryGoneS",
                "name": "cnryGoneSn",
                "grpAct": [
                    {
                        "id": "cnryGoneSg",
                        "state": [
                            {
                                "name": "cnryKeepStn",
                                "value": "cnryKeepStv",
                                "cnryKeepStk": "cnryGoneSt",
                            }
                        ],
                    }
                ],
            }
        ]
    },
    "/moments/file": {
        "prog": [
            {
                "id": "cnryGoneP",
                "cnryGonePk": "cnryGonePv",
                "rRule": [{"cnryGoneRk": "cnryGoneRv"}],
            }
        ],
        "event": [{"progId": "cnryGonePi", "cnryGoneEk": "cnryGoneEv"}],
        "rdv": [{"id": "cnryGoneRd"}],
        "mom": [{"id": "cnryGoneMo"}],
    },
    "/protocols": [
        {
            "protocol": "cnryKeepProto",
            "available": True,
            "installed": True,
            "ready": False,
            "status": "cnryKeepStatus",
            "installStatus": "cnryKeepInstall",
            "cnryKeepPk": "cnryGonePu",
        }
    ],
    "/info": {
        "productName": "cnryKeep Product",
        "apiMode": True,
        "config": "cnryKeepConfig",
        "bddStatus": 3,
        "cnryKeepCounter.dat": 2,
        "cnryKeepCounter.json": 2,
        "local_claim": {"status": "cnryKeepClaim", "lastAccess": "cnryGoneLa"},
        "mainVersionHW": "cnryKeepHw",
        "zigbeeVersionStack": "cnryKeepStack",
        "mac": "cnryGoneMac",
        "mainVersionSW": "cnryKeepSw",
        "mainReference": "cnryKeepRef",
        "keyReference": "cnryKeepKr",
        "zigbeeReference": "cnryKeepZr",
        "bootReference": "cnryKeepBr",
        "javaVersion": "cnryKeepJv",
        "bootVersion": "cnryKeepBv",
        "oryxVersion": "cnryKeep-Ov",
        "serial": "cnryGoneSerial",
        "geoloc": {"cnryGoneLk": "cnryGoneLat"},
        "weather": {
            "cnryGoneWk": {
                "devId": "cnryGoneWd",
                "epId": "cnryGoneWe",
                "cnryGoneWx": "x",
            }
        },
        "clock": {
            "timezone": "cnryGoneTz",
            "source": "cnryKeepSrc",
            "summerOffset": "cnryKeepSo",
        },
        "protocols": [
            {
                "protocol": "cnryKeepIp",
                "available": True,
                "installed": True,
                "ready": False,
                "status": "cnryKeepIs",
                "installStatus": "cnryKeepIi",
            }
        ],
    },
}


def canaries(value: JsonValue) -> set[str]:
    if isinstance(value, dict):
        found = {key for key in value if "cnry" in key.lower()}
        return found.union(*(canaries(item) for item in value.values()))
    if isinstance(value, list):
        return set[str]().union(*(canaries(item) for item in value))
    return {value} if isinstance(value, str) and "cnry" in value.lower() else set()


@pytest.mark.parametrize("url", list(CANARY_BODIES))
def test_only_canaries_at_kept_paths_and_keys_survive(url: str) -> None:
    body = CANARY_BODIES[url]

    survived = canaries(body_of(url, body))

    assert survived == {canary for canary in canaries(body) if "Keep" in canary}
