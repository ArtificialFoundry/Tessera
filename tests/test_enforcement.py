"""Tests for the enforcement engine."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from tessera.engines.backup import BackupEngine
from tessera.engines.enforcement import (
    EnforcementEngine,
    EnforcementError,
    EnforcementMode,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def mock_active() -> AsyncMock:
    """Mock active TechnitiumClient."""
    mock = AsyncMock()
    mock._base_url = "https://test"
    mock.list_scopes = AsyncMock(return_value=[{"name": "LAN", "enabled": True}])
    mock.get_scope = AsyncMock(
        return_value={
            "startingAddress": "10.0.0.1",
            "subnetMask": "255.255.255.0",
            "reservedLeases": [
                {
                    "hardwareAddress": "AA:BB:CC:DD:EE:01",
                    "address": "10.0.0.10",
                    "hostName": "srv1",
                    "comments": "",
                }
            ],
        }
    )
    mock.set_scope = AsyncMock()
    mock.add_reservation = AsyncMock()
    mock.remove_reservation = AsyncMock()
    return mock


@pytest.fixture
def backup_eng(tmp_path: Path, mock_active: AsyncMock) -> BackupEngine:
    """Backup engine with temp dir and mock client."""
    e = BackupEngine(backup_dir=tmp_path / "backups", max_backups=10, auto_interval=0)
    e.set_active_client(mock_active)
    return e


@pytest.fixture
def engine(backup_eng: BackupEngine) -> EnforcementEngine:
    """Enforcement engine with backup engine."""
    e = EnforcementEngine(check_interval=60)
    e.set_backup_engine(backup_eng)
    return e


class TestEnforcementEngine:
    """Tests for EnforcementEngine."""

    def test_name_and_version(self) -> None:
        """Engine has correct metadata."""
        e = EnforcementEngine()
        assert e.name == "enforcement"

    def test_default_mode_is_off(self, engine: EnforcementEngine) -> None:
        """Default mode is OFF."""
        assert engine.mode == EnforcementMode.OFF

    async def test_pin_backup(
        self, engine: EnforcementEngine, backup_eng: BackupEngine
    ) -> None:
        """Pin a valid backup sets pinned ID."""
        await backup_eng.start()
        manifest = await backup_eng.create_backup()
        await engine.pin_backup(manifest.backup_id)
        assert engine.enforcement_state.pinned_backup_id == manifest.backup_id

    async def test_pin_nonexistent_raises(self, engine: EnforcementEngine) -> None:
        """Pin a missing backup raises NotFoundError."""
        from tessera.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await engine.pin_backup("nonexistent")

    async def test_set_mode_monitor(
        self, engine: EnforcementEngine, backup_eng: BackupEngine
    ) -> None:
        """Switch to monitor mode after pinning."""
        await backup_eng.start()
        manifest = await backup_eng.create_backup()
        await engine.pin_backup(manifest.backup_id)
        engine.set_mode(EnforcementMode.MONITOR)
        await engine.stop()
        assert engine.mode == EnforcementMode.MONITOR

    def test_set_mode_without_pin_raises(self, engine: EnforcementEngine) -> None:
        """Cannot enable monitor/enforce without a pinned backup."""
        with pytest.raises(EnforcementError, match="no backup pinned"):
            engine.set_mode(EnforcementMode.MONITOR)

    async def test_unpin(
        self, engine: EnforcementEngine, backup_eng: BackupEngine
    ) -> None:
        """Unpin clears the pin and switches to OFF."""
        await backup_eng.start()
        manifest = await backup_eng.create_backup()
        await engine.pin_backup(manifest.backup_id)
        engine.unpin()
        assert engine.enforcement_state.pinned_backup_id == ""
        assert engine.mode == EnforcementMode.OFF

    async def test_check_drift_no_changes(
        self,
        engine: EnforcementEngine,
        backup_eng: BackupEngine,
    ) -> None:
        """No drift when live state matches backup."""
        await backup_eng.start()
        manifest = await backup_eng.create_backup()
        await engine.pin_backup(manifest.backup_id)
        engine.set_mode(EnforcementMode.MONITOR)
        await engine.stop()
        result = await engine.check_drift()
        assert result["drift_detected"] is False

    async def test_check_drift_detects_changes(
        self,
        engine: EnforcementEngine,
        backup_eng: BackupEngine,
        mock_active: AsyncMock,
    ) -> None:
        """Drift detected when live state differs from backup."""
        await backup_eng.start()
        manifest = await backup_eng.create_backup()
        await engine.pin_backup(manifest.backup_id)
        engine.set_mode(EnforcementMode.MONITOR)
        await engine.stop()

        # Simulate drift: remove reservation
        mock_active.get_scope = AsyncMock(
            return_value={
                "startingAddress": "10.0.0.1",
                "subnetMask": "255.255.255.0",
                "reservedLeases": [],
            }
        )
        result = await engine.check_drift()
        assert result["drift_detected"] is True
        assert result["action"] == "logged"
        assert engine.enforcement_state.drift_count == 1

    async def test_enforce_mode_auto_restores(
        self,
        engine: EnforcementEngine,
        backup_eng: BackupEngine,
        mock_active: AsyncMock,
    ) -> None:
        """Enforce mode restores state on drift."""
        await backup_eng.start()
        manifest = await backup_eng.create_backup()
        await engine.pin_backup(manifest.backup_id)
        engine.set_mode(EnforcementMode.ENFORCE)
        await engine.stop()

        # Simulate drift
        mock_active.get_scope = AsyncMock(
            return_value={
                "startingAddress": "10.0.0.1",
                "subnetMask": "255.255.255.0",
                "reservedLeases": [],
            }
        )
        result = await engine.check_drift()
        assert result["drift_detected"] is True
        assert result["action"] == "restored"
        assert engine.enforcement_state.restore_count == 1
        mock_active.add_reservation.assert_called()

    async def test_check_without_pin_raises(self, engine: EnforcementEngine) -> None:
        """Check drift without pin raises error."""
        with pytest.raises(EnforcementError, match="No backup pinned"):
            await engine.check_drift()

    async def test_drift_history_capped(
        self,
        engine: EnforcementEngine,
        backup_eng: BackupEngine,
        mock_active: AsyncMock,
    ) -> None:
        """Drift history doesn't grow unbounded."""
        await backup_eng.start()
        manifest = await backup_eng.create_backup()
        await engine.pin_backup(manifest.backup_id)
        engine.set_mode(EnforcementMode.MONITOR)
        await engine.stop()

        # Generate unique drifts by varying the reservation IP each iteration
        for i in range(55):
            mock_active.get_scope = AsyncMock(
                return_value={
                    "startingAddress": "10.0.0.1",
                    "subnetMask": "255.255.255.0",
                    "reservedLeases": [
                        {
                            "hardwareAddress": f"AA:BB:CC:DD:EE:{i:02X}",
                            "address": f"10.0.0.{100 + i}",
                            "hostName": f"rogue-{i}",
                            "comments": "",
                        }
                    ],
                }
            )
            await engine.check_drift()
        assert len(engine.enforcement_state.history) <= 50

    async def test_duplicate_drift_increments_count(
        self,
        engine: EnforcementEngine,
        backup_eng: BackupEngine,
        mock_active: AsyncMock,
    ) -> None:
        """Consecutive identical drifts increment count."""
        await backup_eng.start()
        manifest = await backup_eng.create_backup()
        await engine.pin_backup(manifest.backup_id)
        engine.set_mode(EnforcementMode.MONITOR)
        await engine.stop()

        # Same drift every time: reservation removed
        mock_active.get_scope = AsyncMock(
            return_value={
                "startingAddress": "10.0.0.1",
                "subnetMask": "255.255.255.0",
                "reservedLeases": [],
            }
        )
        for _ in range(5):
            await engine.check_drift()

        assert len(engine.enforcement_state.history) == 1
        assert engine.enforcement_state.history[0].count == 5
        assert engine.enforcement_state.history[0].last_seen > 0

    async def test_different_drift_creates_new_entry(
        self,
        engine: EnforcementEngine,
        backup_eng: BackupEngine,
        mock_active: AsyncMock,
    ) -> None:
        """Different drift creates a new history entry."""
        await backup_eng.start()
        manifest = await backup_eng.create_backup()
        await engine.pin_backup(manifest.backup_id)
        engine.set_mode(EnforcementMode.MONITOR)
        await engine.stop()

        # First drift: reservation removed
        mock_active.get_scope = AsyncMock(
            return_value={
                "startingAddress": "10.0.0.1",
                "subnetMask": "255.255.255.0",
                "reservedLeases": [],
            }
        )
        await engine.check_drift()

        # Second drift: different — extra reservation added
        mock_active.get_scope = AsyncMock(
            return_value={
                "startingAddress": "10.0.0.1",
                "subnetMask": "255.255.255.0",
                "reservedLeases": [
                    {
                        "hardwareAddress": "AA:BB:CC:DD:EE:01",
                        "address": "10.0.0.10",
                        "hostName": "srv1",
                        "comments": "",
                    },
                    {
                        "hardwareAddress": "FF:FF:FF:FF:FF:FF",
                        "address": "10.0.0.99",
                        "hostName": "rogue",
                        "comments": "",
                    },
                ],
            }
        )
        await engine.check_drift()

        assert len(engine.enforcement_state.history) == 2

    async def test_health(self, engine: EnforcementEngine) -> None:
        """Health reflects mode and drift count."""
        await engine.start()
        health = await engine.check_health()
        assert "off" in health.message.lower()

    def test_metrics(self, engine: EnforcementEngine) -> None:
        """Metrics returns expected keys."""
        metrics = engine.get_metrics()
        assert "mode" in metrics
        assert "drift_count" in metrics
        assert "restore_count" in metrics


class TestCheckLoopJitter:
    """Jitter in enforcement check loop."""

    async def test_check_loop_applies_initial_delay_and_interval_jitter(self) -> None:
        """Check loop sleep includes jitter."""
        import asyncio
        import contextlib
        from unittest.mock import patch

        engine = EnforcementEngine(check_interval=100)
        engine.set_backup_engine(AsyncMock())

        sleeps: list[float] = []
        call_count = 0

        async def fake_sleep(duration: float) -> None:
            nonlocal call_count
            sleeps.append(duration)
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError

        sleep_patch = patch(
            "tessera.engines.enforcement.asyncio.sleep",
            side_effect=fake_sleep,
        )
        drift_patch = patch.object(
            engine, "check_drift", new_callable=AsyncMock,
        )
        with sleep_patch, drift_patch:
            engine._state.pinned_backup_id = "test"
            with contextlib.suppress(asyncio.CancelledError):
                await engine._check_loop()

        # First sleep is the initial delay (5-30s)
        assert 5 <= sleeps[0] <= 30
        # Second sleep = interval + jitter (110-130)
        if len(sleeps) > 1:
            assert 110 <= sleeps[1] <= 130
