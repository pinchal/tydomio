"""Roller shutter: fields of the `shutter` usage."""

from typing import Annotated

from pydantic import Field

from .base import Reading, TydomEnum, UsageModel, UsageWrite, member_of


class PositionCmd(TydomEnum):
    """Values of `positionCmd`."""

    DOWN = "DOWN"
    DOWN_SLOW = "DOWN_SLOW"
    FAVORIT1 = "FAVORIT1"
    FAVORIT2 = "FAVORIT2"
    STOP = "STOP"
    UP = "UP"
    UP_SLOW = "UP_SLOW"


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


class ShutterWrite(UsageWrite[Shutter]):
    """Fields written to a roller shutter."""

    position: int | None = Field(default=None, alias="position", ge=0, le=100)
    position_cmd: Annotated[PositionCmd, member_of(PositionCmd)] | None = Field(
        default=None, alias="positionCmd"
    )
