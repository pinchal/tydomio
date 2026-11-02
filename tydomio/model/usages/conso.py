"""Energy meter: fields of the `conso` usage."""

from pydantic import Field

from .base import Reading, UsageModel


class Conso(UsageModel):
    """Energy meter."""

    energy_index_ti1: Reading[float] | None = Field(
        default=None, alias="energyIndexTi1"
    )
    energy_instant_ti1_i: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1I"
    )
    energy_instant_ti1_i_max: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1I_Max"
    )
    energy_instant_ti1_i_min: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1I_Min"
    )
    energy_instant_ti1_p: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1P"
    )
    energy_instant_ti1_p_max: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1P_Max"
    )
    energy_instant_ti1_p_min: Reading[float] | None = Field(
        default=None, alias="energyInstantTi1P_Min"
    )
    energy_instant_tot_elec: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElec"
    )
    energy_instant_tot_elec_max: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElec_Max"
    )
    energy_instant_tot_elec_min: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElec_Min"
    )
    energy_instant_tot_elec_p: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElecP"
    )
    energy_instant_tot_elec_p_max: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElec_P_Max"
    )
    energy_instant_tot_elec_p_min: Reading[float] | None = Field(
        default=None, alias="energyInstantTotElec_P_Min"
    )
    energy_scale_ti1_i_max: Reading[float] | None = Field(
        default=None, alias="energyScaleTi1I_Max"
    )
    energy_scale_ti1_i_min: Reading[float] | None = Field(
        default=None, alias="energyScaleTi1I_Min"
    )
    energy_scale_ti1_p_max: Reading[float] | None = Field(
        default=None, alias="energyScaleTi1P_Max"
    )
    energy_scale_ti1_p_min: Reading[float] | None = Field(
        default=None, alias="energyScaleTi1P_Min"
    )
    energy_scale_tot_elec_max: Reading[float] | None = Field(
        default=None, alias="energyScaleTotElec_Max"
    )
    energy_scale_tot_elec_min: Reading[float] | None = Field(
        default=None, alias="energyScaleTotElec_Min"
    )
    energy_scale_tot_elec_p_max: Reading[float] | None = Field(
        default=None, alias="energyScaleTotElec_P_Max"
    )
    energy_scale_tot_elec_p_min: Reading[float] | None = Field(
        default=None, alias="energyScaleTotElec_P_Min"
    )
    energy_tot_index_watt: Reading[float] | None = Field(
        default=None, alias="energyTotIndexWatt"
    )
    jobs_mp: Reading[float] | None = Field(default=None, alias="jobsMP")
    soft_plan: Reading[str] | None = Field(default=None, alias="softPlan")
    soft_version: Reading[str] | None = Field(default=None, alias="softVersion")
