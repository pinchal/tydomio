"""Roller shutter: fields of the `shutter` usage."""

from pydantic import Field

from .base import Reading, UsageModel


class Shutter(UsageModel):
    """Roller shutter."""

    batt_defect: Reading[bool] | None = Field(default=None, alias="battDefect")
    down_defect: Reading[bool] | None = Field(default=None, alias="downDefect")
    intrusion: Reading[bool] | None = Field(default=None, alias="intrusion")
    obstacle_defect: Reading[bool] | None = Field(default=None, alias="obstacleDefect")
    on_fav_pos: Reading[bool] | None = Field(default=None, alias="onFavPos")
    position: Reading[float] | None = Field(default=None, alias="position")
    thermic_defect: Reading[bool] | None = Field(default=None, alias="thermicDefect")
    up_defect: Reading[bool] | None = Field(default=None, alias="upDefect")
