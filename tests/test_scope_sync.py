"""Tests for the scope sync engine."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from tessera.engines.scope_sync import ScopeSyncEngine
from tessera.exceptions import ScopeSyncError


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
