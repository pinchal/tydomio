"""Energy meter: fields of the `conso` usage."""

from pydantic import Field

from .base import Reading, UsageModel


class Conso(UsageModel):
    """Energy meter."""

    energy_index_ti1: Reading[float] | None = Field(
        default=None, alias="energyIndexTi1", json_schema_extra={"unit": "Wh"}
    )
    energy_instant_ti1_i: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1I", json_schema_extra={"unit": "A"}
    )
    energy_instant_ti1_i_max: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1I_Max", json_schema_extra={"unit": "A"}
    )
    energy_instant_ti1_i_min: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1I_Min", json_schema_extra={"unit": "A"}
    )
    energy_instant_ti1_p: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1P", json_schema_extra={"unit": "W"}
    )
    energy_instant_ti1_p_max: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1P_Max", json_schema_extra={"unit": "W"}
    )
    energy_instant_ti1_p_min: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1P_Min", json_schema_extra={"unit": "W"}
    )
    energy_instant_tot_elec: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElec", json_schema_extra={"unit": "A"}
    )
    energy_instant_tot_elec_max: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElec_Max", json_schema_extra={"unit": "A"}
    )
    energy_instant_tot_elec_min: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElec_Min", json_schema_extra={"unit": "A"}
    )
    energy_instant_tot_elec_p: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElecP", json_schema_extra={"unit": "W"}
    )
    energy_instant_tot_elec_p_max: Reading[float] | None = Field(
        default=None,
        alias="energyInstantTotElec_P_Max",
        json_schema_extra={"unit": "W"},
    )
    energy_instant_tot_elec_p_min: Reading[float] | None = Field(
        default=None,
        alias="energyInstantTotElec_P_Min",
        json_schema_extra={"unit": "W"},
    )
    energy_scale_ti1_i_max: Reading[float] | None = Field(
        default=None, alias="energyScaleTi1I_Max", json_schema_extra={"unit": "A"}
    )
    energy_scale_ti1_i_min: Reading[float] | None = Field(
        default=None, alias="energyScaleTi1I_Min", json_schema_extra={"unit": "A"}
    )
    energy_scale_ti1_p_max: Reading[float] | None = Field(
        default=None, alias="energyScaleTi1P_Max", json_schema_extra={"unit": "W"}
    )
    energy_scale_ti1_p_min: Reading[float] | None = Field(
        default=None, alias="energyScaleTi1P_Min", json_schema_extra={"unit": "W"}
    )
    energy_scale_tot_elec_max: Reading[float] | None = Field(
        default=None, alias="energyScaleTotElec_Max", json_schema_extra={"unit": "A"}
    )
    energy_scale_tot_elec_min: Reading[float] | None = Field(
        default=None, alias="energyScaleTotElec_Min", json_schema_extra={"unit": "A"}
    )
    energy_scale_tot_elec_p_max: Reading[float] | None = Field(
        default=None, alias="energyScaleTotElec_P_Max", json_schema_extra={"unit": "W"}
    )
    energy_scale_tot_elec_p_min: Reading[float] | None = Field(
        default=None, alias="energyScaleTotElec_P_Min", json_schema_extra={"unit": "W"}
    )
    energy_tot_index_watt: Reading[float] | None = Field(
        default=None, alias="energyTotIndexWatt", json_schema_extra={"unit": "Wh"}
    )
    jobs_mp: Reading[float] | None = Field(default=None, alias="jobsMP")
    soft_plan: Reading[str] | None = Field(default=None, alias="softPlan")
    soft_version: Reading[str] | None = Field(default=None, alias="softVersion")
