"""Builders shared by the MCP server tests."""

from tydomio.model.config import Config
from tydomio.model.data import AreasData, Data, DevicesData, Endpoint, ModelItem


def config(
    endpoints: dict[tuple[int, int], tuple[str, str]],
    area_map: dict[int, tuple[str, str]] | None = None,
    group_map: dict[int, tuple[str, str]] | None = None,
) -> Config:
    """Build a config: endpoints (device, endpoint), areas, groups to (name, usage)."""
    return Config.model_validate(
        {
            "date": 1,
            "version_application": "1.0",
            "endpoints": [
                {
                    "id_endpoint": endpoint_id,
                    "first_usage": usage,
                    "skill": "skill",
                    "id_device": device_id,
                    "name": name,
                    "anticipation_start": False,
                    "picto": "picto",
                    "last_usage": usage,
                    "widget_behavior": {"tutorial_id": "tutorial"},
                }
                for (device_id, endpoint_id), (name, usage) in endpoints.items()
            ],
            "old_tycam": False,
            "moments": [],
            "os": "tydom",
            "groups": [
                {
                    "group_all": False,
                    "usage": usage,
                    "name": name,
                    "id": group_id,
                    "is_group_user": False,
                    "picto": "picto",
                }
                for group_id, (name, usage) in (group_map or {}).items()
            ],
            "areas": [
                {
                    "first_usage": usage,
                    "name": name,
                    "id": area_id,
                    "anticipation_start": False,
                    "picto": "picto",
                    "last_usage": usage,
                }
                for area_id, (name, usage) in (area_map or {}).items()
            ],
            "scenarios": [],
            "id_catalog": "catalog",
            "version": "2.0",
            "zigbee_networks": [],
        }
    )


def data(name: str, value: float | str | None, validity: str = "upToDate") -> Data:
    return Data(name=name, validity=validity, value=value)


def devices(*items: tuple[int, int, list[Data]], error: int = 0) -> DevicesData:
    return DevicesData(
        [
            ModelItem(
                id=device_id,
                endpoints=[Endpoint(id=endpoint_id, error=error, data=fields)],
            )
            for device_id, endpoint_id, fields in items
        ]
    )


def areas(*items: tuple[int, list[Data]]) -> AreasData:
    return AreasData(
        [Endpoint(id=area_id, error=0, data=fields) for area_id, fields in items]
    )
