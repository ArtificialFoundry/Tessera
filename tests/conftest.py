"""Shared test fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from tessera.app import create_app
from tessera.deps import (
    get_engine_registry,
    get_failover_engine,
    get_module_registry,
    get_technitium_client,
)
from tessera.engines.failover import FailoverEngine
from tessera.engines.technitium import TechnitiumClient
from tessera.registry import EngineRegistry, ModuleRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


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
    """Fresh failover engine for each test."""
    return FailoverEngine(
        quorum=2,
        failover_rounds=2,
        failback_rounds=2,
        vote_ttl=90,
        voter_keys=voter_keys,
    )


@pytest.fixture
def mock_technitium() -> AsyncMock:
    """Mock TechnitiumClient."""
    mock = AsyncMock(spec=TechnitiumClient)
    mock.name = "technitium"
    mock.version = "1.0.0"
    mock.description = "Mock Technitium"
    mock.depends_on = ()
    mock.list_scopes = AsyncMock(return_value=[])
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
async def client(
    engine_registry: EngineRegistry,
    module_registry: ModuleRegistry,
    failover_engine: FailoverEngine,
    mock_technitium: AsyncMock,
) -> AsyncGenerator[AsyncClient]:
    """Async HTTP client with DI overrides for isolated tests."""
    app = create_app()
    app.dependency_overrides[get_engine_registry] = lambda: engine_registry
    app.dependency_overrides[get_module_registry] = lambda: module_registry
    app.dependency_overrides[get_failover_engine] = lambda: failover_engine
    app.dependency_overrides[get_technitium_client] = lambda: mock_technitium
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
