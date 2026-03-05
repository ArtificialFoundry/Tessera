"""Tests for DHCP scope API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tessera.exceptions import TechnitiumError

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

    async def test_get_scope_not_found(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Get scope returns 404 when scope was not found."""
        mock_technitium.get_scope.side_effect = TechnitiumError(
            "Scope 'missing' was not found."
        )
        resp = await client.get("/api/v1/scopes/missing")
        assert resp.status_code == 404

    async def test_get_scope_upstream_error(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Get scope returns 502 on non-not-found Technitium errors."""
        mock_technitium.get_scope.side_effect = TechnitiumError("Connection refused")
        resp = await client.get("/api/v1/scopes/LAN")
        assert resp.status_code == 502

    async def test_create_scope(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Create scope calls set_scope with correct params."""
        resp = await client.post(
            "/api/v1/scopes",
            json={
                "name": "Test",
                "starting_address": "10.0.0.1",
                "ending_address": "10.0.0.254",
                "subnet_mask": "255.255.255.0",
                "router_address": "10.0.0.1",
                "domain_name": "test.local",
                "dns_servers": ["10.0.0.1"],
                "lease_time_days": 1,
            },
        )
        assert resp.status_code == 200
        assert "created" in resp.json()["message"]
        mock_technitium.set_scope.assert_called_once()
        call_args = mock_technitium.set_scope.call_args
        assert call_args[0][0] == "Test"
        settings = call_args[0][1]
        assert settings["startingAddress"] == "10.0.0.1"
        assert settings["dnsServers"] == "10.0.0.1"

    async def test_create_scope_minimal(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Create scope works with only required fields."""
        resp = await client.post(
            "/api/v1/scopes",
            json={
                "name": "Minimal",
                "starting_address": "10.0.0.1",
                "ending_address": "10.0.0.254",
                "subnet_mask": "255.255.255.0",
            },
        )
        assert resp.status_code == 200
        mock_technitium.set_scope.assert_called_once()

    async def test_update_scope(self, client: AsyncClient) -> None:
        """Update scope returns success."""
        resp = await client.put(
            "/api/v1/scopes/LAN",
            json={"settings": {"leaseDuration": "3600"}},
        )
        assert resp.status_code == 200

    async def test_delete_scope(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Delete scope calls delete and returns message."""
        resp = await client.delete("/api/v1/scopes/LAN")
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"]
        mock_technitium.delete_scope.assert_called_once_with("LAN")

    async def test_enable_scope(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Enable scope calls enable_scope."""
        resp = await client.post("/api/v1/scopes/LAN/enable")
        assert resp.status_code == 200
        assert "enabled" in resp.json()["message"]
        mock_technitium.enable_scope.assert_called_once_with("LAN")

    async def test_disable_scope(
        self, client: AsyncClient, mock_technitium: AsyncMock
    ) -> None:
        """Disable scope calls disable_scope."""
        resp = await client.post("/api/v1/scopes/LAN/disable")
        assert resp.status_code == 200
        assert "disabled" in resp.json()["message"]
        mock_technitium.disable_scope.assert_called_once_with("LAN")

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
