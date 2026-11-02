"""Requests for the Tydom API."""

import json
import logging
from dataclasses import dataclass
from typing import Protocol

import pydantic
from pydantic_core import from_json

from .error import InvalidResponse
from .model import config as config_mdl
from .model.data import AreasData, DevicesData, Endpoint, ModelItem

LOGGER = logging.getLogger(__name__)

_STATUS_CODE_OK = 200


def has_json_content(headers: dict[str, str]) -> bool:
    """Whether the content type is JSON, whatever its case and parameters."""
    media_type = headers.get("content-type", "").split(";", 1)[0]
    return media_type.strip().lower() == "application/json"


@dataclass(kw_only=True)
class Response:
    """The Response.

    It's sent by the Tydom following a client's Request
    """

    status: int
    headers: dict[str, str]
    content: bytes | None

    @property
    def transac_id(self) -> int | None:
        """Get the transaction id."""
        if "transac-id" not in self.headers:
            return None
        return int(self.headers["transac-id"])


class ParsedResponse(Protocol):
    """A response parsed from a raw Response, identified by its transaction id."""

    def __init__(self, response: Response) -> None:
        """Initialize the response with the given response object."""

    @property
    def transac_id(self) -> int | None:
        """Get the transaction id."""


class JsonResponse(Response):
    """A standard Response used for development.

    It only parses the JSON content and save it in a file.
    """

    def __init__(
        self,
        response: Response,
        valid_status_codes: tuple[int, ...] = (_STATUS_CODE_OK,),
    ) -> None:
        """Interpret the response from the server."""
        super().__init__(
            status=response.status,
            headers=response.headers,
            content=response.content,
        )

        if self.status not in valid_status_codes:
            raise InvalidResponse(f"Unexpected response with status {self.status}")
        if not has_json_content(self.headers):
            raise InvalidResponse("Content is not JSON")

        try:
            self.json_content = (
                json.loads(response.content) if response.content is not None else None
            )
        except json.JSONDecodeError:
            # the decoding error holds the content: neither echoed nor chained
            raise InvalidResponse("Content is not JSON") from None


class Ok(Response):
    """A response with status 200, expecting no content."""

    def __init__(self, response: Response) -> None:
        """Interpret the response from the server."""
        super().__init__(
            status=response.status,
            headers=response.headers,
            content=response.content,
        )

        if self.status != _STATUS_CODE_OK or self.content is not None:
            # the content may describe the home: never in the message
            raise InvalidResponse(f"Unexpected response with status {self.status}")


class ConfigResponse(Response):
    """Response to a config request."""

    def __init__(self, response: Response) -> None:
        """Interpret the response from the server."""
        super().__init__(
            status=response.status,
            headers=response.headers,
            content=response.content,
        )

        if (
            self.status != _STATUS_CODE_OK
            or self.headers.get("uri-origin") != "/configs/file"
            or response.content is None
        ):
            raise InvalidResponse(f"Unexpected response with status {self.status}")

        try:
            self.config = config_mdl.Config.model_validate_json(response.content)

        except pydantic.ValidationError:
            # pydantic's error holds the received values: not chained
            raise InvalidResponse("Invalid /configs/file content") from None


class _Entity(pydantic.BaseModel):
    """An endpoint or an area, of which only the id is read at the envelope."""

    model_config = pydantic.ConfigDict(extra="allow")

    id: pydantic.StrictInt


class _Device(_Entity):
    endpoints: list[_Entity]


class _DataEnvelope[E: _Entity](pydantic.RootModel[list[E]]):
    """The list of entities of a data response, before each is validated."""


class _DataResponse(Response):
    """Common checks of a /devices/data or /areas/data response."""

    _URL: str

    def __init__(self, response: Response) -> None:
        """Check the status, the origin and the presence of the content."""
        super().__init__(
            status=response.status,
            headers=response.headers,
            content=response.content,
        )

        if (
            self.status != _STATUS_CODE_OK
            or self.headers.get("uri-origin") != self._URL
            or response.content is None
        ):
            raise InvalidResponse(f"Unexpected response with status {self.status}")

    def _content(self) -> bytes:
        if self.content is None:  # unreachable: refused by __init__
            raise InvalidResponse(f"Unexpected response with status {self.status}")
        return self.content

    def _read_envelope[E: _Entity](
        self, envelope: type[_DataEnvelope[E]], content: bytes
    ) -> list[E]:
        """Parse the content and validate the envelope, without chaining the error."""
        try:
            return envelope.model_validate(from_json(content)).root
        except pydantic.ValidationError, ValueError:
            # the error holds the received values: not chained
            raise InvalidResponse(f"Invalid {self._URL} content") from None

    def _log_invalid(self, count: int, noun: str, ids: object) -> None:
        if count:
            LOGGER.warning("%d invalid %s in %s", count, noun, self._URL)
            LOGGER.debug("Invalid %s in %s: %s", noun, self._URL, ids)


class DevicesDataResponse(_DataResponse):
    """Response to a /devices/data request, validated per endpoint.

    `data` holds the valid endpoints; `invalid` the (device id, endpoint id) of those
    that failed, without their value.
    """

    _URL = "/devices/data"

    def __init__(self, response: Response) -> None:
        """Interpret the response from the Tydom."""
        super().__init__(response)

        devices = self._read_envelope(_DataEnvelope[_Device], self._content())
        invalid: list[tuple[int, int]] = []
        items: list[ModelItem] = []
        for device in devices:
            endpoints: list[Endpoint] = []
            for raw in device.endpoints:
                try:
                    endpoints.append(Endpoint.model_validate(raw.model_dump()))
                except pydantic.ValidationError:
                    invalid.append((device.id, raw.id))
            items.append(ModelItem(id=device.id, endpoints=endpoints))

        self.data = DevicesData(items)
        self.invalid = tuple(invalid)
        self._log_invalid(len(invalid), "endpoints", self.invalid)


class AreasDataResponse(_DataResponse):
    """Response to an /areas/data request, validated per area.

    `data` holds the valid areas; `invalid` the ids of those that failed.
    """

    _URL = "/areas/data"

    def __init__(self, response: Response) -> None:
        """Interpret the response from the Tydom."""
        super().__init__(response)

        invalid: list[int] = []
        areas: list[Endpoint] = []
        for raw in self._read_envelope(_DataEnvelope[_Entity], self._content()):
            try:
                areas.append(Endpoint.model_validate(raw.model_dump()))
            except pydantic.ValidationError:
                invalid.append(raw.id)

        self.data = AreasData(areas)
        self.invalid = tuple(invalid)
        self._log_invalid(len(invalid), "areas", self.invalid)
