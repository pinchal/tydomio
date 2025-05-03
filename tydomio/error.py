"""Exceptions for the TydomIo library."""


class TydomIoError(RuntimeError):
    """Base class for all TydomIo exceptions."""


class CommunicationError(TydomIoError):
    """Exception raised for communication errors with Tydom."""


class InvalidResponse(TydomIoError):
    """Exception raised for invalid responses from Tydom."""


class InvalidRequest(TydomIoError):
    """Exception raised for invalid requests from Tydom."""
