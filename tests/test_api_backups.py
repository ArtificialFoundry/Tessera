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
        assert resp.json()["pagination"]["total"] == 0

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

    async def test_list_pagination(
        self,
        client: AsyncClient,
        mock_technitium: AsyncMock,
    ) -> None:
        """List respects offset and limit params."""
        mock_technitium.list_scopes = AsyncMock(
            return_value=[{"name": "LAN", "enabled": True}]
        )
        mock_technitium.get_scope = AsyncMock(
            return_value={
                "startingAddress": "10.0.0.1",
                "reservedLeases": [],
            }
        )
        # Create 3 backups with unique IDs
        from unittest.mock import patch

        ids = ["20260101-000001", "20260101-000002", "20260101-000003"]
        call_count = 0

        orig_strftime = __import__("time").strftime

        def fake_strftime(fmt: str, t: object = None) -> str:
            nonlocal call_count
            if fmt == "%Y%m%d-%H%M%S":
                idx = min(call_count, len(ids) - 1)
                call_count += 1
                return ids[idx]
            return orig_strftime(fmt, t) if t else orig_strftime(fmt)

        with patch("tessera.engines.backup.time") as mt:
            mt.time.return_value = 1000000.0
            mt.gmtime = __import__("time").gmtime
            mt.strftime = fake_strftime
            for i in range(3):
                mt.time.return_value = 1000000.0 + i
                await client.post(
                    "/api/v1/backups",
                    json={"description": f"b{i}"},
                )

        # Default returns all 3
        resp = await client.get("/api/v1/backups")
        assert resp.json()["pagination"]["total"] == 3

        # Limit to 2
        resp = await client.get("/api/v1/backups?limit=2")
        assert len(resp.json()["backups"]) == 2
        assert resp.json()["pagination"]["total"] == 3

        # Offset past all
        resp = await client.get("/api/v1/backups?offset=10")
        assert len(resp.json()["backups"]) == 0
        assert resp.json()["pagination"]["total"] == 3
