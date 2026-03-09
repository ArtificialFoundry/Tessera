"""Tests for Sprint 4 features: quorum alerting, pin-live, scope sync."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from tessera.engines.enforcement import EnforcementEngine, EnforcementError
from tessera.engines.scope_sync import ScopeSyncEngine
from tessera.registry import EngineStatus


@pytest.mark.asyncio
class TestPinLive:
    """Tests for EnforcementEngine.pin_live()."""

    async def test_pin_live_creates_and_pins(self) -> None:
        engine = EnforcementEngine()
        mock_backup = AsyncMock()
        manifest = MagicMock()
        manifest.backup_id = "live-001"
        mock_backup.create_backup.return_value = manifest
        engine.set_backup_engine(mock_backup)

        result = await engine.pin_live()
        assert result == "live-001"
        assert engine.enforcement_state.pinned_backup_id == "live-001"
        mock_backup.create_backup.assert_called_once_with(
            description="Pinned from live state"
        )

    async def test_pin_live_no_backup_engine(self) -> None:
        engine = EnforcementEngine()
        with pytest.raises(EnforcementError, match="Backup engine"):
            await engine.pin_live()


class TestQuorumAlerting:
    """Tests for failover quorum unreachable tracking."""

    @pytest.mark.asyncio
    async def test_quorum_reached_tracking(self) -> None:
        from unittest.mock import patch as _patch

        from tessera.engines.failover import (
            FailoverEngine,
            Vote,
            VoteStatus,
        )

        engine = FailoverEngine(
            quorum=2,
            vote_ttl=30,
            voter_keys={"v1": "k1", "v2": "k2"},
        )
        # Inject votes directly (bypass signature check)
        now = time.time()
        engine._votes["v1"] = Vote(voter="v1", status=VoteStatus.UP, timestamp=now)
        engine._votes["v2"] = Vote(voter="v2", status=VoteStatus.UP, timestamp=now)

        votes = list(engine._votes.values())
        with (
            _patch.object(engine, "_verify_votes", return_value=votes),
            _patch.object(
                engine,
                "_verify_active_health",
                return_value=True,
            ),
        ):
            await engine.evaluate_quorum()

        assert engine._last_quorum_reached > 0
        assert not engine._quorum_warned

    @pytest.mark.asyncio
    async def test_quorum_warning_in_health(self) -> None:
        from tessera.engines.failover import FailoverEngine

        engine = FailoverEngine(quorum=3, vote_ttl=30)
        engine._quorum_warned = True
        h = await engine.check_health()
        assert h.status == EngineStatus.DEGRADED
        assert "quorum unreachable" in h.message

    def test_quorum_in_metrics(self) -> None:
        from tessera.engines.failover import FailoverEngine

        engine = FailoverEngine(quorum=3, vote_ttl=30)
        engine._last_quorum_reached = 1234.0
        m = engine.get_metrics()
        assert m["quorum"] == 3
        assert m["quorum_reached"] == 1
        assert m["last_quorum_reached"] == 1234.0


@pytest.mark.asyncio
class TestScopeSyncConflictDetection:
    """Tests for atomic snapshot + conflict detection in sync."""

    async def test_sync_detects_scope_change(self) -> None:
        """If active scope set changes mid-snapshot, sync aborts."""
        engine = ScopeSyncEngine()
        active = AsyncMock()
        candidate = AsyncMock()
        candidate.server_name = "cand-1"

        # First list_scopes call returns one scope
        # Second call (snap check) returns different set
        active.list_scopes.side_effect = [
            [{"name": "scope-a"}],
            [{"name": "scope-a"}, {"name": "scope-b"}],
        ]
        active.get_scope.return_value = {"reservedLeases": []}

        candidate.get_scope.return_value = {"reservedLeases": []}

        result = await engine._sync_to_candidate(active, candidate)
        # Should have aborted — no syncs applied
        assert result["scopes_synced"] == 0

    async def test_sync_succeeds_on_stable_state(self) -> None:
        """Normal sync completes when snapshots match."""
        engine = ScopeSyncEngine()
        active = AsyncMock()
        candidate = AsyncMock()
        candidate.server_name = "cand-1"

        scopes = [{"name": "scope-a"}]
        active.list_scopes.side_effect = [scopes, scopes]
        active.get_scope.return_value = {
            "reservedLeases": [
                {
                    "hardwareAddress": "AA:BB:CC:DD:EE:FF",
                    "address": "10.0.0.1",
                    "hostName": "host1",
                    "comments": "",
                }
            ]
        }
        candidate.get_scope.return_value = {"reservedLeases": []}

        result = await engine._sync_to_candidate(active, candidate)
        assert result["scopes_synced"] == 1
        assert result["reservations_synced"] == 1
