"""Tests for the scope sync engine."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from tessera.engines.scope_sync import ScopeSyncEngine


@pytest.fixture
def mock_primary() -> AsyncMock:
    """Mock primary TechnitiumClient."""
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
def mock_standby() -> AsyncMock:
    """Mock standby TechnitiumClient."""
    mock = AsyncMock()
    mock.get_scope = AsyncMock(return_value={"reservedLeases": []})
    mock.add_reservation = AsyncMock()
    mock.remove_reservation = AsyncMock()
    return mock


@pytest.fixture
def sync_engine(mock_primary: AsyncMock, mock_standby: AsyncMock) -> ScopeSyncEngine:
    """ScopeSyncEngine with mock clients."""
    engine = ScopeSyncEngine(sync_interval=60)
    engine.set_clients(mock_primary, mock_standby)
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
        mock_standby: AsyncMock,
    ) -> None:
        """Sync adds missing reservations to standby."""
        result = await sync_engine.sync_once()
        assert result["scopes_synced"] == 2
        assert result["reservations_synced"] == 1
        mock_standby.add_reservation.assert_called_once()

    async def test_sync_once_removes_stale_reservations(
        self,
        mock_primary: AsyncMock,
        mock_standby: AsyncMock,
    ) -> None:
        """Sync removes reservations not present on primary."""
        mock_primary.list_scopes = AsyncMock(return_value=[{"name": "LAN"}])
        mock_primary.get_scope = AsyncMock(return_value={"reservedLeases": []})
        mock_standby.get_scope = AsyncMock(
            return_value={
                "reservedLeases": [
                    {"hardwareAddress": "AA:BB:CC:DD:EE:99", "address": "10.0.0.1"}
                ]
            }
        )

        engine = ScopeSyncEngine(sync_interval=60)
        engine.set_clients(mock_primary, mock_standby)
        await engine.sync_once()
        mock_standby.remove_reservation.assert_called_once()

    async def test_sync_without_clients_raises(self) -> None:
        """Sync without configured clients raises RuntimeError."""
        engine = ScopeSyncEngine()
        with pytest.raises(RuntimeError, match="Clients not configured"):
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
