"""Tests for the scope sync engine."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tessera.engines.scope_sync import ScopeSyncEngine
from tessera.exceptions import ScopeSyncError
from tessera.registry import EngineStatus


@pytest.fixture
def mock_active() -> AsyncMock:
    """Mock active TechnitiumClient."""
    mock = AsyncMock()
    mock.list_scopes = AsyncMock(return_value=[{"name": "LAN"}, {"name": "IoT"}])

    def make_scope(name: str) -> dict[str, Any]:
        if name == "LAN":
            return {
                "reservedLeases": [
                    {
                        "hardwareAddress": "AA:BB:CC:DD:EE:01",
                        "address": "192.168.1.10",
                        "hostName": "server1",
                        "comments": "",
                    }
                ]
            }
        return {"reservedLeases": []}

    mock.get_scope = AsyncMock(side_effect=make_scope)
    return mock


@pytest.fixture
def mock_candidate() -> AsyncMock:
    """Mock candidate TechnitiumClient."""
    mock = AsyncMock()
    mock.get_scope = AsyncMock(return_value={"reservedLeases": []})
    mock.add_reservation = AsyncMock()
    mock.remove_reservation = AsyncMock()
    return mock


@pytest.fixture
def sync_engine(mock_active: AsyncMock, mock_candidate: AsyncMock) -> ScopeSyncEngine:
    """ScopeSyncEngine with mock clients."""
    engine = ScopeSyncEngine(sync_interval=60)
    engine.set_clients(mock_active, mock_candidate)
    return engine


class TestScopeSyncEngine:
    """Tests for ScopeSyncEngine."""

    def test_name_and_version(self) -> None:
        """Engine has correct metadata."""
        engine = ScopeSyncEngine()
        assert engine.name == "scope_sync"

    async def test_sync_once_adds_reservations(
        self,
        sync_engine: ScopeSyncEngine,
        mock_candidate: AsyncMock,
    ) -> None:
        """Sync adds missing reservations to candidate."""
        result = await sync_engine.sync_once()
        assert result["scopes_synced"] == 2
        assert result["reservations_synced"] == 1
        mock_candidate.add_reservation.assert_called_once()

    async def test_sync_once_removes_stale_reservations(
        self,
        mock_active: AsyncMock,
        mock_candidate: AsyncMock,
    ) -> None:
        """Sync removes reservations not present on active."""
        mock_active.list_scopes = AsyncMock(return_value=[{"name": "LAN"}])
        mock_active.get_scope = AsyncMock(return_value={"reservedLeases": []})
        mock_candidate.get_scope = AsyncMock(
            return_value={
                "reservedLeases": [
                    {"hardwareAddress": "AA:BB:CC:DD:EE:99", "address": "10.0.0.1"}
                ]
            }
        )

        engine = ScopeSyncEngine(sync_interval=60)
        engine.set_clients(mock_active, mock_candidate)
        await engine.sync_once()
        mock_candidate.remove_reservation.assert_called_once()

    async def test_sync_without_clients_raises(self) -> None:
        """Sync without configured clients raises ScopeSyncError."""
        engine = ScopeSyncEngine()
        with pytest.raises(ScopeSyncError, match="clients not configured"):
            await engine.sync_once()

    async def test_health_after_sync(self, sync_engine: ScopeSyncEngine) -> None:
        """Health shows sync count after successful sync."""
        await sync_engine.sync_once()
        health = await sync_engine.check_health()
        assert "1" in health.message

    def test_metrics(self) -> None:
        """Metrics returns expected keys."""
        engine = ScopeSyncEngine()
        metrics = engine.get_metrics()
        assert "sync_count" in metrics
        assert "sync_interval" in metrics


class TestSyncFanOutFailure:
    """Partial fan-out failure handling."""

    async def test_failed_candidate_recorded_in_sync_status(self) -> None:
        """One candidate fails → per-candidate status tracked."""
        active = AsyncMock()
        active.list_scopes = AsyncMock(
            return_value=[{"name": "LAN"}],
        )
        active.get_scope = AsyncMock(return_value={
            "reservedLeases": [
                {
                    "hardwareAddress": "AA:BB:CC:DD:EE:01",
                    "address": "10.0.0.10",
                    "hostName": "srv1",
                    "comments": "",
                },
            ],
        })

        good = AsyncMock()
        good.server_name = "good"
        good.get_scope = AsyncMock(
            return_value={"reservedLeases": []},
        )

        bad = AsyncMock()
        bad.server_name = "bad"
        bad.get_scope = AsyncMock(
            return_value={"reservedLeases": []},
        )
        bad.add_reservation = AsyncMock(
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

    async def test_all_candidates_succeed_clears_error_state(self) -> None:
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
