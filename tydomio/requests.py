"""Requests for the Tydom API."""

import itertools
import json
import random
from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, Protocol, TypeVar

import pydantic

from .error import InvalidRequest
from .globals import DEV_MODE
from .model.data import AreasData, DevicesData
from .response import ConfigResponse, Ok, Response

_MODULE_DIRECTORY_PATH = Path(__file__).parent.resolve()

TRANSACTION_COUNTER = itertools.count(random.randint(1, 100000000))


class _ResponseInitProtocol(Protocol):
    """Protocol for the response initialization."""

    def __init__(self, response: Response) -> None:
        """Initialize the response with the given response object."""


ResponseT = TypeVar("ResponseT", bound=_ResponseInitProtocol)
DataModelT = TypeVar("DataModelT", bound=pydantic.BaseModel)


@dataclass
class Request:
    """The Request.

    - sent by the client to the Tydom
    - but also from the Tydom to the client 🤷
    """

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    transaction_id: int | None = field(
        default_factory=lambda: next(TRANSACTION_COUNTER)
    )

    def _make_headers(self) -> dict[str, str]:
        auto_headers: dict[str, str] = {}

        has_content_length = (
            "Content-Length" in self.headers or "Transfer-Encoding" in self.headers
        )

        has_transac_id = "Transac-Id" in self.headers

        if not has_content_length and self.method in ("POST", "PUT", "PATCH"):
            auto_headers["content-length"] = "0"

        if not has_transac_id:
            auto_headers["transac-id"] = str(self.transaction_id)

        return {**self.headers, **auto_headers}

    def to_bytes(self, cmd_prefix: bytes) -> bytes:
        """Format this request to an array of bytes to send on the websocket."""
        return cmd_prefix + (
            f"{self.method} {self.url} HTTP/1.1\r\n"
            + "\r\n".join(
                (f"{key}: {value}" for key, value in self._make_headers().items())
            )
            + "\r\n\r\n"
        ).encode("ascii")


class JsonRequest(Request):
    """A standard Request used for development.

    It only parses the JSON content and save it in a file.
    """

    def __init__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        content: bytes | None = None,
    ) -> None:
        """Interpret the request from the server."""
        super().__init__(
            method=method,
            url=url,
            headers=headers,
            content=content,
            transaction_id=None,
        )

        if self.headers.get("content-type") != "application/json":
            raise InvalidRequest("Invalid content type")

        try:
            self.json_content = json.loads(self.content) if self.content else None
        except json.JSONDecodeError as error:
            raise InvalidRequest(self.content) from error

        if DEV_MODE:
            file_name = f"request_{self.method}_{self.url.replace('/', '_')}.json"
            file_path = _MODULE_DIRECTORY_PATH / file_name
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(self.json_content, file, indent=4, ensure_ascii=False)


class TydomRequest(Request):
    """Request made by the Tydom with a json payload to parse."""

    def __init__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        content: bytes | None,
        data_model_type: type[DataModelT],
    ) -> None:
        """Interpret the request from the server."""
        super().__init__(
            method=method,
            url=url,
            headers=headers,
            content=content,
            transaction_id=None,
        )

        if self.content is None:
            raise InvalidRequest("No content in the request")

        try:
            self.data: DataModelT = data_model_type.model_validate_json(self.content)
        except pydantic.ValidationError as error:
            raise InvalidRequest() from error


class TydomPutDevicesDataRequest(TydomRequest):
    """Request made by the Tydom to push devices values."""

    def __init__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        content: bytes | None = None,
    ) -> None:
        """Interpret the request from the server."""
        super().__init__(
            method=method,
            url=url,
            headers=headers,
            content=content,
            data_model_type=DevicesData,
        )


class TydomPutAreasDataRequest(TydomRequest):
    """Request made by the Tydom to push areas data."""

    def __init__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        content: bytes | None = None,
    ) -> None:
        """Interpret the request from the server."""
        super().__init__(
            method=method,
            url=url,
            headers=headers,
            content=content,
            data_model_type=AreasData,
        )


class ClientRequest(Request, Generic[ResponseT], ABC):
    """The ClientRequest.

    - This one can only be sent by the client to the Tydom
    - the type of response must be specified
    """

    response_type: type[ResponseT]

    def parse_response(
        self,
        response: Response,
    ) -> ResponseT:
        """Parse the given response and convert it to the expected response type.

        Args:
            response (Response): The response object to be parsed.

        Returns:
            ResponseT: The parsed response, cast to the expected response type.

        """
        return self.response_type(response)


class PingRequest(ClientRequest[Ok]):
    """Ping request to check if the server is reachable."""

    response_type = Ok

    def __init__(self) -> None:
        """Initialize the ping request."""
        super().__init__(method="GET", url="/ping")


class GetConfigFile(ClientRequest[ConfigResponse]):
    """ClientRequest to get /configs/file."""

    response_type = ConfigResponse

    def __init__(self) -> None:
        """Initialize the get config request."""
        super().__init__(
            method="GET",
            url="/configs/file",
            headers={
                "Content-Type": "application/json; charset=UTF-8",
            },
        )


class RefreshAll(ClientRequest[Ok]):
    """ClientRequest to refresh all devices."""

    response_type = Ok

    def __init__(self) -> None:
        """Initialize the refresh all request."""
        super().__init__(
            method="POST",
            url="/refresh/all",
        )
