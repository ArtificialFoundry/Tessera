"""Tests for lease API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from httpx import AsyncClient


class TestLeasesEndpoints:
    """Tests for /api/v1/leases endpoints."""

    async def test_all_leases(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """All leases endpoint returns leases by scope."""
        mock_technitium.list_scopes.return_value = [{"name": "LAN"}]
        mock_technitium.get_leases.return_value = [
            {"address": "192.168.1.10", "type": "Dynamic"}
        ]
        resp = await client.get("/api/v1/leases")
        assert resp.status_code == 200
        data = resp.json()
        assert "LAN" in data["scopes"]
        assert len(data["scopes"]["LAN"]) == 1

    async def test_scope_leases(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Scope leases endpoint returns leases for a scope."""
        mock_technitium.get_leases.return_value = [
            {"address": "192.168.1.10", "type": "Dynamic"}
        ]
        resp = await client.get("/api/v1/leases/LAN")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "LAN"
        assert len(data["leases"]) == 1
