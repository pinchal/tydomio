from collections.abc import Callable

import pytest

from tydomio.error import InvalidTarget
from tydomio.installation import AreaRef, EndpointRef, Installation
from tydomio.model.config import Config
from tydomio.model.data import AreasData, Data, DevicesData, Endpoint, ModelItem
from tydomio.model.usages import Area
from tydomio.model.usages.boiler import Boiler
from tydomio.model.usages.shutter import Shutter

# endpoint id 1 on two devices, of two usages
INSTALLATION = Installation({(10, 1): "shutter", (20, 1): "boiler"}, [7])
POSITION = Data(name="position", validity="upToDate", value=40)


def untyped(factory: Callable[..., object]) -> Callable[..., object]:
    """Call the factory as a caller without types would, e.g. from a tool call."""
    return factory


def devices_data(device_id: int, endpoint_id: int) -> DevicesData:
    return DevicesData(
        [
            ModelItem(
                id=device_id,
                endpoints=[Endpoint(id=endpoint_id, error=0, data=[POSITION])],
            )
        ]
    )


def test_from_config_indexes_endpoints_by_device_and_endpoint() -> None:
    config = Config.model_validate(
        {
            "date": 1,
            "version_application": "1.0",
            "endpoints": [
                {
                    "id_endpoint": 1,
                    "first_usage": "shutter",
                    "skill": "skill",
                    "id_device": 10,
                    "name": "name",
                    "anticipation_start": False,
                    "picto": "picto",
                    "last_usage": "shutter",
                    "widget_behavior": {"tutorial_id": "tutorial"},
                }
            ],
            "old_tycam": False,
            "moments": [],
            "os": "tydom",
            "groups": [],
            "areas": [],
            "scenarios": [],
            "id_catalog": "catalog",
            "version": "2.0",
            "zigbee_networks": [],
        }
    )

    ref = Installation.from_config(config).endpoint(10, 1, Shutter)

    assert ref == EndpointRef(10, 1, Shutter)


def test_endpoint_id_shared_by_two_devices_resolves_per_device() -> None:
    ref = INSTALLATION.endpoint(20, 1, Boiler)

    assert (ref.device_id, ref.usage) == (20, Boiler)


def test_unknown_endpoint_is_refused_without_its_ids() -> None:
    with pytest.raises(InvalidTarget) as raised:
        INSTALLATION.endpoint(30, 9, Shutter)

    assert "30" not in str(raised.value)


def test_endpoint_of_another_usage_is_refused() -> None:
    with pytest.raises(InvalidTarget) as raised:
        INSTALLATION.endpoint(10, 1, Boiler)

    assert str(raised.value) == "The endpoint is a shutter, not a Boiler"


def test_known_area_is_referenced() -> None:
    assert INSTALLATION.area(7) == AreaRef(7)


def test_unknown_area_is_refused() -> None:
    with pytest.raises(InvalidTarget):
        INSTALLATION.area(8)


@pytest.mark.parametrize("device_id", ["10/../x", 10.0, True])
def test_reference_refuses_an_id_that_is_not_an_int(device_id: object) -> None:
    with pytest.raises(InvalidTarget):
        untyped(EndpointRef)(device_id, 1, Shutter)


def test_read_endpoint_types_the_data_by_its_usage() -> None:
    shutter = INSTALLATION.read_endpoint(devices_data(10, 1), 10, 1)

    assert isinstance(shutter, Shutter)
    assert shutter.position is not None
    assert shutter.position.value == 40


def test_read_endpoint_is_none_when_the_push_lacks_it() -> None:
    assert INSTALLATION.read_endpoint(devices_data(20, 1), 10, 1) is None


def test_read_endpoint_refuses_an_unknown_endpoint() -> None:
    with pytest.raises(InvalidTarget):
        INSTALLATION.read_endpoint(devices_data(30, 1), 30, 1)


def test_read_area_types_the_data() -> None:
    data = AreasData([Endpoint(id=7, error=0, data=[])])

    assert isinstance(INSTALLATION.read_area(data, 7), Area)


def test_read_area_is_none_when_the_push_lacks_it() -> None:
    assert INSTALLATION.read_area(AreasData([]), 7) is None
