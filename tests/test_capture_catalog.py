import json
from pathlib import Path

from tydomio.capture.catalog import (
    AREA,
    AREAS_CMETA,
    AREAS_META,
    CONFIG,
    DEVICES_CMETA,
    DEVICES_META,
    EXIT_CONFLICT,
    EXIT_INVALID,
    EXIT_WRITTEN,
    Catalog,
    extract,
    main,
    merge,
)
from tydomio.capture.jsonvalue import JsonValue

# invented capture: two devices of the same usage, endpoint ids reused across devices
LEVEL: dict[str, JsonValue] = {
    "name": "level",
    "type": "numeric",
    "permission": "rw",
    "validity": "INFINITE",
    "shared": False,
    "min": 0,
    "max": 100,
    "step": 1,
    "unit": "%",
}
MODE: dict[str, JsonValue] = {
    "name": "mode",
    "type": "string",
    "permission": "rw",
    "validity": "INFINITE",
    "enum_values": ["ON", "OFF"],
}
DOCUMENTS: dict[str, JsonValue] = {
    CONFIG: {
        "body": {
            "endpoints": [
                {"id_device": 7, "id_endpoint": 1, "last_usage": "lamp"},
                {"id_device": 8, "id_endpoint": 1, "last_usage": "lamp"},
            ]
        }
    },
    DEVICES_META: {
        "body": [
            {"id": 7, "endpoints": [{"id": 1, "error": 0, "metadata": [LEVEL, MODE]}]},
            {"id": 8, "endpoints": [{"id": 1, "error": 0, "metadata": [MODE]}]},
        ]
    },
    DEVICES_CMETA: {
        "body": [
            {
                "id": 7,
                "endpoints": [
                    {"id": 1, "cmetadata": [{"name": "blink", "permission": "w"}]}
                ],
            }
        ]
    },
    AREAS_META: {"body": [{"id": 33, "metadata": [LEVEL]}]},
    AREAS_CMETA: {"body": []},
}


def lamp_mode(enum_values: list[str]) -> Catalog:
    return Catalog.model_validate(
        {
            "lamp": {
                "fields": [
                    {"name": "mode", "type": "string", "enum_values": enum_values}
                ]
            }
        }
    )


def test_extract_keeps_one_descriptor_per_field_without_ids() -> None:
    catalog, conflicts = extract(DOCUMENTS)

    assert conflicts == []
    assert json.loads(catalog.model_dump_json(exclude_none=True)) == {
        "lamp": {
            "fields": [
                {
                    "name": "level",
                    "type": "numeric",
                    "permission": "rw",
                    "validity": "INFINITE",
                    "unit": "%",
                    "min": 0,
                    "max": 100,
                    "step": 1,
                },
                {
                    "name": "mode",
                    "type": "string",
                    "permission": "rw",
                    "validity": "INFINITE",
                    "enum_values": ["OFF", "ON"],
                },
            ],
            "commands": [{"name": "blink", "permission": "w", "parameters": []}],
        },
        AREA: {
            "fields": [
                {
                    "name": "level",
                    "type": "numeric",
                    "permission": "rw",
                    "validity": "INFINITE",
                    "unit": "%",
                    "min": 0,
                    "max": 100,
                    "step": 1,
                }
            ],
            "commands": [],
        },
    }


def test_merge_unions_enum_values() -> None:
    merged, conflicts = merge(lamp_mode(["ON"]), lamp_mode(["OFF", "DIM"]))

    assert conflicts == []
    assert merged == lamp_mode(["DIM", "OFF", "ON"])


def test_main_writes_the_catalog_of_a_capture(tmp_path: Path) -> None:
    for name, document in DOCUMENTS.items():
        (tmp_path / name).write_text(json.dumps(document))
    catalog_file = tmp_path / "catalog.json"

    assert main([str(tmp_path), str(catalog_file)]) == EXIT_WRITTEN
    assert (
        Catalog.model_validate_json(catalog_file.read_text()) == extract(DOCUMENTS)[0]
    )


def test_main_on_conflict_writes_nothing(tmp_path: Path) -> None:
    for name, document in DOCUMENTS.items():
        (tmp_path / name).write_text(json.dumps(document))
    catalog_file = tmp_path / "catalog.json"
    known = '{"lamp": {"fields": [{"name": "level", "type": "string"}]}}'
    catalog_file.write_text(known)

    assert main([str(tmp_path), str(catalog_file)]) == EXIT_CONFLICT
    assert catalog_file.read_text() == known


def test_main_rejects_a_capture_missing_a_document(tmp_path: Path) -> None:
    catalog_file = tmp_path / "catalog.json"

    assert main([str(tmp_path), str(catalog_file)]) == EXIT_INVALID
    assert not catalog_file.exists()
