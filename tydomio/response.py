"""Requests for the Tydom API."""

import json
from dataclasses import dataclass
from typing import Protocol

import pydantic

from .error import InvalidResponse
from .model import config as config_mdl

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
