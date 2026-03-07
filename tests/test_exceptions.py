"""Exception hierarchy and error code tests."""

from __future__ import annotations

from tessera.engines.backup import BackupError
from tessera.exceptions import (
    AppError,
    AuthenticationError,
    ErrorCode,
    ErrorResponse,
    NotFoundError,
    RateLimitError,
    TechnitiumError,
)


class TestExceptionErrorCodes:
    """Each exception subclass carries the correct error code."""

    def test_base_error_defaults_to_internal(self) -> None:
        assert AppError("boom").code == ErrorCode.INTERNAL_ERROR

    def test_not_found_error_code(self) -> None:
        assert NotFoundError("Scope", "t").code == ErrorCode.NOT_FOUND

    def test_authentication_error_code(self) -> None:
        assert AuthenticationError().code == ErrorCode.AUTHENTICATION_FAILED

    def test_rate_limit_error_code(self) -> None:
        assert RateLimitError("v1", 30.0).code == ErrorCode.RATE_LIMITED

    def test_technitium_error_code(self) -> None:
        assert TechnitiumError("f").code == ErrorCode.TECHNITIUM_ERROR

    def test_backup_error_code(self) -> None:
        assert BackupError("f").code == ErrorCode.BACKUP_ERROR


class TestErrorResponseModel:
    """ErrorResponse Pydantic model serialization."""

    def test_model_roundtrip_preserves_fields(self) -> None:
        resp = ErrorResponse(
            error={
                "code": "NOT_FOUND",
                "message": "gone",
                "detail": None,
            }
        )
        assert resp.error.code == "NOT_FOUND"
