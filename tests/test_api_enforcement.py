"""Tests for enforcement API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestEnforcementAPI:
    """Tests for /api/v1/enforcement endpoints."""

    async def test_get_status(self, client: AsyncClient) -> None:
        """Get enforcement status returns default state."""
        resp = await client.get("/api/v1/enforcement")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "off"
        assert data["pinned_backup_id"] == ""

    async def test_set_mode_without_pin_fails(self, client: AsyncClient) -> None:
        """Cannot enable monitor without pinned backup."""
        resp = await client.post("/api/v1/enforcement/mode", json={"mode": "monitor"})
        assert resp.status_code == 400

    async def test_pin_and_monitor(
        self,
        client: AsyncClient,
        mock_technitium: AsyncMock,
    ) -> None:
        """Pin a backup then switch to monitor mode."""
        mock_technitium.list_scopes = AsyncMock(
            return_value=[{"name": "LAN", "enabled": True}]
        )
        mock_technitium.get_scope = AsyncMock(
            return_value={"startingAddress": "10.0.0.1", "reservedLeases": []}
        )

        resp = await client.post("/api/v1/backups", json={})
        backup_id = resp.json()["backup_id"]

        resp = await client.post(
            "/api/v1/enforcement/pin", json={"backup_id": backup_id}
        )
        assert resp.status_code == 200

        resp = await client.post("/api/v1/enforcement/mode", json={"mode": "monitor"})
        assert resp.status_code == 200

        resp = await client.get("/api/v1/enforcement")
        assert resp.json()["mode"] == "monitor"

    async def test_pin_nonexistent_returns_404(self, client: AsyncClient) -> None:
        """Pinning a nonexistent backup returns 404."""
        resp = await client.post("/api/v1/enforcement/pin", json={"backup_id": "nope"})
        assert resp.status_code == 404

    async def test_unpin(
        self,
        client: AsyncClient,
        mock_technitium: AsyncMock,
    ) -> None:
        """Unpin clears pin and mode."""
        mock_technitium.list_scopes = AsyncMock(return_value=[])

        resp = await client.post("/api/v1/backups", json={})
        backup_id = resp.json()["backup_id"]

        await client.post("/api/v1/enforcement/pin", json={"backup_id": backup_id})
        resp = await client.post("/api/v1/enforcement/unpin")
        assert resp.status_code == 200

        resp = await client.get("/api/v1/enforcement")
        assert resp.json()["mode"] == "off"
        assert resp.json()["pinned_backup_id"] == ""

    async def test_check_without_pin_fails(self, client: AsyncClient) -> None:
        """Drift check without pin returns 400."""
        resp = await client.post("/api/v1/enforcement/check")
        assert resp.status_code == 400

    async def test_invalid_mode_returns_400(self, client: AsyncClient) -> None:
        """Invalid mode string returns 400."""
        resp = await client.post("/api/v1/enforcement/mode", json={"mode": "invalid"})
        assert resp.status_code == 400

    async def test_drift_check(
        self,
        client: AsyncClient,
        mock_technitium: AsyncMock,
    ) -> None:
        """Manual drift check works after pin."""
        mock_technitium.list_scopes = AsyncMock(
            return_value=[{"name": "LAN", "enabled": True}]
        )
        mock_technitium.get_scope = AsyncMock(
            return_value={"startingAddress": "10.0.0.1", "reservedLeases": []}
        )

        resp = await client.post("/api/v1/backups", json={})
        backup_id = resp.json()["backup_id"]

        await client.post("/api/v1/enforcement/pin", json={"backup_id": backup_id})
        await client.post("/api/v1/enforcement/mode", json={"mode": "monitor"})

        resp = await client.post("/api/v1/enforcement/check")
        assert resp.status_code == 200
        assert resp.json()["drift_detected"] is False
