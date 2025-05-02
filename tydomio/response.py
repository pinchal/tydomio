"""Requests for the Tydom API."""

import itertools
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

import pydantic

from .error import InvalidResponse
from .model import config as config_mdl

_STATUS_CODE_OK = 200
_MODULE_DIRECTORY_PATH = Path(__file__).parent.resolve()

DEV_MODE = False
TRANSACTION_COUNTER = itertools.count(random.randint(1, 100000000))


class _ResponseInitProtocol(Protocol):
    """Protocol for the response initialization."""

    def __init__(self, response: "Response") -> None:
        """Initialize the response with the given response object."""


ResponseT = TypeVar("ResponseT", bound="Response")


@dataclass(kw_only=True)
class Response(_ResponseInitProtocol):
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

        if (
            self.status not in valid_status_codes
            or self.headers.get("content-type") != "application/json"
        ):
            raise InvalidResponse(response)

        try:
            self.json_content = (
                json.loads(response.content) if response.content else None
            )
        except json.JSONDecodeError as error:
            raise InvalidResponse(response) from error

        if DEV_MODE:
            origin = self.headers.get("uri-origin", "no-origin")
            file_name = f"{origin.replace('/', '_')}.json"
            file_path = _MODULE_DIRECTORY_PATH / file_name
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(self.json_content, file, indent=4, ensure_ascii=False)


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
            raise InvalidResponse(response)


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
            raise InvalidResponse()

        try:
            self.config = config_mdl.Config.model_validate_json(response.content)

        except pydantic.ValidationError as error:
            raise InvalidResponse() from error
