"""Custom exception hierarchy for Tessera.

All application exceptions inherit from ``AppError``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ErrorCode(StrEnum):
    """Machine-readable error codes for API responses."""

    INTERNAL_ERROR = "INTERNAL_ERROR"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"
    TECHNITIUM_ERROR = "TECHNITIUM_ERROR"
    BACKUP_ERROR = "BACKUP_ERROR"
    ENFORCEMENT_ERROR = "ENFORCEMENT_ERROR"
    REGISTRATION_ERROR = "REGISTRATION_ERROR"
    SCOPE_SYNC_ERROR = "SCOPE_SYNC_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"


class _ErrorBody(BaseModel):
    """Inner error object."""

    code: str
    message: str
    detail: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    """Structured error response returned by all API error handlers."""

    error: _ErrorBody


class AppError(Exception):
    """Base for all Tessera application errors."""

    def __init__(
        self,
        message: str = "",
        *,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
    ) -> None:
        self.code = code
        super().__init__(message)


class RegistryError(AppError):
    """An operation on a registry failed."""


class DuplicateEntryError(RegistryError):
    """Attempted to register an entry that already exists."""

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(
            f"{kind} already registered: {name}",
            code=ErrorCode.VALIDATION_ERROR,
        )


class NotFoundError(RegistryError):
    """Requested registry entry does not exist."""

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(f"{kind} not found: {name}", code=ErrorCode.NOT_FOUND)


class EngineStartupError(AppError):
    """An engine failed during startup."""

    def __init__(self, engine_name: str, cause: Exception) -> None:
        self.engine_name = engine_name
        self.__cause__ = cause
        super().__init__(
            f"Engine '{engine_name}' failed to start: {cause}",
            code=ErrorCode.INTERNAL_ERROR,
        )


class DependencyError(AppError):
    """An engine dependency is missing or unsatisfied."""

    def __init__(self, engine_name: str, dependency: str) -> None:
        self.engine_name = engine_name
        self.dependency = dependency
        super().__init__(
            f"Engine '{engine_name}' depends on unregistered engine '{dependency}'",
            code=ErrorCode.INTERNAL_ERROR,
        )


class AuthenticationError(AppError):
    """HMAC or voter authentication failed."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message, code=ErrorCode.AUTHENTICATION_FAILED)


class TechnitiumError(AppError):
    """Communication with Technitium API failed."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.status_code = status_code
        super().__init__(message, code=ErrorCode.TECHNITIUM_ERROR)


class ScopeSyncError(AppError):
    """An error occurred during DHCP scope synchronisation."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message, code=ErrorCode.SCOPE_SYNC_ERROR)


class RateLimitError(AppError):
    """A voter exceeded the per-voter rate limit."""

    def __init__(self, voter: str, retry_after: float) -> None:
        self.voter = voter
        self.retry_after = retry_after
        msg = f"Voter '{voter}' rate-limited; retry after {retry_after:.0f}s"
        super().__init__(msg, code=ErrorCode.RATE_LIMITED)


class RegistrationError(AppError):
    """A voter registration operation failed."""

    def __init__(self, message: str = "Registration failed") -> None:
        super().__init__(message, code=ErrorCode.REGISTRATION_ERROR)


class ServiceUnavailableError(AppError):
    """A required service or configuration is not available."""

    def __init__(self, message: str = "Service unavailable") -> None:
        super().__init__(message, code=ErrorCode.INTERNAL_ERROR)
