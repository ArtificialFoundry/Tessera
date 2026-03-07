"""Structured error response handler tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestStructuredErrorResponses:
    """Exception handlers return ErrorResponse JSON with error codes."""

    @pytest.mark.asyncio
    async def test_not_found_returns_error_code(
        self, client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/v1/backups/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_auth_failure_returns_error_code(
        self, client: AsyncClient,
    ) -> None:
        resp = await client.post(
            "/api/v1/vote",
            json={
                "voter": "voter-1",
                "status": "up",
                "timestamp": 9999999999,
                "signature": "bad",
            },
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "AUTHENTICATION_FAILED"
