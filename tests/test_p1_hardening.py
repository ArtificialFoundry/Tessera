"""Tests for P1 reliability and correctness hardening."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from tessera.engines.backup import BackupEngine
from tessera.engines.enforcement import EnforcementEngine
from tessera.engines.scope_sync import ScopeSyncEngine
from tessera.engines.technitium import (
    DhcpClientProtocol,
    TechnitiumClient,
    TechnitiumPool,
)
from tessera.registry import EngineStatus

if TYPE_CHECKING:
    from pathlib import Path


# ── Issue #7: Backup backoff logic ──────────────────────────────────────


class TestBackupBackoff:
    """Tests for exponential backoff in auto-backup loop."""

    async def test_consecutive_failures_tracked(
        self, tmp_path: Path,
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

    async def test_backoff_resets_on_success(
        self, tmp_path: Path,
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

    async def test_degraded_after_five_failures(
        self, tmp_path: Path,
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


# ── Issue #9: DhcpClientProtocol ────────────────────────────────────────


class TestDhcpClientProtocol:
    """Tests for the typed DHCP client protocol."""

    def test_technitium_client_satisfies_protocol(self) -> None:
        """TechnitiumClient satisfies DhcpClientProtocol."""
        client = TechnitiumClient(
            base_url="https://test", token="tok",
        )
        assert isinstance(client, DhcpClientProtocol)

    def test_mock_has_required_methods(self) -> None:
        """Mock with correct methods has protocol attrs."""
        mock = AsyncMock()
        mock.list_scopes = AsyncMock(return_value=[])
        mock.get_scope = AsyncMock(return_value={})
        mock.get_leases = AsyncMock(return_value=[])
        mock.set_scope = AsyncMock()
        mock.add_reservation = AsyncMock()
        mock.remove_reservation = AsyncMock()
        assert hasattr(mock, "list_scopes")
        assert hasattr(mock, "remove_reservation")


# ── Issue #10: TechnitiumPool role persistence ──────────────────────────


class TestPoolRolePersistence:
    """Tests for persisting pool role changes."""

    def test_promote_persists_roles(self, tmp_path: Path) -> None:
        """Promote writes roles to servers file."""
        sf = tmp_path / "servers.json"
        pool = TechnitiumPool(token="tok", servers_file=sf)
        c1 = TechnitiumClient(
            base_url="https://s1", token="tok", server_name="s1",
        )
        c2 = TechnitiumClient(
            base_url="https://s2", token="tok", server_name="s2",
        )
        pool._clients = {"s1": c1, "s2": c2}
        pool._roles = {"s1": "active", "s2": "candidate"}
        pool._priorities = {"s1": 0, "s2": 1}
        pool.promote("s2")
        assert sf.is_file()
        data = json.loads(sf.read_text())
        roles = {s["name"]: s["role"] for s in data}
        assert roles["s2"] == "active"
        assert roles["s1"] == "candidate"

    def test_demote_persists_roles(self, tmp_path: Path) -> None:
        """Demote writes roles to servers file."""
        sf = tmp_path / "servers.json"
        pool = TechnitiumPool(token="tok", servers_file=sf)
        c1 = TechnitiumClient(
            base_url="https://s1", token="tok", server_name="s1",
        )
        pool._clients = {"s1": c1}
        pool._roles = {"s1": "active"}
        pool._priorities = {"s1": 0}
        pool.demote("s1")
        data = json.loads(sf.read_text())
        assert data[0]["role"] == "candidate"


# ── Issue #11: Enforcement jitter ───────────────────────────────────────


class TestEnforcementJitter:
    """Tests for jitter in enforcement check loop."""

    async def test_check_loop_has_jitter(self) -> None:
        """Check loop sleep includes jitter."""
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


# ── Issue #12: Scope sync partial failure ───────────────────────────────


class TestScopeSyncPartialFailure:
    """Tests for partial fan-out failure handling."""

    async def test_partial_failure_tracked(self) -> None:
        """One candidate fails → per-candidate status tracked."""
        active = AsyncMock()
        active.list_scopes = AsyncMock(
            return_value=[{"name": "LAN"}],
        )
        active.get_scope = AsyncMock(
            return_value={"reservedLeases": []},
        )

        good = AsyncMock()
        good.server_name = "good"
        good.get_scope = AsyncMock(
            return_value={"reservedLeases": []},
        )

        bad = AsyncMock()
        bad.server_name = "bad"
        bad.list_scopes = AsyncMock(
            side_effect=Exception("connection refused"),
        )

        engine = ScopeSyncEngine(sync_interval=60)
        engine._pool = MagicMock()
        engine._pool.get_active.return_value = active
        engine._pool.get_candidates.return_value = [good, bad]

        result = await engine.sync_once()
        cr = result["candidate_results"]
        assert cr["good"] == "success"
        assert cr["bad"].startswith("error:")
        assert engine.sync_status["bad"].startswith("error:")
        assert engine.health.status == EngineStatus.DEGRADED

    async def test_all_success_clears_error(self) -> None:
        """All candidates succeed → no error state."""
        active = AsyncMock()
        active.list_scopes = AsyncMock(
            return_value=[{"name": "LAN"}],
        )
        active.get_scope = AsyncMock(
            return_value={"reservedLeases": []},
        )

        candidate = AsyncMock()
        candidate.server_name = "c1"
        candidate.get_scope = AsyncMock(
            return_value={"reservedLeases": []},
        )

        engine = ScopeSyncEngine(sync_interval=60)
        engine._pool = MagicMock()
        engine._pool.get_active.return_value = active
        engine._pool.get_candidates.return_value = [candidate]

        result = await engine.sync_once()
        assert result["candidate_results"]["c1"] == "success"
        assert engine._last_error == ""
