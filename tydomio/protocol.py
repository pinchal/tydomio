"""Conversion of the bytes received on the websocket into messages."""

import logging
import re

from .error import InvalidRequest, InvalidResponse
from .requests import (
    JsonRequest,
    Request,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
)
from .response import Response

LOGGER = logging.getLogger(__name__)

_STATUS_LINE = re.compile(r"HTTP/1\.1 (\d{3})(?: .*)?")
_CHUNK_SIZE = re.compile(rb"[0-9a-fA-F]+")

MessageError = type[InvalidRequest] | type[InvalidResponse]


def parse_message(message: bytes, cmd_prefix: bytes) -> Response | Request:
    """Parse messages received from the websocket into Request or Response."""
    message = message.removeprefix(cmd_prefix)

    if message.startswith(b"HTTP/1.1 "):
        return parse_response(message)

    return parse_request(message)


def parse_response(message: bytes) -> Response:
    """Parse messages received from the websocket into Response."""
    status_line, headers, content = _split_message(message, InvalidResponse)
    status = _STATUS_LINE.fullmatch(status_line)
    if status is None:
        raise InvalidResponse("Invalid status line")

    return Response(
        status=int(status[1]),
        headers=headers,
        content=content,
    )


def parse_request(message: bytes) -> Request:
    """Parse messages received from the websocket into Request."""
    request_line, headers, cleaned_content = _split_message(message, InvalidRequest)
    method_url = request_line.split(" ")
    first_line_expected_size = 3
    if len(method_url) != first_line_expected_size or method_url[2] != "HTTP/1.1":
        raise InvalidRequest("Invalid request line")
    method = method_url[0]
    url = method_url[1]

    if method == "PUT":
        try:
            match url:
                case "/devices/data":
                    return TydomPutDevicesDataRequest(
                        method=method,
                        url=url,
                        headers=headers,
                        content=cleaned_content,
                    )
                case "/areas/data":
                    return TydomPutAreasDataRequest(
                        method=method,
                        url=url,
                        headers=headers,
                        content=cleaned_content,
                    )

        except InvalidRequest:
            # the error chain quotes the content: DEBUG only
            LOGGER.error("Failed to create TydomPutDataRequest")
            LOGGER.debug("Invalid data", exc_info=True)

    return JsonRequest(
        method=method,
        url=url,
        headers=headers,
        content=cleaned_content,
    )


def _split_message(
    message: bytes, error: MessageError
) -> tuple[str, dict[str, str], bytes | None]:
    """Split a message into its start line, headers and content, if any."""
    head, separator, body = message.partition(b"\r\n\r\n")
    if not separator:
        raise error("No end of headers")
    try:
        start_line, *header_lines = head.decode("ascii").split("\r\n")
    except UnicodeDecodeError as decode_error:
        raise error("Headers are not ASCII") from decode_error

    headers = {}
    for line in header_lines:
        name, colon, value = line.partition(":")
        if not colon:
            raise error("Header line without colon")
        headers[name.strip().lower()] = value.strip()

    return start_line, headers, _decode_content(body, headers, error) or None


def _decode_content(body: bytes, headers: dict[str, str], error: MessageError) -> bytes:
    """Decode the content according to its transfer encoding."""
    transfer_encoding = headers.get("transfer-encoding")
    if transfer_encoding is None:
        return body
    if transfer_encoding.lower() != "chunked":
        raise error("Unsupported transfer encoding")
    return _decode_chunked(body, error)


def _decode_chunked(body: bytes, error: MessageError) -> bytes:
    """Join the chunks of HTTP/1.1 chunked content, extensions ignored."""
    content = bytearray()
    position = 0
    while True:
        line_end = body.find(b"\r\n", position)
        size = body[position:line_end].split(b";", 1)[0].strip()
        if line_end < 0 or not _CHUNK_SIZE.fullmatch(size):
            raise error("Invalid chunk size")
        start = line_end + 2
        end = start + int(size, 16)
        if end == start:
            break
        if body[end : end + 2] != b"\r\n":
            raise error("Invalid chunk end")
        content += body[start:end]
        position = end + 2

    # no trailer section expected after the last chunk
    if body[start:] != b"\r\n":
        raise error("Invalid end of chunked content")
    return bytes(content)
