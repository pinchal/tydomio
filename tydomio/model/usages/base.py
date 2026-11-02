"""Shared pieces of the usage models: a reading, a tolerant enum, the base models."""

import logging
from collections.abc import Iterable
from enum import StrEnum
from typing import Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    ModelWrapValidatorHandler,
    PlainValidator,
    ValidationError,
    model_validator,
)
from pydantic_core import CoreSchema

from tydomio.error import InvalidResponse, InvalidWrite
from tydomio.model.data import Data

logger = logging.getLogger(__name__)


class Reading[T](BaseModel):
    """One field as read from the Tydom: its value and how fresh it is."""

    value: T | None
    validity: str


class TydomEnum(StrEnum):
    """Enum of the values a field is known to take; others are kept as `str`.

    Annotate a field `Reading[MyEnum | str]`: a value outside the enum is logged at
    WARNING and kept as a plain `str`, so a new firmware value rejects no message.
    """

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Validate a string into a member, or keep it as `str` when unknown."""
        validator = AfterValidator(cls._member_or_str)
        return validator.__get_pydantic_core_schema__(str, handler)

    @classmethod
    def _member_or_str(cls, value: str) -> Self | str:
        try:
            return cls(value)
        except ValueError:
            # the value is message content: DEBUG only
            logger.warning("Value outside the known values of %s", cls.__name__)
            logger.debug("Unknown %s value: %r", cls.__name__, value)
            return value


class UsageModel(BaseModel):
    """Typed fields of one usage; fields absent from a message stay `None`.

    Each field is `Reading[...] | None` with `alias` set to its Tydom name.
    """

    unknown: dict[str, Data] = Field(default_factory=dict)
    """Fields the model does not know, kept as received."""

    @classmethod
    def from_data(cls, data: Iterable[Data]) -> Self:
        """Build the model from the generic data of one endpoint or area.

        Raise InvalidResponse, naming the fields, when a value has the wrong type.
        """
        aliases = {field.alias for field in cls.model_fields.values()}
        known: dict[str, dict[str, object]] = {}
        unknown: dict[str, Data] = {}
        for item in data:
            if item.name in aliases:
                known[item.name] = {"value": item.value, "validity": item.validity}
            else:
                logger.debug("Unknown field %s in %s", item.name, cls.__name__)
                unknown[item.name] = item
        try:
            return cls.model_validate({**known, "unknown": unknown})
        except ValidationError as error:
            # pydantic's message holds the received values: name the fields only
            fields = sorted({str(detail["loc"][0]) for detail in error.errors()})
            raise InvalidResponse(
                f"{cls.__name__}: invalid fields {', '.join(fields)}"
            ) from None


def member_of[E: TydomEnum](enum: type[E]) -> PlainValidator:
    """Validate a write field: a value outside the enum is refused, not kept."""

    def member(value: object) -> E:
        return enum(value)

    return PlainValidator(member)


class UsageWrite[U: UsageModel](BaseModel):
    """Fields to write to an endpoint of usage U, or an area; unset fields stay as is.

    Each field is `X | None` with `alias` set to its Tydom name, the bounds of the
    catalog, and `Annotated[MyEnum, member_of(MyEnum)]` for an enum. Built by field
    name; at least one field is required.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_by_name=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def _refuse_without_values(
        cls, data: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        """Raise InvalidWrite naming the fields, never the values given."""
        try:
            write = handler(data)
        except ValidationError as error:
            raise InvalidWrite(_describe(cls, error)) from None
        if not write.body():
            raise InvalidWrite(f"{cls.__name__}: nothing to write")
        return write

    def body(self) -> list[dict[str, object]]:
        """Give the Tydom body: the name and value of each field set."""
        return [
            {"name": info.alias, "value": value}
            for name, info in type(self).model_fields.items()
            if (value := getattr(self, name)) is not None
        ]


def _describe(model: type[BaseModel], error: ValidationError) -> str:
    """Name the invalid known fields; a name the model does not have is not echoed."""
    known = set(model.model_fields) | {
        info.alias for info in model.model_fields.values() if info.alias is not None
    }
    locations = [str(detail["loc"][0]) for detail in error.errors() if detail["loc"]]
    fields = sorted({location for location in locations if location in known})
    unknown = any(location not in known for location in locations)
    parts = [f"invalid fields {', '.join(fields)}"] if fields else []
    if unknown or not locations:
        parts.append("unknown fields or invalid input")
    return f"{model.__name__}: {'; '.join(parts)}"
