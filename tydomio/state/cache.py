"""The state of the Tydom as the MCP server knows it: raw `Data` by id, no I/O."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..installation import Installation
from ..model.config import Config
from ..model.data import AreasData, Data, DevicesData
from ..model.data import Endpoint as DataEndpoint

_logger = logging.getLogger(__name__)

type Connection = Literal["connecting", "connected", "disconnected"]


class CacheError(Exception):
    """A cache failure; not a RuntimeError, so the client does not swallow it."""


@dataclass
class Entity:
    """The cached fields of one endpoint or area, by Tydom field name."""

    fields: dict[str, Data]
    error: int = 0
    invalid: bool = False


def _usages(config: Config) -> tuple[dict[tuple[int, int], str], dict[int, str]]:
    return (
        {(e.id_device, e.id_endpoint): e.last_usage for e in config.endpoints},
        {a.id: a.last_usage for a in config.areas},
    )


def _merge(entity: Entity, item: DataEndpoint) -> None:
    for field in item.data:
        entity.fields[field.name] = field
    entity.error = item.error
    entity.invalid = False


class Cache:
    """Raw Tydom data keyed by ids, filtered by the config, with its freshness."""

    def __init__(self) -> None:
        """Start empty, connecting and not synced."""
        self.config: Config | None = None
        self.installation: Installation | None = None
        self.endpoints: dict[tuple[int, int], Entity] = {}
        self.areas: dict[int, Entity] = {}
        self.connection: Connection = "connecting"
        self.synced = False
        self.synced_at: datetime | None = None
        self._endpoint_usages: dict[tuple[int, int], str] = {}
        self._area_usages: dict[int, str] = {}

    @property
    def fresh(self) -> bool:
        """Whether connected with a snapshot applied."""
        return self.connection == "connected" and self.synced

    def apply_config(self, config: Config) -> None:
        """Take the config, dropping the state of ids removed or whose usage changed."""
        endpoint_usages, area_usages = _usages(config)
        self.endpoints = {
            key: entity
            for key, entity in self.endpoints.items()
            if endpoint_usages.get(key) == self._endpoint_usages.get(key)
            and key in endpoint_usages
        }
        self.areas = {
            key: entity
            for key, entity in self.areas.items()
            if area_usages.get(key) == self._area_usages.get(key) and key in area_usages
        }
        self._endpoint_usages = endpoint_usages
        self._area_usages = area_usages
        self.config = config
        self.installation = Installation.from_config(config)

    def replace_devices(
        self, data: DevicesData, invalid: Iterable[tuple[int, int]]
    ) -> None:
        """Replace the endpoints by a snapshot; invalid ones have no fields."""
        endpoints: dict[tuple[int, int], Entity] = {}
        for item in data.root:
            for endpoint in item.endpoints:
                key = (item.id, endpoint.id)
                if key in self._endpoint_usages:
                    entity = Entity({})
                    _merge(entity, endpoint)
                    endpoints[key] = entity
        for key in invalid:
            if key in self._endpoint_usages:
                endpoints[key] = Entity({}, invalid=True)
        self.endpoints = endpoints

    def replace_areas(self, data: AreasData, invalid: Iterable[int]) -> None:
        """Replace the areas by a snapshot; invalid ones have no fields."""
        areas: dict[int, Entity] = {}
        for item in data.root:
            if item.id in self._area_usages:
                entity = Entity({})
                _merge(entity, item)
                areas[item.id] = entity
        for area_id in invalid:
            if area_id in self._area_usages:
                areas[area_id] = Entity({}, invalid=True)
        self.areas = areas

    def merge_devices(self, data: DevicesData) -> None:
        """Upsert the pushed fields of the endpoints of the config."""
        for item in data.root:
            for endpoint in item.endpoints:
                key = (item.id, endpoint.id)
                if key not in self._endpoint_usages:
                    _logger.debug("Push for an endpoint not in the config: %s", key)
                    continue
                _merge(self.endpoints.setdefault(key, Entity({})), endpoint)

    def merge_areas(self, data: AreasData) -> None:
        """Upsert the pushed fields of the areas of the config."""
        for item in data.root:
            if item.id not in self._area_usages:
                _logger.debug("Push for an area not in the config: %s", item.id)
                continue
            _merge(self.areas.setdefault(item.id, Entity({})), item)

    def mark_connected(self) -> None:
        """Note the connection is up."""
        self.connection = "connected"

    def mark_synced(self, now: datetime) -> None:
        """Note a snapshot was applied at this time."""
        self.synced = True
        self.synced_at = now

    def mark_disconnected(self) -> None:
        """Note the connection is lost, keeping the data but no longer synced."""
        self.connection = "disconnected"
        self.synced = False
