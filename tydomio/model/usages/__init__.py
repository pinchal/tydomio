"""Typed models of the Tydom data, one per device usage, and one for areas.

Add a usage: write its module with a `UsageModel` subclass, then register it in
`USAGES` under its Tydom `last_usage` name. A usage that may be written also gets a
`UsageWrite` subclass, registered in `WRITES`, and its writable fields left out are
listed in `NOT_WRITTEN` with the reason.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from tydomio.model.data import Data

from .area import Area, AreaWrite
from .base import Reading, TydomEnum, UsageModel, UsageWrite
from .boiler import Boiler, BoilerWrite
from .conso import Conso
from .re2020_control_boiler import Re2020ControlBoiler, Re2020ControlBoilerWrite
from .sensor_sun import SensorSun
from .shutter import Shutter, ShutterWrite
from .weather import Weather

__all__ = [
    "NOT_WRITTEN",
    "USAGES",
    "WRITES",
    "Area",
    "Reading",
    "TydomEnum",
    "UsageModel",
    "UsageWrite",
    "parse_usage",
]

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

WRITES: Final[Mapping[type[UsageModel], type[UsageWrite[UsageModel]]]] = (
    MappingProxyType(
        {
            Area: AreaWrite,
            Boiler: BoilerWrite,
            Re2020ControlBoiler: Re2020ControlBoilerWrite,
            Shutter: ShutterWrite,
        }
    )
)
"""Read model to the only write model accepted for its endpoints or areas."""

_PAIRING = "pairing or installation set-up, not a use of the equipment"

NOT_WRITTEN: Final[Mapping[type[UsageModel], Mapping[str, str]]] = MappingProxyType(
    {
        Boiler: {
            "ambientTemperature": "a measure: writing it lies to the regulation",
            "localisation": _PAIRING,
            "modeAsso": _PAIRING,
        },
        Conso: {"reset": "command erasing the recorded values of the meter"},
        Re2020ControlBoiler: {
            "name": "the probe's name is installation set-up",
            "sendAsso": _PAIRING,
            "sendNetRemoteAssoMode": _PAIRING,
        },
        Shutter: {
            "localisation": _PAIRING,
            "modeAsso": _PAIRING,
            "recFav": "overwrites a favourite position, installation set-up",
        },
    }
)
"""Writable fields and commands of the catalog not exposed, by usage, with why."""


def parse_usage(usage: str, data: Iterable[Data]) -> UsageModel | None:
    """Typed model of an endpoint's data, `None` for a usage without a model."""
    model = USAGES.get(usage)
    return None if model is None else model.from_data(data)
