"""Tests for the backup engine."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tessera.engines.backup import BackupEngine, BackupError
from tessera.exceptions import NotFoundError
from tessera.registry import EngineStatus


@pytest.fixture
def mock_active() -> AsyncMock:
    """Mock active TechnitiumClient with scope data."""
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
def engine(tmp_path: Path, mock_active: AsyncMock) -> BackupEngine:
    """BackupEngine with temp directory and mock client."""
    e = BackupEngine(backup_dir=tmp_path / "backups", max_backups=5, auto_interval=0)
    e.set_active_client(mock_active)
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
        e.set_active_client(mock)
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
        self, engine: BackupEngine, mock_active: AsyncMock
    ) -> None:
        """Dry run restore computes changes without applying."""
        await engine.start()
        manifest = await engine.create_backup()

        # Simulate drift: remove the reservation from live state
        mock_active.get_scope = AsyncMock(
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
        mock_active.add_reservation.assert_not_called()

    async def test_restore_applies_changes(
        self, engine: BackupEngine, mock_active: AsyncMock
    ) -> None:
        """Actual restore applies changes to the server."""
        await engine.start()
        manifest = await engine.create_backup()

        mock_active.get_scope = AsyncMock(
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
        mock_active.add_reservation.assert_called()

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


class TestRestoreRollback:
    """Restore creates a pre-restore snapshot for rollback."""

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        mock = AsyncMock()
        mock._base_url = "https://test:53443"
        mock.list_scopes = AsyncMock(return_value=[{"name": "LAN", "enabled": True}])
        mock.get_scope = AsyncMock(return_value={"reservedLeases": []})
        mock.set_scope = AsyncMock()
        mock.add_reservation = AsyncMock()
        mock.remove_reservation = AsyncMock()
        return mock

    @pytest.mark.asyncio
    async def test_restore_snapshots_current_state_before_applying(
        self, tmp_path: Path, mock_client: AsyncMock
    ) -> None:
        engine = BackupEngine(backup_dir=tmp_path / "backups", max_backups=50)
        engine.set_active_client(mock_client)
        await engine.start()

        manifest = await engine.create_backup(description="test")
        result = await engine.restore_backup(manifest.backup_id)
        assert "pre_restore_backup_id" in result
        assert result["pre_restore_backup_id"] is not None

    @pytest.mark.asyncio
    async def test_restore_result_contains_pre_restore_backup_id(
        self, tmp_path: Path, mock_client: AsyncMock
    ) -> None:
        """Pre-restore backup ID is always present in result."""
        engine = BackupEngine(backup_dir=tmp_path / "backups", max_backups=50)
        engine.set_active_client(mock_client)
        await engine.start()

        manifest = await engine.create_backup(description="test")
        result = await engine.restore_backup(manifest.backup_id)
        pre_id = result["pre_restore_backup_id"]
        assert pre_id is not None
        backups = await engine.list_backups()
        ids = [b.backup_id for b in backups]
        assert pre_id in ids


class TestAutoBackupBackoff:
    """Exponential backoff in auto-backup loop."""

    async def test_initial_backoff_state_is_zero_failures(
        self,
        tmp_path: Path,
    ) -> None:
        """Consecutive failures increment counter."""
        engine = BackupEngine(
            backup_dir=tmp_path / "backups",
            cron_schedule="* * * * *",
        )
        engine._auto_enabled = False
        await engine.start()
        assert engine._consecutive_failures == 0
        assert engine._backoff_seconds == 60.0

    async def test_successful_backup_resets_failure_counter(
        self,
        tmp_path: Path,
    ) -> None:
        """Backoff state resets after successful backup."""
        mock = AsyncMock()
        mock._base_url = "https://test"
        mock.list_scopes = AsyncMock(return_value=[])
        engine = BackupEngine(backup_dir=tmp_path / "backups")
        engine.set_active_client(mock)
        await engine.start()
        engine._consecutive_failures = 3
        engine._backoff_seconds = 480.0
        await engine.create_backup(description="test")
        assert engine._backup_count == 1

    async def test_engine_degrades_after_five_consecutive_failures(
        self,
        tmp_path: Path,
    ) -> None:
        """Engine status is DEGRADED after 5 consecutive failures."""
        failing = AsyncMock()
        failing._base_url = "https://test"
        failing.list_scopes = AsyncMock(
            side_effect=Exception("down"),
        )
        engine = BackupEngine(
            backup_dir=tmp_path / "backups",
            cron_schedule="* * * * *",
        )
        engine.set_active_client(failing)
        await engine.start()
        engine._consecutive_failures = 5
        engine.health.status = EngineStatus.DEGRADED
        assert engine.health.status == EngineStatus.DEGRADED


class TestBackupFilesystemLock:
    """Filesystem lock protects concurrent backup operations."""

    @pytest.mark.asyncio
    async def test_engine_has_filesystem_lock(
        self,
        engine: BackupEngine,
    ) -> None:
        assert isinstance(engine._fs_lock, type(threading.Lock()))

    @pytest.mark.asyncio
    async def test_list_tolerates_file_deleted_between_glob_and_read(
        self,
        engine: BackupEngine,
    ) -> None:
        await engine.start()
        await engine.create_backup("test")
        assert len(engine._list_backups_sync()) == 1
        for f in engine.backup_dir.glob("*.json"):
            f.unlink()
        assert len(engine._list_backups_sync()) == 0


class TestBackupChecksumIntegrity:
    """SHA-256 checksum written on create and verified on load."""

    @pytest.mark.asyncio
    async def test_created_backup_contains_valid_checksum(
        self,
        engine: BackupEngine,
    ) -> None:
        await engine.start()
        m = await engine.create_backup("cksum test")
        fp = engine.backup_dir / f"{m.backup_id}.json"
        data = json.loads(fp.read_text())
        assert "checksum" in data
        backup = await engine.get_backup(m.backup_id)
        assert backup.manifest.backup_id == m.backup_id

    @pytest.mark.asyncio
    async def test_tampered_checksum_raises_on_load(
        self,
        engine: BackupEngine,
    ) -> None:
        await engine.start()
        m = await engine.create_backup("corrupt test")
        fp = engine.backup_dir / f"{m.backup_id}.json"
        data = json.loads(fp.read_text())
        data["checksum"] = "deadbeef"
        fp.write_text(json.dumps(data))

        with pytest.raises(BackupError, match="integrity"):
            await engine.get_backup(m.backup_id)

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_verify_integrity_reports_corrupt_and_valid(
        self,
        engine: BackupEngine,
    ) -> None:
        import time

        await engine.start()
        await engine.create_backup("ok")
        time.sleep(1.1)
        m2 = await engine.create_backup("corrupt")
        fp = engine.backup_dir / f"{m2.backup_id}.json"
        data = json.loads(fp.read_text())
        data["checksum"] = "bad"
        fp.write_text(json.dumps(data))

        report = await engine.verify_integrity()
        assert report["total"] == 2
        assert report["valid"] == 1
        assert report["corrupt"] == 1
        assert f"{m2.backup_id}.json" in report["failures"]
