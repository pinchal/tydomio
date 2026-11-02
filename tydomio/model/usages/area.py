"""Heating area: fields of an area in `/areas/data`."""

from pydantic import Field

from .base import IndoorHumidity, Reading
from .thermic import ThermicWrite, ThermicZone


class Area(ThermicZone):
    """Heating area."""

    anticip_coeff: Reading[float] | None = Field(
        default=None, alias="anticipCoeff", json_schema_extra={"unit": "min/deg"}
    )
    hygro_in: IndoorHumidity


class AreaWrite(ThermicWrite[Area]):
    """Fields written to a heating area."""
