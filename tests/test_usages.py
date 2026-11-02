import logging

import pytest
from pydantic import Field

from tydomio.error import InvalidResponse
from tydomio.model.data import Data
from tydomio.model.usages import (
    USAGES,
    FieldMeta,
    Reading,
    Shutter,
    TydomEnum,
    UsageModel,
    parse_usage,
)


class Mode(TydomEnum):
    """Invented enum."""

    ON = "ON"


class Lamp(UsageModel):
    """Invented usage."""

    level: Reading[float] | None = Field(default=None, alias="levelPct")
    mode: Reading[Mode | str] | None = Field(default=None, alias="mode")


def test_from_data_keeps_value_and_validity() -> None:
    lamp = Lamp.from_data([Data(name="levelPct", validity="expired", value=40)])

    assert lamp.level == Reading[float](value=40.0, validity="expired")


def test_known_enum_value_is_a_member() -> None:
    lamp = Lamp.from_data([Data(name="mode", validity="upToDate", value="ON")])

    assert lamp.mode is not None
    assert lamp.mode.value is Mode.ON


def test_invalid_value_raises_without_the_value() -> None:
    with pytest.raises(InvalidResponse) as raised:
        Lamp.from_data([Data(name="levelPct", validity="upToDate", value="bright")])

    assert str(raised.value) == "Lamp: invalid fields levelPct"


def test_unknown_field_is_kept_and_logged_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    datum = Data(name="colour", validity="upToDate", value="red")

    with caplog.at_level(logging.DEBUG):
        lamp = Lamp.from_data([datum])

    assert lamp.unknown == {"colour": datum}
    assert [(r.levelno, "colour" in r.getMessage()) for r in caplog.records] == [
        (logging.DEBUG, True)
    ]


def test_unknown_enum_value_is_kept_as_str_and_logged_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lamp = Lamp.from_data([Data(name="mode", validity="upToDate", value="BLINK")])

    assert lamp.mode is not None
    assert type(lamp.mode.value) is str
    assert lamp.mode.value == "BLINK"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "BLINK" not in warnings[0].getMessage()


def test_unknown_usage_has_no_model() -> None:
    assert parse_usage("noSuchUsage", []) is None


@pytest.mark.parametrize(("usage", "model"), USAGES.items())
def test_known_usage_parses_into_its_model(usage: str, model: type[UsageModel]) -> None:
    assert type(parse_usage(usage, [])) is model


def test_field_meta_holds_the_shutter_position() -> None:
    assert Shutter.field_meta()["position"] == FieldMeta(
        unit="%", description="0 = closed, 100 = open"
    )
