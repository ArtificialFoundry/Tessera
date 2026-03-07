from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


class TestRequestSizeLimitMiddleware:
    """Request body size limit middleware."""

    @pytest.mark.asyncio
    async def test_request_within_limit_passes_through(self) -> None:
        from tessera.app import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/ping")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_request_exceeding_limit_returns_413(self) -> None:
        from tessera.app import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            big_body = "x" * (1_048_576 + 1)
            resp = await client.post(
                "/api/v1/vote",
                content=big_body,
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 413
