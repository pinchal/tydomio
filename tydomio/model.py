# ruff: noqa: D100, D101
"""Data models for the Delta Dore Tydom API using Pydantic."""

from pydantic import BaseModel


class WidgetBehavior(BaseModel):
    tutorial_id: str


class Endpoint(BaseModel):
    id_endpoint: int
    first_usage: str
    skill: str
    id_device: int
    name: str
    anticipation_start: bool
    space_id: str | None = None
    picto: str
    last_usage: str
    widget_behavior: WidgetBehavior


class Moment(BaseModel):
    rule_id: str
    color: int
    name: str
    id: int


class Group(BaseModel):
    group_all: bool
    usage: str
    name: str
    id: int
    is_group_user: bool
    picto: str


class Area(BaseModel):
    first_usage: str
    name: str
    id: int
    anticipation_start: bool
    picto: str
    last_usage: str


class Scenario(BaseModel):
    rule_id: str
    name: str
    id: int
    type: str
    picto: str


class ZigbeeNetwork(BaseModel):
    is_connected: bool
    extended_pan_id: str
    name: str
    type: str


class Config(BaseModel):
    date: int
    version_application: str
    endpoints: list[Endpoint]
    old_tycam: bool
    moments: list[Moment]
    os: str
    groups: list[Group]
    areas: list[Area]
    scenarios: list[Scenario]
    id_catalog: str
    version: str
    zigbee_networks: list[ZigbeeNetwork]


def parse_config(json_data: str) -> Config:
    """Parse a JSON string and return a Config object.

    Args:
        json_data (str): A JSON-formatted string representing the configuration.

    Returns:
        Config: An instance of the Config class.

    Raises:
        ValidationError: If the JSON data is invalid or does not conform to the schema.

    """
    return Config.model_validate_json(json_data)


def serialize_config(config: Config) -> str:
    """Serialize a Config object into a JSON string.

    Args:
        config (Config): The configuration object to serialize.

    Returns:
        str: A JSON string representation of the configuration object.

    """
    return config.model_dump_json()
