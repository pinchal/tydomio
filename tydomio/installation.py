"""The installation described by `/configs/file`: the usage of each endpoint, the areas.

It gives the references a write is addressed to, and types pushed data by usage.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Self

from .error import InvalidTarget
from .model.config import Config
from .model.data import AreasData, DevicesData
from .model.usages import USAGES, Area, UsageModel, parse_usage


def _check_id(value: object) -> None:
    # the ids go into the url of a request: ints only, never strings
    if type(value) is not int:
        raise InvalidTarget("An id must be an int")


@dataclass(frozen=True)
class EndpointRef[U: UsageModel]:
    """An endpoint of the installation, known to have the usage of model U."""

    device_id: int
    endpoint_id: int
    usage: type[U]

    def __post_init__(self) -> None:
        """Refuse ids that are not ints."""
        _check_id(self.device_id)
        _check_id(self.endpoint_id)


@dataclass(frozen=True)
class AreaRef:
    """An area of the installation."""

    area_id: int

    def __post_init__(self) -> None:
        """Refuse an id that is not an int."""
        _check_id(self.area_id)


class Installation:
    """Endpoints by (device id, endpoint id), as endpoint ids repeat across devices."""

    def __init__(self, endpoints: Mapping[tuple[int, int], str], areas: Iterable[int]):
        """Know these endpoints, with their usage name, and these area ids."""
        self._endpoints = dict(endpoints)
        self._areas = frozenset(areas)

    @classmethod
    def from_config(cls, config: Config) -> Self:
        """Build the installation of a `/configs/file`."""
        return cls(
            {
                (endpoint.id_device, endpoint.id_endpoint): endpoint.last_usage
                for endpoint in config.endpoints
            },
            (area.id for area in config.areas),
        )

    def endpoint[U: UsageModel](
        self, device_id: int, endpoint_id: int, usage: type[U]
    ) -> EndpointRef[U]:
        """Give the endpoint's reference; InvalidTarget if unknown or another usage."""
        name = self._usage(device_id, endpoint_id)
        if USAGES.get(name) is not usage:
            raise InvalidTarget(f"The endpoint is a {name}, not a {usage.__name__}")
        return EndpointRef(device_id, endpoint_id, usage)

    def area(self, area_id: int) -> AreaRef:
        """Give the area's reference; InvalidTarget if unknown."""
        if area_id not in self._areas:
            raise InvalidTarget("Unknown area")
        return AreaRef(area_id)

    def read_endpoint(
        self, data: DevicesData, device_id: int, endpoint_id: int
    ) -> UsageModel | None:
        """Typed data of one endpoint in pushed devices data.

        `None` when the push does not hold the endpoint or its usage has no model;
        InvalidTarget when the installation does not have the endpoint.
        """
        usage = self._usage(device_id, endpoint_id)
        for device in data.root:
            if device.id != device_id:
                continue
            for endpoint in device.endpoints:
                if endpoint.id == endpoint_id:
                    return parse_usage(usage, endpoint.data)
        return None

    def read_area(self, data: AreasData, area_id: int) -> Area | None:
        """Typed data of one area in pushed areas data, `None` if not held."""
        self.area(area_id)
        for area in data.root:
            if area.id == area_id:
                return Area.from_data(area.data)
        return None

    def _usage(self, device_id: int, endpoint_id: int) -> str:
        name = self._endpoints.get((device_id, endpoint_id))
        if name is None:
            raise InvalidTarget("Unknown endpoint")
        return name
