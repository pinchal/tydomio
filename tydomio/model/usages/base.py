"""Shared pieces of the usage models: a reading, a tolerant enum, the base model."""

import logging
from collections.abc import Iterable
from enum import StrEnum
from typing import Self

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    GetCoreSchemaHandler,
    ValidationError,
)
from pydantic_core import CoreSchema

from tydomio.error import InvalidResponse
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
