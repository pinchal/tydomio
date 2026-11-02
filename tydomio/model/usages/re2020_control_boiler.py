"""RE2020 room thermostat: fields of the `re2020ControlBoiler` usage."""

from pydantic import Field

from .base import Reading, TydomEnum, UsageModel, UsageWrite


class ShutterCmd(TydomEnum):
    """Values of `shutterCmd`."""

    DOWN = "DOWN"
    STOP = "STOP"
    UP = "UP"


class Re2020ControlBoiler(UsageModel):
    """RE2020 room thermostat."""

    ambient_temperature: Reading[float] | None = Field(
        default=None, alias="ambientTemperature"
    )
    batt_level: Reading[float] | None = Field(default=None, alias="battLevel")
    hygro_in: Reading[float] | None = Field(default=None, alias="hygroIn")
    is_reference: Reading[bool] | None = Field(default=None, alias="isReference")
    jobs: Reading[float] | None = Field(default=None, alias="jobs")
    jobs_mp: Reading[float] | None = Field(default=None, alias="jobsMP")
    jobs_rm: Reading[float] | None = Field(default=None, alias="jobsRM")
    nb_rf_actuators: Reading[float] | None = Field(default=None, alias="nbRFActuators")
    selected_prog: Reading[float] | None = Field(default=None, alias="selectedProg")
    shutter_cmd: Reading[ShutterCmd | str] | None = Field(
        default=None, alias="shutterCmd"
    )
    soft_plan: Reading[str] | None = Field(default=None, alias="softPlan")
    soft_version: Reading[str] | None = Field(default=None, alias="softVersion")
    synchro_radio: Reading[bool] | None = Field(default=None, alias="synchroRadio")
    uid: Reading[str] | None = Field(default=None, alias="uid")


class Re2020ControlBoilerWrite(UsageWrite[Re2020ControlBoiler]):
    """Fields written to a RE2020 room thermostat."""

    selected_prog: int | None = Field(default=None, alias="selectedProg", ge=0, le=4)
