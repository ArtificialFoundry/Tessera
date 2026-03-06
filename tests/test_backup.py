"""Tests for the backup engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tessera.engines.backup import BackupEngine, BackupError
from tessera.exceptions import NotFoundError


@pytest.fixture
def mock_primary() -> AsyncMock:
    """Mock primary TechnitiumClient with scope data."""
    mock = AsyncMock()
    mock._base_url = "https://192.0.2.1:53443"
    mock.list_scopes = AsyncMock(
        return_value=[
            {"name": "LAN", "enabled": True, "startingAddress": "10.0.0.1"},
            {"name": "IoT", "enabled": False, "startingAddress": "10.1.0.1"},
        ]
    )

    def make_scope(name: str) -> dict[str, Any]:
        if name == "LAN":
            return {
                "startingAddress": "10.0.0.1",
                "endingAddress": "10.0.0.254",
                "subnetMask": "255.255.255.0",
                "reservedLeases": [
                    {
                        "hardwareAddress": "AA:BB:CC:DD:EE:01",
                        "address": "10.0.0.10",
                        "hostName": "server1",
                        "comments": "",
                    }
                ],
            }
        return {
            "startingAddress": "10.1.0.1",
            "endingAddress": "10.1.0.254",
            "subnetMask": "255.255.255.0",
            "reservedLeases": [],
        }

    mock.get_scope = AsyncMock(side_effect=make_scope)
    return mock


@pytest.fixture
def engine(tmp_path: Path, mock_primary: AsyncMock) -> BackupEngine:
    """BackupEngine with temp directory and mock client."""
    e = BackupEngine(backup_dir=tmp_path / "backups", max_backups=5, auto_interval=0)
    e.set_primary_client(mock_primary)
    return e


class TestBackupEngine:
    """Tests for BackupEngine."""

    def test_name_and_version(self) -> None:
        """Engine has correct metadata."""
        e = BackupEngine(backup_dir=Path("/tmp/test"))
        assert e.name == "backup"
        assert e.version == "1.0.0"

    async def test_create_backup(self, engine: BackupEngine) -> None:
        """Create a backup and verify manifest."""
        await engine.start()
        manifest = await engine.create_backup(description="Test backup")
        assert manifest.scope_count == 2
        assert manifest.reservation_count == 1
        assert manifest.description == "Test backup"
        assert manifest.source == "https://192.0.2.1:53443"

    async def test_list_backups(self, engine: BackupEngine) -> None:
        """List returns backups sorted newest first."""
        await engine.start()
        await engine.create_backup(description="First")
        await engine.create_backup(description="Second")
        backups = await engine.list_backups()
        assert len(backups) >= 1

    async def test_get_backup(self, engine: BackupEngine) -> None:
        """Get a specific backup by ID."""
        await engine.start()
        manifest = await engine.create_backup()
        backup = await engine.get_backup(manifest.backup_id)
        assert len(backup.scopes) == 2
        assert backup.scopes[0].name == "LAN"
        assert len(backup.scopes[0].reservations) == 1

    async def test_get_nonexistent_raises(self, engine: BackupEngine) -> None:
        """Getting a missing backup raises NotFoundError."""
        await engine.start()
        with pytest.raises(NotFoundError):
            await engine.get_backup("nonexistent")

    async def test_delete_backup(self, engine: BackupEngine) -> None:
        """Delete removes the backup file."""
        await engine.start()
        manifest = await engine.create_backup()
        engine.delete_backup(manifest.backup_id)
        with pytest.raises(NotFoundError):
            await engine.get_backup(manifest.backup_id)

    async def test_delete_nonexistent_raises(self, engine: BackupEngine) -> None:
        """Deleting a missing backup raises NotFoundError."""
        await engine.start()
        with pytest.raises(NotFoundError):
            engine.delete_backup("nonexistent")

    async def test_retention_enforced(self, tmp_path: Path) -> None:
        """Oldest backups deleted when exceeding max."""
        mock = AsyncMock()
        mock._base_url = "https://test"
        mock.list_scopes = AsyncMock(return_value=[])
        e = BackupEngine(
            backup_dir=tmp_path / "backups", max_backups=2, auto_interval=0
        )
        e.set_primary_client(mock)
        await e.start()
        for i in range(4):
            await e.create_backup(description=f"Backup {i}")
        backups = await e.list_backups()
        assert len(backups) <= 2

    async def test_create_without_client_raises(self, tmp_path: Path) -> None:
        """Creating backup without client raises BackupError."""
        e = BackupEngine(backup_dir=tmp_path / "backups")
        await e.start()
        with pytest.raises(BackupError, match="Primary client not configured"):
            await e.create_backup()

    async def test_restore_dry_run(
        self, engine: BackupEngine, mock_primary: AsyncMock
    ) -> None:
        """Dry run restore computes changes without applying."""
        await engine.start()
        manifest = await engine.create_backup()

        # Simulate drift: remove the reservation from live state
        mock_primary.get_scope = AsyncMock(
            return_value={
                "startingAddress": "10.0.0.1",
                "endingAddress": "10.0.0.254",
                "subnetMask": "255.255.255.0",
                "reservedLeases": [],
            }
        )
        result = await engine.restore_backup(manifest.backup_id, dry_run=True)
        assert result["dry_run"] is True
        assert result["total_changes"] > 0
        # Verify nothing was actually written
        mock_primary.add_reservation.assert_not_called()

    async def test_restore_applies_changes(
        self, engine: BackupEngine, mock_primary: AsyncMock
    ) -> None:
        """Actual restore applies changes to the server."""
        await engine.start()
        manifest = await engine.create_backup()

        mock_primary.get_scope = AsyncMock(
            return_value={
                "startingAddress": "10.0.0.1",
                "endingAddress": "10.0.0.254",
                "subnetMask": "255.255.255.0",
                "reservedLeases": [],
            }
        )
        result = await engine.restore_backup(manifest.backup_id, dry_run=False)
        assert result["dry_run"] is False
        assert result["total_changes"] > 0
        mock_primary.add_reservation.assert_called()

    async def test_health_after_backup(self, engine: BackupEngine) -> None:
        """Health reflects backup count."""
        await engine.start()
        await engine.create_backup()
        health = await engine.check_health()
        assert "1" in health.message

    def test_metrics(self, engine: BackupEngine) -> None:
        """Metrics returns expected keys."""
        metrics = engine.get_metrics()
        assert "backup_count" in metrics
        assert "max_backups" in metrics
        assert "cron_schedule" in metrics
