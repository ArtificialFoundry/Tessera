"""Tests for admin API key authentication."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from tessera.app import create_app
from tessera.config import Settings
from tessera.deps import (
    get_backup_engine,
    get_enforcement_engine,
    get_engine_registry,
    get_failover_engine,
    get_module_registry,
    get_settings,
    get_technitium_client,
    get_technitium_pool,
    get_voter_registry,
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

ADMIN_KEY = "test-admin-secret-key"

# A representative set of admin endpoints to test
ADMIN_ENDPOINTS: list[tuple[str, str]] = [
    ("POST", "/api/v1/backups"),
    ("DELETE", "/api/v1/backups/some-id"),
    ("PUT", "/api/v1/backups/settings"),
    ("POST", "/api/v1/enforcement/mode"),
    ("POST", "/api/v1/enforcement/pin"),
    ("POST", "/api/v1/enforcement/unpin"),
    ("POST", "/api/v1/enforcement/check"),
    ("PUT", "/api/v1/enforcement/settings"),
    ("POST", "/api/v1/enforcement/accept"),
    ("POST", "/api/v1/scopes"),
    ("DELETE", "/api/v1/scopes/test"),
    ("POST", "/api/v1/scopes/test/enable"),
    ("POST", "/api/v1/servers/active/promote"),
    ("POST", "/api/v1/servers/active/demote"),
    ("POST", "/api/v1/voters/tokens"),
    ("POST", "/api/v1/voters/test/approve"),
    ("POST", "/api/v1/voters/test/revoke"),
    ("DELETE", "/api/v1/voters/test"),
    ("POST", "/api/v1/voters/test/rotate-key"),
    ("DELETE", "/api/v1/leases/test/192.168.1.1"),
]

# Public endpoints that should NOT require admin auth
PUBLIC_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/status"),
    ("GET", "/api/v1/scopes"),
    ("GET", "/api/v1/servers"),
    ("GET", "/api/v1/backups"),
    ("GET", "/api/v1/enforcement"),
    ("GET", "/api/v1/leases"),
    ("GET", "/api/v1/voters"),
    ("GET", "/api/v1/voters/pending"),
    ("GET", "/api/v1/voters/tokens"),
]


def _build_app_with_settings(
    admin_api_key: str,
    engine_registry: EngineRegistry,
    module_registry: ModuleRegistry,
    failover_engine: FailoverEngine,
    mock_technitium: AsyncMock,
    mock_pool: TechnitiumPool,
    backup_engine: BackupEngine,
    enforcement_engine: EnforcementEngine,
    voter_registry: VoterRegistryEngine,
) -> create_app:
    """Create an app with specific admin_api_key setting."""
    app = create_app()
    settings = Settings(admin_api_key=admin_api_key)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine_registry] = lambda: engine_registry
    app.dependency_overrides[get_module_registry] = lambda: module_registry
    app.dependency_overrides[get_failover_engine] = lambda: failover_engine
    app.dependency_overrides[get_technitium_client] = lambda: mock_technitium
    app.dependency_overrides[get_technitium_pool] = lambda: mock_pool
    app.dependency_overrides[get_backup_engine] = lambda: backup_engine
    app.dependency_overrides[get_enforcement_engine] = lambda: enforcement_engine
    app.dependency_overrides[get_voter_registry] = lambda: voter_registry
    return app


@pytest.fixture
def voter_keys() -> dict[str, str]:
    return {"voter-1": "k1", "voter-2": "k2", "voter-3": "k3"}


@pytest.fixture
def failover_engine(voter_keys: dict[str, str]) -> FailoverEngine:
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
    mock = AsyncMock(spec=TechnitiumClient)
    mock.name = "technitium"
    mock.version = "1.0.0"
    mock.description = "Mock"
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
    engine = EnforcementEngine(check_interval=60)
    engine.set_backup_engine(backup_engine)
    return engine


@pytest.fixture
def voter_registry(tmp_path: Path) -> VoterRegistryEngine:
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
def engine_registry() -> EngineRegistry:
    return EngineRegistry()


@pytest.fixture
def module_registry() -> ModuleRegistry:
    return ModuleRegistry()


@pytest.fixture
async def admin_client(
    engine_registry: EngineRegistry,
    module_registry: ModuleRegistry,
    failover_engine: FailoverEngine,
    mock_technitium: AsyncMock,
    mock_pool: TechnitiumPool,
    backup_engine: BackupEngine,
    enforcement_engine: EnforcementEngine,
    voter_registry: VoterRegistryEngine,
) -> AsyncGenerator[AsyncClient]:
    """Client with admin_api_key configured."""
    app = _build_app_with_settings(
        ADMIN_KEY,
        engine_registry,
        module_registry,
        failover_engine,
        mock_technitium,
        mock_pool,
        backup_engine,
        enforcement_engine,
        voter_registry,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def nokey_client(
    engine_registry: EngineRegistry,
    module_registry: ModuleRegistry,
    failover_engine: FailoverEngine,
    mock_technitium: AsyncMock,
    mock_pool: TechnitiumPool,
    backup_engine: BackupEngine,
    enforcement_engine: EnforcementEngine,
    voter_registry: VoterRegistryEngine,
) -> AsyncGenerator[AsyncClient]:
    """Client with no admin_api_key configured."""
    app = _build_app_with_settings(
        "",
        engine_registry,
        module_registry,
        failover_engine,
        mock_technitium,
        mock_pool,
        backup_engine,
        enforcement_engine,
        voter_registry,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _request(
    client: AsyncClient,
    method: str,
    url: str,
    **kwargs: object,
) -> object:
    return await client.request(method, url, **kwargs)


@pytest.mark.parametrize(("method", "url"), ADMIN_ENDPOINTS)
async def test_admin_endpoint_returns_503_when_key_not_configured(
    nokey_client: AsyncClient,
    method: str,
    url: str,
) -> None:
    resp = await nokey_client.request(method, url)
    assert resp.status_code == 503
    assert "not configured" in resp.json()["error"]["message"]


@pytest.mark.parametrize(("method", "url"), ADMIN_ENDPOINTS)
async def test_admin_endpoint_rejects_missing_token(
    admin_client: AsyncClient,
    method: str,
    url: str,
) -> None:
    resp = await admin_client.request(method, url)
    assert resp.status_code == 401
    assert "Missing Bearer token" in resp.json()["error"]["message"]


@pytest.mark.parametrize(("method", "url"), ADMIN_ENDPOINTS)
async def test_admin_endpoint_rejects_invalid_token(
    admin_client: AsyncClient,
    method: str,
    url: str,
) -> None:
    resp = await admin_client.request(
        method,
        url,
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401
    assert "Invalid API key" in resp.json()["error"]["message"]


@pytest.mark.parametrize(("method", "url"), ADMIN_ENDPOINTS[:3])
async def test_admin_endpoint_accepts_valid_token(
    admin_client: AsyncClient,
    method: str,
    url: str,
) -> None:
    resp = await admin_client.request(
        method,
        url,
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )
    # Should not be 401 or 503 — may be 4xx/5xx for other reasons
    assert resp.status_code not in (401, 503)


@pytest.mark.parametrize(("method", "url"), PUBLIC_ENDPOINTS)
async def test_public_endpoint_accessible_without_token(
    admin_client: AsyncClient,
    method: str,
    url: str,
) -> None:
    resp = await admin_client.request(method, url)
    assert resp.status_code not in (401, 503)


async def test_admin_endpoint_rejects_non_bearer_scheme(
    admin_client: AsyncClient,
) -> None:
    resp = await admin_client.post(
        "/api/v1/backups",
        headers={"Authorization": f"Basic {ADMIN_KEY}"},
    )
    assert resp.status_code == 401
    assert "Missing Bearer token" in resp.json()["error"]["message"]


async def test_admin_endpoint_rejects_bearer_with_extra_whitespace(
    admin_client: AsyncClient,
) -> None:
    """Bearer token with leading/trailing whitespace should still work."""
    resp = await admin_client.post(
        "/api/v1/backups",
        headers={"Authorization": f"Bearer  {ADMIN_KEY} "},
    )
    # Token is stripped, so valid key with extra spaces should pass
    assert resp.status_code not in (401, 503)
