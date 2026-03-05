"""Tests for lease API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from httpx import AsyncClient


class TestLeasesEndpoints:
    """Tests for /api/v1/leases endpoints."""

    async def test_all_leases_groups_by_scope(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """All leases groups leases by scope using IP range filtering."""
        mock_technitium.list_scopes.return_value = [
            {
                "name": "LAN",
                "startingAddress": "192.168.1.1",
                "endingAddress": "192.168.1.254",
            },
            {
                "name": "Guest",
                "startingAddress": "10.0.0.1",
                "endingAddress": "10.0.0.254",
            },
        ]
        mock_technitium.get_leases.return_value = [
            {"address": "192.168.1.10", "type": "Dynamic"},
            {"address": "10.0.0.5", "type": "Dynamic"},
        ]
        resp = await client.get("/api/v1/leases")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["scopes"]["LAN"]) == 1
        assert data["scopes"]["LAN"][0]["address"] == "192.168.1.10"
        assert len(data["scopes"]["Guest"]) == 1
        assert data["scopes"]["Guest"][0]["address"] == "10.0.0.5"

    async def test_scope_leases_filtered(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Scope leases filters by IP range since Technitium ignores name param."""
        mock_technitium.get_scope.return_value = {
            "startingAddress": "192.168.1.1",
            "endingAddress": "192.168.1.254",
        }
        mock_technitium.get_leases.return_value = [
            {"address": "192.168.1.10", "type": "Dynamic"},
            {"address": "10.0.0.5", "type": "Dynamic"},
        ]
        resp = await client.get("/api/v1/leases/LAN")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["leases"]) == 1
        assert data["leases"][0]["address"] == "192.168.1.10"

    async def test_remove_lease(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Remove lease calls remove_lease on client."""
        resp = await client.delete("/api/v1/leases/LAN/192.168.1.10")
        assert resp.status_code == 200
        assert "removed" in resp.json()["message"]
        mock_technitium.remove_lease.assert_called_once_with(
            "LAN", address="192.168.1.10"
        )

    async def test_convert_lease_to_reservation(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Convert lease finds MAC from lease list and adds reservation."""
        mock_technitium.get_leases.return_value = [
            {
                "address": "192.168.1.10",
                "hardwareAddress": "AA:BB:CC:DD:EE:FF",
                "hostName": "my-device",
                "type": "Dynamic",
            }
        ]
        resp = await client.post("/api/v1/leases/LAN/192.168.1.10/convert")
        assert resp.status_code == 200
        assert "converted" in resp.json()["message"]
        mock_technitium.add_reservation.assert_called_once_with(
            "LAN",
            hardware_address="AA:BB:CC:DD:EE:FF",
            address="192.168.1.10",
            host_name="my-device",
            comments="Converted from dynamic lease",
        )

    async def test_convert_lease_not_found(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Convert lease returns 404 when lease address not in list."""
        mock_technitium.get_leases.return_value = []
        resp = await client.post("/api/v1/leases/LAN/192.168.1.99/convert")
        assert resp.status_code == 404

    async def test_convert_lease_no_mac(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Convert lease returns 400 when lease has no MAC."""
        mock_technitium.get_leases.return_value = [
            {"address": "192.168.1.10", "hardwareAddress": "", "type": "Dynamic"}
        ]
        resp = await client.post("/api/v1/leases/LAN/192.168.1.10/convert")
        assert resp.status_code == 400
