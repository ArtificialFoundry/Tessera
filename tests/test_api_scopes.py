"""Tests for DHCP scope API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from httpx import AsyncClient


class TestScopesEndpoints:
    """Tests for /api/v1/scopes endpoints."""

    async def test_list_scopes(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """List scopes returns scope list."""
        mock_technitium.list_scopes.return_value = [
            {
                "name": "LAN",
                "enabled": True,
                "startingAddress": "192.168.1.1",
                "endingAddress": "192.168.1.254",
                "subnetMask": "255.255.255.0",
            }
        ]
        resp = await client.get("/api/v1/scopes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["scopes"]) == 1
        assert data["scopes"][0]["name"] == "LAN"

    async def test_get_scope_detail(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Get scope returns detail."""
        mock_technitium.get_scope.return_value = {
            "name": "LAN",
            "startingAddress": "192.168.1.1",
        }
        resp = await client.get("/api/v1/scopes/LAN")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "LAN"

    async def test_add_reservation(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Add reservation returns success."""
        resp = await client.post(
            "/api/v1/scopes/LAN/reservations",
            json={
                "hardware_address": "AA:BB:CC:DD:EE:FF",
                "address": "192.168.1.100",
                "host_name": "test-host",
            },
        )
        assert resp.status_code == 200
        mock_technitium.add_reservation.assert_called_once()

    async def test_remove_reservation(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Remove reservation returns success."""
        resp = await client.delete("/api/v1/scopes/LAN/reservations/AA:BB:CC:DD:EE:FF")
        assert resp.status_code == 200
        mock_technitium.remove_reservation.assert_called_once()

    async def test_update_scope(self, client: AsyncClient) -> None:
        """Update scope returns success."""
        resp = await client.put(
            "/api/v1/scopes/LAN",
            json={"settings": {"leaseDuration": "3600"}},
        )
        assert resp.status_code == 200
