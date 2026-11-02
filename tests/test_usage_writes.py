"""Write models: bounds, strict enums, and errors without values.

Heating write models are only built here, never sent.
"""

import math

import pytest

from tydomio.error import InvalidWrite
from tydomio.model.usages.boiler import BoilerWrite
from tydomio.model.usages.shutter import PositionCmd, ShutterWrite
from tydomio.model.usages.thermic import LocalMode


@pytest.mark.parametrize("position", [0, 100])
def test_bounds_are_accepted(position: int) -> None:
    assert ShutterWrite(position=position).position == position


@pytest.mark.parametrize("position", [-1, 101])
def test_out_of_range_is_refused(position: int) -> None:
    with pytest.raises(InvalidWrite):
        ShutterWrite(position=position)


def test_value_off_the_step_is_refused() -> None:
    with pytest.raises(InvalidWrite):
        BoilerWrite(setpoint=20.3)


@pytest.mark.parametrize("setpoint", [math.nan, math.inf, -math.inf])
def test_nan_and_infinity_are_refused(setpoint: float) -> None:
    with pytest.raises(InvalidWrite):
        BoilerWrite(setpoint=setpoint)


@pytest.mark.parametrize("position", [True, "50", 50.0])
def test_value_of_another_type_is_not_converted(position: object) -> None:
    with pytest.raises(InvalidWrite):
        ShutterWrite.model_validate({"position": position})


def test_known_enum_value_becomes_a_member() -> None:
    assert BoilerWrite.model_validate({"local_mode": "NORMAL"}).local_mode is (
        LocalMode.NORMAL
    )


def test_unknown_enum_value_is_refused() -> None:
    with pytest.raises(InvalidWrite):
        ShutterWrite.model_validate({"position_cmd": "JUMP"})


def test_empty_write_is_refused() -> None:
    with pytest.raises(InvalidWrite):
        ShutterWrite()


def test_write_of_none_only_is_refused() -> None:
    with pytest.raises(InvalidWrite):
        ShutterWrite(position=None)


def test_field_left_out_of_the_model_is_refused() -> None:
    with pytest.raises(InvalidWrite):
        BoilerWrite.model_validate({"setpoint": 20, "ambientTemperature": 30})


def test_error_names_the_field_without_its_value() -> None:
    with pytest.raises(InvalidWrite) as raised:
        BoilerWrite(setpoint=987.5)

    assert str(raised.value) == "BoilerWrite: invalid fields setpoint"


def test_error_does_not_echo_an_unknown_field_name() -> None:
    with pytest.raises(InvalidWrite) as raised:
        ShutterWrite.model_validate({"position": 1, "injected_name": 1})

    assert "injected_name" not in str(raised.value)


def test_body_holds_the_fields_set_by_their_tydom_name() -> None:
    write = ShutterWrite(position_cmd=PositionCmd.UP)

    assert write.body() == [{"name": "positionCmd", "value": PositionCmd.UP}]
