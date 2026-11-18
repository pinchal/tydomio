"""Exceptions for the TydomIo library."""

from collections.abc import Iterator


class TydomIoError(RuntimeError):
    """Base class for all TydomIo exceptions."""


class CommunicationError(TydomIoError):
    """Exception raised for communication errors with Tydom."""


class InvalidResponse(CommunicationError):
    """Exception raised for invalid responses from Tydom."""


class InvalidRequest(CommunicationError):
    """Exception raised for invalid requests from Tydom."""


class AuthorizationError(CommunicationError):
    """Exception raised when the authorization cannot be obtained from Tydom."""


class InvalidTarget(TydomIoError):
    """Exception raised for a target unknown or not of the usage expected."""


class InvalidWrite(TydomIoError):
    """Exception raised for a write refused before it is sent."""


def leaf_type_names(group: BaseExceptionGroup) -> Iterator[str]:
    """Give the type name of each exception of a group, nested groups flattened."""
    for error in group.exceptions:
        if isinstance(error, BaseExceptionGroup):
            yield from leaf_type_names(error)
        else:
            yield type(error).__name__
