"""Sun sensor: fields of the `sensorSun` usage."""

from pydantic import Field

from .base import Reading, UsageModel


class SensorSun(UsageModel):
    """Sun sensor."""

    batt_defect: Reading[bool] | None = Field(default=None, alias="battDefect")
    config_sensor: Reading[float] | None = Field(default=None, alias="configSensor")
    config_temp: Reading[float] | None = Field(default=None, alias="configTemp")
    light_power: Reading[float] | None = Field(default=None, alias="lightPower")
