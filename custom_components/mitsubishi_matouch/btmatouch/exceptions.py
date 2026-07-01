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
    """The device answered a session/data request with ERROR_FROM_DEVICE (result 0x09):
    it is reachable and authenticates, but rejects operation/settings sessions. Seen when
    the unit is stuck on an error/startup screen (a fault that blocks normal operation) or,
    transiently, while the user is in the thermostat's on-device menus. Carries the raw
    response so the trailing device-error detail byte (e.g. 0x78) can be surfaced."""

    def __init__(self, message: str, detail: int | None = None) -> None:
        super().__init__(message)
        self.detail = detail
