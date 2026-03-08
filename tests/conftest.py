"""Shared test fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

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
    get_technitium_pool,
    get_voter_registry,
    require_admin,
)
from tessera.engines.backup import BackupEngine
from tessera.engines.enforcement import EnforcementEngine
from tessera.engines.failover import FailoverEngine
from tessera.engines.technitium import TechnitiumClient, TechnitiumPool
from tessera.engines.voter_registry import VoterRegistryEngine
from tessera.registry import EngineRegistry, EngineStatus, ModuleRegistry

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
    """Fresh failover engine for each test (no active client = no verification)."""
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
    mock.server_name = "active"
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
def mock_pool(mock_technitium: AsyncMock) -> TechnitiumPool:
    """Mock TechnitiumPool with a single active."""
    pool = TechnitiumPool(token="test-token")
    mock_technitium._base_url = "https://test:53443"
    mock_technitium.health = MagicMock()
    mock_technitium.health.status = EngineStatus.REGISTERED
    mock_technitium.health.message = ""
    pool._clients["active"] = mock_technitium
    pool._roles["active"] = "active"
    pool._priorities["active"] = 0
    return pool


@pytest.fixture
async def backup_engine(tmp_path: Path, mock_technitium: AsyncMock) -> BackupEngine:
    """Fresh backup engine with temp directory."""
    engine = BackupEngine(
        backup_dir=tmp_path / "backups",
        max_backups=10,
        auto_interval=0,
    )
    engine.set_active_client(mock_technitium)
    await engine.start()
    return engine


@pytest.fixture
def enforcement_engine(backup_engine: BackupEngine) -> EnforcementEngine:
    """Fresh enforcement engine with backup engine."""
    engine = EnforcementEngine(check_interval=60)
    engine.set_backup_engine(backup_engine)
    return engine


@pytest.fixture
def voter_registry(tmp_path: Path) -> VoterRegistryEngine:
    """Fresh voter registry with temp files."""
    return VoterRegistryEngine(
        voter_keys_file=tmp_path / "voter-keys.json",
        voter_registry_file=tmp_path / "voter-registry.json",
        reg_tokens_file=tmp_path / "reg-tokens.json",
        static_registration_token="static-test-token",
        auto_approve=False,
        token_ttl=3600,
        psk_grace_period=60,
    )


@pytest.fixture
async def client(
    engine_registry: EngineRegistry,
    module_registry: ModuleRegistry,
    failover_engine: FailoverEngine,
    mock_technitium: AsyncMock,
    mock_pool: TechnitiumPool,
    backup_engine: BackupEngine,
    enforcement_engine: EnforcementEngine,
    voter_registry: VoterRegistryEngine,
) -> AsyncGenerator[AsyncClient]:
    """Async HTTP client with DI overrides for isolated tests."""
    app = create_app()
    app.dependency_overrides[get_engine_registry] = lambda: engine_registry
    app.dependency_overrides[get_module_registry] = lambda: module_registry
    app.dependency_overrides[get_failover_engine] = lambda: failover_engine
    app.dependency_overrides[get_technitium_client] = lambda: mock_technitium
    app.dependency_overrides[get_technitium_pool] = lambda: mock_pool
    app.dependency_overrides[get_backup_engine] = lambda: backup_engine
    app.dependency_overrides[get_enforcement_engine] = lambda: enforcement_engine
    app.dependency_overrides[get_voter_registry] = lambda: voter_registry
    app.dependency_overrides[require_admin] = lambda: None

    from tessera.deps import dhcp_write_guard

    app.dependency_overrides[dhcp_write_guard] = lambda: enforcement_engine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
