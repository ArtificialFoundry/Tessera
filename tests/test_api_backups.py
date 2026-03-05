"""Tests for backup API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestBackupAPI:
    """Tests for /api/v1/backups endpoints."""

    async def test_list_empty(self, client: AsyncClient) -> None:
        """List returns empty when no backups exist."""
        resp = await client.get("/api/v1/backups")
        assert resp.status_code == 200
        assert resp.json()["backups"] == []

    async def test_create_and_list(
        self,
        client: AsyncClient,
        mock_technitium: AsyncMock,
    ) -> None:
        """Create a backup then list it."""
        mock_technitium.list_scopes = AsyncMock(
            return_value=[{"name": "LAN", "enabled": True}]
        )
        mock_technitium.get_scope = AsyncMock(
            return_value={
                "startingAddress": "10.0.0.1",
                "reservedLeases": [],
            }
        )

        resp = await client.post("/api/v1/backups", json={"description": "Test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope_count"] == 1
        assert data["description"] == "Test"

        resp = await client.get("/api/v1/backups")
        assert len(resp.json()["backups"]) == 1

    async def test_get_backup(
        self,
        client: AsyncClient,
        mock_technitium: AsyncMock,
    ) -> None:
        """Get a specific backup by ID."""
        mock_technitium.list_scopes = AsyncMock(
            return_value=[{"name": "LAN", "enabled": True}]
        )
        mock_technitium.get_scope = AsyncMock(
            return_value={"startingAddress": "10.0.0.1", "reservedLeases": []}
        )

        resp = await client.post("/api/v1/backups", json={})
        backup_id = resp.json()["backup_id"]

        resp = await client.get(f"/api/v1/backups/{backup_id}")
        assert resp.status_code == 200
        assert resp.json()["manifest"]["backup_id"] == backup_id

    async def test_get_nonexistent_returns_404(self, client: AsyncClient) -> None:
        """Getting a missing backup returns 404."""
        resp = await client.get("/api/v1/backups/nonexistent")
        assert resp.status_code == 404

    async def test_delete_backup(
        self,
        client: AsyncClient,
        mock_technitium: AsyncMock,
    ) -> None:
        """Delete removes the backup."""
        mock_technitium.list_scopes = AsyncMock(return_value=[])

        resp = await client.post("/api/v1/backups", json={})
        backup_id = resp.json()["backup_id"]

        resp = await client.delete(f"/api/v1/backups/{backup_id}")
        assert resp.status_code == 200

        resp = await client.get(f"/api/v1/backups/{backup_id}")
        assert resp.status_code == 404

    async def test_restore_dry_run(
        self,
        client: AsyncClient,
        mock_technitium: AsyncMock,
    ) -> None:
        """Dry run restore returns changes without applying."""
        mock_technitium.list_scopes = AsyncMock(
            return_value=[{"name": "LAN", "enabled": True}]
        )
        mock_technitium.get_scope = AsyncMock(
            return_value={"startingAddress": "10.0.0.1", "reservedLeases": []}
        )

        resp = await client.post("/api/v1/backups", json={})
        backup_id = resp.json()["backup_id"]

        resp = await client.post(
            f"/api/v1/backups/{backup_id}/restore",
            json={"dry_run": True},
        )
        assert resp.status_code == 200
        assert resp.json()["dry_run"] is True

    async def test_get_settings(self, client: AsyncClient) -> None:
        """Get backup settings returns defaults."""
        resp = await client.get("/api/v1/backups/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "max_backups" in data
        assert "auto_enabled" in data

    async def test_update_settings(self, client: AsyncClient) -> None:
        """Update backup settings persists changes."""
        resp = await client.put(
            "/api/v1/backups/settings",
            json={"max_backups": 25, "cron_schedule": "0 */12 * * *"},
        )
        assert resp.status_code == 200
        assert resp.json()["max_backups"] == 25

        resp = await client.get("/api/v1/backups/settings")
        assert resp.json()["max_backups"] == 25
