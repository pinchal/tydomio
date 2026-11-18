"""Roller shutter: fields of the `shutter` usage."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from tydomio.error import InvalidWrite

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
    position: Reading[float] | None = Field(
        default=None,
        alias="position",
        description="0 = closed, 100 = open",
        json_schema_extra={"unit": "%"},
    )
    thermic_defect: Reading[bool] | None = Field(default=None, alias="thermicDefect")
    up_defect: Reading[bool] | None = Field(default=None, alias="upDefect")


class ShutterWrite(UsageWrite[Shutter]):
    """Fields written to a roller shutter."""

    position: int | None = Field(default=None, alias="position", ge=0, le=100)
    position_cmd: Annotated[PositionCmd, member_of(PositionCmd)] | None = Field(
        default=None, alias="positionCmd"
    )

    @model_validator(mode="after")
    def _one_of_position_or_command(self) -> Self:
        """Refuse a position and a command together: the shutter cannot do both."""
        if self.position is not None and self.position_cmd is not None:
            raise InvalidWrite(
                "ShutterWrite: set exactly one of position or positionCmd"
            )
        return self
