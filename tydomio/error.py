"""Exceptions for the TydomIo library."""


class TydomIoError(RuntimeError):
    """Base class for all TydomIo exceptions."""


class CommunicationError(TydomIoError):
    """Exception raised for communication errors with Tydom."""


class InvalidResponse(CommunicationError):
    """Exception raised for invalid responses from Tydom."""
