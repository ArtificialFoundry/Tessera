"""Tests for admin auth rate limiting."""

from __future__ import annotations

import pytest

from tessera.deps import (
    _AUTH_MAX_ATTEMPTS,
    _check_auth_rate_limit,
    _record_auth_failure,
    reset_auth_rate_limits,
)
from tessera.exceptions import RateLimitError


class TestAdminRateLimit:
    """Admin auth rate limiting per IP."""

    @pytest.fixture(autouse=True)
    def _clean(self) -> None:
        reset_auth_rate_limits()

    def test_allows_under_limit(self) -> None:
        for _ in range(_AUTH_MAX_ATTEMPTS - 1):
            _record_auth_failure("10.0.0.1")
        # Should not raise
        _check_auth_rate_limit("10.0.0.1")

    def test_blocks_at_limit(self) -> None:
        for _ in range(_AUTH_MAX_ATTEMPTS):
            _record_auth_failure("10.0.0.2")
        with pytest.raises(RateLimitError):
            _check_auth_rate_limit("10.0.0.2")

    def test_separate_ips(self) -> None:
        for _ in range(_AUTH_MAX_ATTEMPTS):
            _record_auth_failure("10.0.0.3")
        # Different IP should be fine
        _check_auth_rate_limit("10.0.0.4")

    def test_reset_clears_state(self) -> None:
        for _ in range(_AUTH_MAX_ATTEMPTS):
            _record_auth_failure("10.0.0.5")
        reset_auth_rate_limits()
        _check_auth_rate_limit("10.0.0.5")  # Should not raise
