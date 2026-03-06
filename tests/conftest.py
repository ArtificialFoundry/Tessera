"""Shared test fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from tessera.app import create_app
from tessera.deps import (
    get_backup_engine,
    get_enforcement_engine,
    get_engine_registry,
    get_failover_engine,
    get_module_registry,
    get_technitium_client,
)
from tessera.engines.backup import BackupEngine
from tessera.engines.enforcement import EnforcementEngine
from tessera.engines.failover import FailoverEngine
from tessera.engines.technitium import TechnitiumClient
from tessera.registry import EngineRegistry, ModuleRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


@pytest.fixture
def engine_registry() -> EngineRegistry:
    """Fresh engine registry for each test."""
    return EngineRegistry()


@pytest.fixture
def module_registry() -> ModuleRegistry:
    """Fresh module registry for each test."""
    return ModuleRegistry()


@pytest.fixture
def voter_keys() -> dict[str, str]:
    """Test voter keys."""
    return {
        "voter-1": "test-key-ca1",
        "voter-2": "test-key-frontend1",
        "voter-3": "test-key-apps1",
    }


@pytest.fixture
def failover_engine(voter_keys: dict[str, str]) -> FailoverEngine:
    """Fresh failover engine for each test (no primary client = no verification)."""
    return FailoverEngine(
        quorum=2,
        failover_rounds=2,
        failback_rounds=2,
        vote_ttl=90,
        voter_keys=voter_keys,
        vote_cooldown=0,
    )


@pytest.fixture
def mock_technitium() -> AsyncMock:
    """Mock TechnitiumClient."""
    mock = AsyncMock(spec=TechnitiumClient)
    mock.name = "technitium"
    mock.version = "1.0.0"
    mock.description = "Mock Technitium"
    mock.depends_on = ()
    mock.list_scopes = AsyncMock(return_value=[{"name": "default", "enabled": True}])
    mock.get_scope = AsyncMock(return_value={})
    mock.get_leases = AsyncMock(return_value=[])
    mock.set_scope = AsyncMock()
    mock.delete_scope = AsyncMock()
    mock.enable_scope = AsyncMock()
    mock.disable_scope = AsyncMock()
    mock.add_reservation = AsyncMock()
    mock.remove_reservation = AsyncMock()
    mock.remove_lease = AsyncMock()
    return mock


@pytest.fixture
async def backup_engine(tmp_path: Path, mock_technitium: AsyncMock) -> BackupEngine:
    """Fresh backup engine with temp directory."""
    engine = BackupEngine(
        backup_dir=tmp_path / "backups",
        max_backups=10,
        auto_interval=0,
    )
    engine.set_primary_client(mock_technitium)
    await engine.start()
    return engine


@pytest.fixture
def enforcement_engine(backup_engine: BackupEngine) -> EnforcementEngine:
    """Fresh enforcement engine with backup engine."""
    engine = EnforcementEngine(check_interval=60)
    engine.set_backup_engine(backup_engine)
    return engine


@pytest.fixture
async def client(
    engine_registry: EngineRegistry,
    module_registry: ModuleRegistry,
    failover_engine: FailoverEngine,
    mock_technitium: AsyncMock,
    backup_engine: BackupEngine,
    enforcement_engine: EnforcementEngine,
) -> AsyncGenerator[AsyncClient]:
    """Async HTTP client with DI overrides for isolated tests."""
    app = create_app()
    app.dependency_overrides[get_engine_registry] = lambda: engine_registry
    app.dependency_overrides[get_module_registry] = lambda: module_registry
    app.dependency_overrides[get_failover_engine] = lambda: failover_engine
    app.dependency_overrides[get_technitium_client] = lambda: mock_technitium
    app.dependency_overrides[get_backup_engine] = lambda: backup_engine
    app.dependency_overrides[get_enforcement_engine] = lambda: enforcement_engine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
