"""Tests for health and metrics endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.asyncio
class TestHealthEndpoint:
    """Tests for /api/v1/health."""

    async def test_shallow_health(self, client: AsyncClient) -> None:
        """Default health check returns engine statuses."""
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")
        assert "engines" in data

    async def test_deep_health_flag(self, client: AsyncClient) -> None:
        """Deep health check includes dependency probes."""
        resp = await client.get("/api/v1/health?deep=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "engines" in data


@pytest.mark.asyncio
class TestMetricsEndpoint:
    """Tests for /api/v1/metrics."""

    async def test_metrics_returns_text(self, client: AsyncClient) -> None:
        """Metrics endpoint returns Prometheus text format."""
        resp = await client.get("/api/v1/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        body = resp.text
        assert "tessera_engine_health" in body
        assert "# HELP" in body
        assert "# TYPE" in body

    async def test_metrics_contains_engine_gauges(self, client: AsyncClient) -> None:
        """Each registered engine has a health gauge."""
        resp = await client.get("/api/v1/metrics")
        body = resp.text
        # Should have at least the failover engine metric
        assert "tessera_engine_health" in body
