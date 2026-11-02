"""Requests for the Tydom API."""

import itertools
import json
import logging
import secrets
from abc import ABC
from dataclasses import dataclass, field
from typing import Final, override

import pydantic

from .error import InvalidRequest, InvalidTarget
from .installation import AreaRef, EndpointRef, GroupRef
from .model.data import AreasData, DevicesData
from .model.usages import WRITES, Area, UsageModel, UsageWrite
from .model.usages.area import AreaWrite
from .response import (
    ConfigResponse,
    Ok,
    ParsedResponse,
    Response,
    has_json_content,
)

LOGGER = logging.getLogger(__name__)

TRANSACTION_COUNTER = itertools.count(secrets.randbelow(100000000) + 1)


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
        given_headers = {name.lower() for name in self.headers}

        has_content_length = (
            "content-length" in given_headers or "transfer-encoding" in given_headers
        )

        has_transac_id = "transac-id" in given_headers

        if not has_content_length and (
            self.content is not None or self.method in ("POST", "PUT", "PATCH")
        ):
            auto_headers["content-length"] = str(len(self.content or b""))

        if not has_transac_id:
            auto_headers["transac-id"] = str(self.transaction_id)

        return {**self.headers, **auto_headers}

    def to_bytes(self, cmd_prefix: bytes) -> bytes:
        """Format this request to an array of bytes to send on the websocket."""
        return (
            cmd_prefix
            + (
                f"{self.method} {self.url} HTTP/1.1\r\n"
                + "\r\n".join(
                    (f"{key}: {value}" for key, value in self._make_headers().items())
                )
                + "\r\n\r\n"
            ).encode("ascii")
            + (self.content or b"")
        )


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

        if not has_json_content(self.headers):
            raise InvalidRequest("Invalid content type")

        try:
            self.json_content = (
                json.loads(self.content) if self.content is not None else None
            )
        except json.JSONDecodeError:
            # the decoding error holds the content: neither echoed nor chained
            raise InvalidRequest("Content is not JSON") from None


class TydomRequest[DataModelT: pydantic.BaseModel](Request):
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
            self.data: Final[DataModelT] = data_model_type.model_validate_json(
                self.content
            )
        except pydantic.ValidationError:
            # pydantic's error holds the received values: not chained
            raise InvalidRequest(f"Invalid {data_model_type.__name__}") from None


class TydomPutDevicesDataRequest(TydomRequest[DevicesData]):
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


class TydomPutAreasDataRequest(TydomRequest[AreasData]):
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


class ClientRequest[ResponseT: ParsedResponse](Request, ABC):
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

    def log_sending(self) -> None:
        """Log the request, called by the client just before it goes on the wire.

        It logs nothing by default.
        """


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


class _Write(ClientRequest[Ok]):
    """A PUT of the fields of a write model, acknowledged by an empty 200."""

    response_type = Ok

    def __init__(
        self, url: str, usage: type[UsageModel], write: UsageWrite[UsageModel]
    ) -> None:
        """Check the write fits the usage, revalidate it, serialize it to JSON."""
        if WRITES.get(usage) is not type(write):
            raise InvalidTarget(
                f"{type(write).__name__} does not write to a {usage.__name__}"
            )
        # revalidated: model_construct and model_copy skip the bounds
        body = type(write).model_validate(dict(write)).body()
        super().__init__(
            method="PUT",
            url=url,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            content=json.dumps(body, allow_nan=False).encode(),
        )
        self._usage_name = usage.__name__
        self._body = body

    @override
    def log_sending(self) -> None:
        """Log the usage and field names at INFO, the values at DEBUG only."""
        LOGGER.info(
            "Write to a %s: %s",
            self._usage_name,
            ", ".join(str(item["name"]) for item in self._body),
        )
        LOGGER.debug("Write values %s", self._body)


class WriteEndpoint[U: UsageModel](_Write):
    """ClientRequest writing fields of an endpoint."""

    def __init__(self, ref: EndpointRef[U], write: UsageWrite[U]) -> None:
        """Write to the endpoint of the reference."""
        super().__init__(
            f"/devices/{ref.device_id}/endpoints/{ref.endpoint_id}/data",
            ref.usage,
            write,
        )


class WriteGroup[U: UsageModel](_Write):
    """ClientRequest writing fields of every device of a group."""

    def __init__(self, ref: GroupRef[U], write: UsageWrite[U]) -> None:
        """Write to the group of the reference."""
        super().__init__(f"/groups/{ref.group_id}/data", ref.usage, write)


class WriteArea(_Write):
    """ClientRequest writing fields of an area."""

    def __init__(self, ref: AreaRef, write: AreaWrite) -> None:
        """Write to the area of the reference."""
        super().__init__(f"/areas/{ref.area_id}/data", Area, write)
