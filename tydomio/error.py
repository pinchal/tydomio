"""Exceptions for the TydomIo library."""


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
