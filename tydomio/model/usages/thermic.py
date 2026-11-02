"""Thermal regulation: enums and fields shared by heating zones and areas."""

from typing import Annotated

from pydantic import Field

from .base import Reading, TydomEnum, UsageModel, UsageWrite, member_of


class Authorization(TydomEnum):
    """Values of `authorization`."""

    HEATING = "HEATING"
    STOP = "STOP"


class ComfortMode(TydomEnum):
    """Values of `comfortMode`."""

    HEATING = "HEATING"
    STOP = "STOP"


class LocalMode(TydomEnum):
    """Values of `localMode`."""

    ABSENCE = "ABSENCE"
    ANTI_FROST = "ANTI_FROST"
    NORMAL = "NORMAL"
    STOP = "STOP"


class ThermicLevel(TydomEnum):
    """Values of `thermicLevel` and `overrideThermicLevel`."""

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


class ThermicWrite[U: ThermicZone](UsageWrite[U]):
    """Regulation fields written to a `boiler` heating zone or an area."""

    comfort_mode: Annotated[ComfortMode, member_of(ComfortMode)] | None = Field(
        default=None, alias="comfortMode"
    )
    heat_setpoint: float | None = Field(
        default=None, alias="heatSetpoint", ge=1, le=50, multiple_of=0.5
    )
    local_mode: Annotated[LocalMode, member_of(LocalMode)] | None = Field(
        default=None, alias="localMode"
    )
    override_remaining_time: int | None = Field(
        default=None, alias="overrideRemainingTime", ge=0, le=720
    )
    override_setpoint: float | None = Field(
        default=None, alias="overrideSetpoint", ge=1, le=50, multiple_of=0.5
    )
    override_thermic_level: Annotated[ThermicLevel, member_of(ThermicLevel)] | None = (
        Field(default=None, alias="overrideThermicLevel")
    )
    setpoint: float | None = Field(
        default=None, alias="setpoint", ge=1, le=50, multiple_of=0.5
    )
    thermic_level: Annotated[ThermicLevel, member_of(ThermicLevel)] | None = Field(
        default=None, alias="thermicLevel"
    )
    use_mode: Annotated[UseMode, member_of(UseMode)] | None = Field(
        default=None, alias="useMode"
    )
