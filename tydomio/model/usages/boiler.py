"""Heating zone: fields of the `boiler` usage."""

from pydantic import Field

from .base import Reading
from .thermic import ThermicWrite, ThermicZone


class Boiler(ThermicZone):
    """Heating zone."""

    battery_cmd_defect: Reading[bool] | None = Field(
        default=None, alias="batteryCmdDefect"
    )
    load_shedding_on: Reading[bool] | None = Field(default=None, alias="loadSheddingOn")
    production_defect: Reading[bool] | None = Field(
        default=None, alias="productionDefect"
    )
    temp_sensor_defect: Reading[bool] | None = Field(
        default=None, alias="tempSensorDefect"
    )
    temp_sensor_open_circ: Reading[bool] | None = Field(
        default=None, alias="tempSensorOpenCirc"
    )
    temp_sensor_short_cut: Reading[bool] | None = Field(
        default=None, alias="tempSensorShortCut"
    )
    temperature: Reading[float] | None = Field(default=None, alias="temperature")


class BoilerWrite(ThermicWrite[Boiler]):
    """Fields written to a heating zone."""
