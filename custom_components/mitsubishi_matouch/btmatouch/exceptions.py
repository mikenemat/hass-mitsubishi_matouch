"""Exceptions for the btmatouch library."""

__all__ = [
    "MAException",
    "MAConnectionException",
    "MARequestException",
    "MAAlreadyAwaitingResponseException",
    "MATimeoutException",
    "MAStateException",
    "MAInternalException",
    "MAResponseException",
    "MAAuthException",
    "MADeviceErrorException",
]


class MAException(Exception):
    """Base exception for the btmatouch library."""


class MAConnectionException(MAException):
    """Exception for connection errors."""


class MARequestException(MAException):
    """Exception for request errors."""


class MAAlreadyAwaitingResponseException(MAException):
    """Exception for requests that are already awaiting a response."""


class MATimeoutException(MAException):
    """Exception for timeouts."""


class MAStateException(MAException):
    """Exception for invalid states."""


class MAInternalException(MAException):
    """Exception for internal errors."""


class MAResponseException(MAException):
    """Exception for response errors."""


class MAControlRequestFailedException(MAException):
    """Exception for control request failures."""


class MAAuthException(MAException):
    """Exception for auth errors."""


class MADeviceErrorException(MAException):
    """The device answered a session/data request with an error result code it can't
    continue from — it is reachable and authenticates, but rejects operation/settings
    sessions. The canonical code is ERROR_FROM_DEVICE (0x09), but this also covers ANY
    other non-success, non-transient, non-auth result code (a generic "startup/other
    fault", not limited to one error like E4). Seen when the unit is stuck on an
    error/startup screen, or transiently while the user is in the on-device menus.
    Carries the result code and the trailing device-error detail byte (e.g. 0x78) for
    surfacing/diagnostics."""

    def __init__(self, message: str, result: int | None = None, detail: int | None = None) -> None:
        super().__init__(message)
        self.result = result
        self.detail = detail
