"""Requests for the Tydom API."""

from .client import Request, Response
from .error import InvalidResponse

_STATUS_CODE_OK = 200


class PongResponse(Response):
    """Response to a ping request."""

    def __init__(self, response: Response) -> None:
        """Interpret the response from the server."""
        super().__init__(
            status=response.status, headers=response.headers, content=response.content
        )

        if self.status != _STATUS_CODE_OK or self.headers.get("Uri-Origin") != "/ping":
            raise InvalidResponse()


class PingRequest(Request[PongResponse]):
    """Ping request to check if the server is reachable."""

    response_type = PongResponse

    def __init__(self) -> None:
        """Initialize the ping request."""
        super().__init__(method="GET", url="/ping")

    # @classmethod
    # def parse_response(
    #    cls: type[Request[PongResponse]], response: Response
    # ) -> PongResponse:
    #    """Parse the response from the server."""
    #    return cls.response_type(response)
