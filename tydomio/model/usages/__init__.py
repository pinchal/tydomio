"""Typed read models of the Tydom data, one per device usage, and one for areas.

Add a usage: write its module with a `UsageModel` subclass, then register it in
`USAGES` under its Tydom `last_usage` name.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from tydomio.model.data import Data

from .area import Area
from .base import Reading, TydomEnum, UsageModel
from .boiler import Boiler
from .conso import Conso
from .re2020_control_boiler import Re2020ControlBoiler
from .sensor_sun import SensorSun
from .shutter import Shutter
from .weather import Weather

__all__ = ["USAGES", "Area", "Reading", "TydomEnum", "UsageModel", "parse_usage"]

USAGES: Final[Mapping[str, type[UsageModel]]] = MappingProxyType(
    {
        "boiler": Boiler,
        "conso": Conso,
        "re2020ControlBoiler": Re2020ControlBoiler,
        "sensorSun": SensorSun,
        "shutter": Shutter,
        "weather": Weather,
    }
)


def parse_usage(usage: str, data: Iterable[Data]) -> UsageModel | None:
    """Typed model of an endpoint's data, `None` for a usage without a model."""
    model = USAGES.get(usage)
    return None if model is None else model.from_data(data)
