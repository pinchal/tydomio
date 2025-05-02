# ruff: noqa: D100, D101
"""Data models for the Delta Dore Tydom API using Pydantic."""

from pydantic import BaseModel, RootModel


class Data(BaseModel):
    name: str
    validity: str
    value: float | str | None


class Endpoint(BaseModel):
    id: int
    error: int
    data: list[Data]


class ModelItem(BaseModel):
    id: int
    endpoints: list[Endpoint]


class DevicesData(RootModel[list[ModelItem]]): ...


class AreasData(RootModel[list[Endpoint]]): ...
