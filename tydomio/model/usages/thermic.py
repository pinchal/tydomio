"""Thermal regulation: enums and fields shared by heating zones and areas."""

from pydantic import Field

from .base import Reading, TydomEnum, UsageModel


class Authorization(TydomEnum):
    """Values of `authorization`."""

    HEATING = "HEATING"
    STOP = "STOP"


class LocalMode(TydomEnum):
    """Values of `localMode`."""

    ABSENCE = "ABSENCE"
    ANTI_FROST = "ANTI_FROST"
    NORMAL = "NORMAL"
    STOP = "STOP"


class ThermicLevel(TydomEnum):
    """Values of `overrideThermicLevel`."""

    ANTI_FROST = "ANTI_FROST"
    STOP = "STOP"


class UseMode(TydomEnum):
    """Values of `useMode`."""

    MANUAL = "MANUAL"
    OVERRIDE = "OVERRIDE"
    SCHED = "SCHED"


class ThermicZone(UsageModel):
    """Regulation fields shared by a `boiler` heating zone and an area."""

    ambient_temperature: Reading[float] | None = Field(
        default=None, alias="ambientTemperature"
    )
    authorization: Reading[Authorization | str] | None = Field(
        default=None, alias="authorization"
    )
    heat_setpoint: Reading[float] | None = Field(default=None, alias="heatSetpoint")
    local_mode: Reading[LocalMode | str] | None = Field(default=None, alias="localMode")
    max_heat_setpoint: Reading[float] | None = Field(
        default=None, alias="maxHeatSetpoint"
    )
    max_setpoint: Reading[float] | None = Field(default=None, alias="maxSetpoint")
    min_heat_setpoint: Reading[float] | None = Field(
        default=None, alias="minHeatSetpoint"
    )
    min_setpoint: Reading[float] | None = Field(default=None, alias="minSetpoint")
    opening_detected: Reading[bool] | None = Field(
        default=None, alias="openingDetected"
    )
    override_remaining_time: Reading[float] | None = Field(
        default=None, alias="overrideRemainingTime"
    )
    override_setpoint: Reading[float] | None = Field(
        default=None, alias="overrideSetpoint"
    )
    override_thermic_level: Reading[ThermicLevel | str] | None = Field(
        default=None, alias="overrideThermicLevel"
    )
    setpoint: Reading[float] | None = Field(default=None, alias="setpoint")
    thermic_level: Reading[ThermicLevel | str] | None = Field(
        default=None, alias="thermicLevel"
    )
    use_mode: Reading[UseMode | str] | None = Field(default=None, alias="useMode")
