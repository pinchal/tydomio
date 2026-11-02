"""Heating area: fields of an area in `/areas/data`."""

from pydantic import Field

from .base import Reading
from .thermic import ThermicWrite, ThermicZone


class Area(ThermicZone):
    """Heating area."""

    anticip_coeff: Reading[float] | None = Field(default=None, alias="anticipCoeff")
    hygro_in: Reading[float] | None = Field(default=None, alias="hygroIn")


class AreaWrite(ThermicWrite[Area]):
    """Fields written to a heating area."""
