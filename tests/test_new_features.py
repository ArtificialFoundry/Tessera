"""Tests for multi-server pool, config hot-reload, and voter registration."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from tessera.config import DhcpServer, Settings
from tessera.engines.config_watcher import ConfigWatcherEngine
from tessera.engines.failover import FailoverEngine
from tessera.engines.technitium import TechnitiumClient, TechnitiumPool
from tessera.engines.voter_registry import (
    VoterRegistryEngine,
)
from tessera.exceptions import AuthenticationError, TechnitiumError

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient

# ── Multi-server pool ────────────────────────────────────────────────────────


class TestTechnitiumPool:
    """Tests for TechnitiumPool."""

    @pytest.fixture
    def servers(self) -> list[DhcpServer]:
        return [
            DhcpServer(
                name="dns-1", url="https://dns-1:53443",
                role="active", priority=0,
            ),
            DhcpServer(
                name="dns-2", url="https://dns-2:53443",
                role="candidate", priority=10,
            ),
            DhcpServer(
                name="dns-3", url="https://dns-3:53443",
                role="observer", priority=99,
            ),
        ]

    @pytest.fixture
    def pool(self, servers: list[DhcpServer]) -> TechnitiumPool:
        return TechnitiumPool.from_servers(servers, token="test-token")

    def test_get_active(self, pool: TechnitiumPool) -> None:
        active_srv = pool.get_active()
        assert active_srv.server_name == "dns-1"

    def test_get_candidate(self, pool: TechnitiumPool) -> None:
        candidate = pool.get_candidate()
        assert candidate is not None
        assert candidate.server_name == "dns-2"

    def test_get_candidates(self, pool: TechnitiumPool) -> None:
        candidates = pool.get_candidates()
        assert len(candidates) == 1
        assert candidates[0].server_name == "dns-2"

    def test_get_all(self, pool: TechnitiumPool) -> None:
        assert len(pool.get_all()) == 3

    def test_promote(self, pool: TechnitiumPool) -> None:
        pool.promote("dns-2")
        assert pool.get_role("dns-2") == "active"
        assert pool.get_role("dns-1") == "candidate"

    def test_demote(self, pool: TechnitiumPool) -> None:
        pool.demote("dns-1")
        assert pool.get_role("dns-1") == "candidate"

    def test_promote_observer_fails(self, pool: TechnitiumPool) -> None:
        with pytest.raises(TechnitiumError, match="Cannot promote observer"):
            pool.promote("dns-3")

    def test_promote_unknown_fails(self, pool: TechnitiumPool) -> None:
        with pytest.raises(TechnitiumError, match="Server not found"):
            pool.promote("nonexistent")

    def test_get_server_states(self, pool: TechnitiumPool) -> None:
        states = pool.get_server_states()
        assert len(states) == 3
        names = {s["name"] for s in states}
        assert names == {"dns-1", "dns-2", "dns-3"}

    def test_update_servers_add_remove(self, pool: TechnitiumPool) -> None:
        new_servers = [
            DhcpServer(
                name="dns-1", url="https://dns-1:53443",
                role="active", priority=0,
            ),
            DhcpServer(
                name="dns-4", url="https://dns-4:53443",
                role="candidate", priority=5,
            ),
        ]
        changes = pool.update_servers(new_servers)
        assert any("removed server dns-2" in c for c in changes)
        assert any("removed server dns-3" in c for c in changes)
        assert any("added server dns-4" in c for c in changes)
        assert pool.get_client("dns-4") is not None
        assert pool.get_client("dns-2") is None

    def test_no_active_raises(self) -> None:
        pool = TechnitiumPool(token="x")
        with pytest.raises(TechnitiumError, match="No active"):
            pool.get_active()


# ── Multi-server failover ────────────────────────────────────────────────────


class TestMultiServerFailover:
    """Tests for failover with pool."""

    @pytest.fixture
    def pool_with_mocks(self) -> TechnitiumPool:
        pool = TechnitiumPool(token="test")
        servers = [
            ("p", "active", 0),
            ("s1", "candidate", 10),
            ("s2", "candidate", 20),
        ]
        for name, role, prio in servers:
            mock = AsyncMock(spec=TechnitiumClient)
            mock.server_name = name
            pool._clients[name] = mock
            pool._roles[name] = role
            pool._priorities[name] = prio
        return pool

    @pytest.fixture
    def engine(self, pool_with_mocks: TechnitiumPool) -> FailoverEngine:
        e = FailoverEngine(
            quorum=1, failover_rounds=1, failback_rounds=1,
            voter_keys={"v1": "key1"}, vote_cooldown=0,
        )
        e.set_pool(pool_with_mocks)
        e.set_scope_names(["scope1"])
        return e

    @pytest.mark.asyncio
    async def test_failover_activates_all_standbys(
        self, engine: FailoverEngine, pool_with_mocks: TechnitiumPool
    ) -> None:
        """Failover should enable scopes on ALL standby servers."""

        ts = int(time.time())
        sig = hmac.new(b"key1", f"v1|down|{ts}".encode(), hashlib.sha256).hexdigest()
        engine.submit_vote("v1", "down", ts, sig)
        result = await engine.evaluate_quorum()

        assert result["state"] == "active"
        # After failover: s1 promoted to active, so candidates are p and s2
        p = pool_with_mocks._clients["p"]
        s2 = pool_with_mocks._clients["s2"]
        p.enable_scope.assert_called_with("scope1")
        s2.enable_scope.assert_called_with("scope1")


# ── Config watcher ───────────────────────────────────────────────────────────


class TestConfigWatcher:
    """Tests for ConfigWatcherEngine."""

    @pytest.fixture
    def watcher(self, tmp_path: Path) -> ConfigWatcherEngine:
        keys_file = tmp_path / "voters.json"
        keys_file.write_text('{"v1": "key1"}')
        return ConfigWatcherEngine(
            check_interval=1,
            voter_keys_file=keys_file,
        )

    @pytest.mark.asyncio
    async def test_detects_voter_key_change(
        self, watcher: ConfigWatcherEngine, tmp_path: Path
    ) -> None:
        engine = FailoverEngine(
            quorum=1, failover_rounds=1, failback_rounds=1,
            voter_keys={"v1": "key1"}, vote_cooldown=0,
        )
        watcher.set_failover_engine(engine)

        # Record initial mtime
        path = tmp_path / "voters.json"
        watcher._mtimes[str(path)] = path.stat().st_mtime

        # Modify file
        import asyncio
        await asyncio.sleep(0.05)
        path.write_text('{"v1": "key1", "v2": "key2"}')

        await watcher._check_all_files()
        assert "v2" in engine.config["voters"]


# ── Voter registration ───────────────────────────────────────────────────────


class TestVoterRegistry:
    """Tests for VoterRegistryEngine."""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> VoterRegistryEngine:
        return VoterRegistryEngine(
            voter_keys_file=tmp_path / "voter-keys.json",
            voter_registry_file=tmp_path / "voter-registry.json",
            reg_tokens_file=tmp_path / "reg-tokens.json",
            static_registration_token="static-token",
            auto_approve=False,
            token_ttl=3600,
            psk_grace_period=2,
        )

    @pytest.fixture
    def auto_registry(self, tmp_path: Path) -> VoterRegistryEngine:
        return VoterRegistryEngine(
            voter_keys_file=tmp_path / "voter-keys.json",
            voter_registry_file=tmp_path / "voter-registry.json",
            reg_tokens_file=tmp_path / "reg-tokens.json",
            static_registration_token="",
            auto_approve=True,
            token_ttl=3600,
            psk_grace_period=2,
        )

    # -- Token tests --

    def test_generate_token(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        assert len(token.token) == 64  # 32 bytes hex
        assert token.is_valid()

    def test_token_expiry(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token(ttl=0)
        # Token with 0 TTL expires immediately
        import time
        time.sleep(0.01)
        assert token.is_expired()

    def test_validate_valid_token(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        validated = registry.validate_token(token.token)
        assert validated.is_valid()

    def test_validate_expired_token(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token(ttl=0)
        import time
        time.sleep(0.01)
        with pytest.raises(AuthenticationError, match="expired"):
            registry.validate_token(token.token)

    def test_validate_unknown_token(self, registry: VoterRegistryEngine) -> None:
        with pytest.raises(AuthenticationError, match="Invalid"):
            registry.validate_token("nonexistent-token")

    def test_token_reuse_rejected(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        with pytest.raises(AuthenticationError, match="already used"):
            registry.register_voter("host-2", token.token)

    def test_static_token_never_consumed(self, registry: VoterRegistryEngine) -> None:
        # First use
        registry.register_voter("host-1", "static-token")
        # Static token should still work (pending host-2 is a different voter)
        record, _ = registry.register_voter("host-2", "static-token")
        assert record.status == "pending"

    def test_token_ip_binding(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token(bind_ip="10.0.0.1")
        # Wrong IP
        with pytest.raises(AuthenticationError, match="bound to"):
            registry.validate_token(token.token, source_ip="10.0.0.2")
        # Correct IP
        validated = registry.validate_token(token.token, source_ip="10.0.0.1")
        assert validated.is_valid()

    def test_cleanup_expired_tokens(self, registry: VoterRegistryEngine) -> None:
        registry.generate_token(ttl=0)
        registry.generate_token(ttl=3600)
        import time
        time.sleep(0.01)
        removed = registry.cleanup_expired_tokens()
        assert removed == 1
        assert len(registry.list_tokens()) == 1

    # -- Registration tests --

    def test_register_pending(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        record, psk = registry.register_voter("host-1", token.token)
        assert record.status == "pending"
        assert psk is None

    def test_register_auto_approve(self, auto_registry: VoterRegistryEngine) -> None:
        token = auto_registry.generate_token()
        record, psk = auto_registry.register_voter("host-1", token.token)
        assert record.status == "active"
        assert psk is not None
        assert len(psk) == 64

    def test_approve_pending(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        record, psk = registry.approve_voter("host-1")
        assert record.status == "active"
        assert psk is not None

    def test_revoke_voter(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        registry.approve_voter("host-1")
        record = registry.revoke_voter("host-1")
        assert record.status == "revoked"

    def test_list_pending(self, registry: VoterRegistryEngine) -> None:
        t1 = registry.generate_token()
        t2 = registry.generate_token()
        registry.register_voter("host-1", t1.token)
        registry.register_voter("host-2", t2.token)
        pending = registry.list_pending()
        assert len(pending) == 2

    # -- PSK rotation tests --

    def test_rotate_key(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        _, original_psk = registry.approve_voter("host-1")

        new_psk = registry.rotate_key("host-1")
        assert new_psk != original_psk
        assert len(new_psk) == 64

    def test_grace_period_both_keys_valid(
        self, registry: VoterRegistryEngine
    ) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        _, original_psk = registry.approve_voter("host-1")

        new_psk = registry.rotate_key("host-1")

        # Both keys should be valid during grace period
        psks = registry.get_valid_psks("host-1")
        assert new_psk in psks
        assert original_psk in psks
        assert registry.is_in_grace_period("host-1")

    def test_grace_period_expires(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        _, original_psk = registry.approve_voter("host-1")

        new_psk = registry.rotate_key("host-1")

        # Manually expire the grace period
        registry._grace_keys["host-1"].expires_at = time.time() - 1

        psks = registry.get_valid_psks("host-1")
        assert new_psk in psks
        assert original_psk not in psks
        assert not registry.is_in_grace_period("host-1")

    def test_rotate_revoked_fails(self, registry: VoterRegistryEngine) -> None:
        from tessera.engines.voter_registry import RegistrationError

        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        registry.approve_voter("host-1")
        registry.revoke_voter("host-1")

        with pytest.raises(RegistrationError, match="revoked"):
            registry.rotate_key("host-1")

    # -- Persistence tests --

    @pytest.mark.asyncio
    async def test_persist_and_reload(self, tmp_path: Path) -> None:
        r1 = VoterRegistryEngine(
            voter_keys_file=tmp_path / "keys.json",
            voter_registry_file=tmp_path / "registry.json",
            reg_tokens_file=tmp_path / "tokens.json",
            auto_approve=True,
            token_ttl=3600,
            psk_grace_period=60,
        )
        await r1.start()
        token = r1.generate_token()
        r1.register_voter("host-1", token.token)
        await r1.stop()

        r2 = VoterRegistryEngine(
            voter_keys_file=tmp_path / "keys.json",
            voter_registry_file=tmp_path / "registry.json",
            reg_tokens_file=tmp_path / "tokens.json",
            auto_approve=True,
            token_ttl=3600,
            psk_grace_period=60,
        )
        await r2.start()
        voters = r2.list_voters()
        assert len(voters) == 1
        assert voters[0].name == "host-1"


# ── PSK grace period in failover HMAC ────────────────────────────────────────


class TestFailoverGracePeriod:
    """Tests for HMAC validation with grace period PSKs."""

    @pytest.fixture
    def setup(self, tmp_path: Path) -> tuple[FailoverEngine, VoterRegistryEngine]:
        registry = VoterRegistryEngine(
            voter_keys_file=tmp_path / "keys.json",
            voter_registry_file=tmp_path / "reg.json",
            reg_tokens_file=tmp_path / "tokens.json",
            auto_approve=True,
            token_ttl=3600,
            psk_grace_period=60,
        )
        token = registry.generate_token()
        _, psk = registry.register_voter("voter-1", token.token)
        assert psk is not None

        engine = FailoverEngine(
            quorum=1, failover_rounds=1, failback_rounds=1,
            voter_keys={"voter-1": psk}, vote_cooldown=0,
        )
        engine.set_voter_registry(registry)
        return engine, registry

    def test_vote_with_old_key_during_grace(
        self, setup: tuple[FailoverEngine, VoterRegistryEngine]
    ) -> None:

        engine, registry = setup
        old_psk = registry._load_voter_keys()["voter-1"]

        # Rotate key
        registry.rotate_key("voter-1")
        engine.update_voter_keys(registry._load_voter_keys())

        # Vote with OLD key (should work during grace period)
        ts = int(time.time())
        msg = f"voter-1|up|{ts}".encode()
        sig = hmac.new(
            old_psk.encode(), msg, hashlib.sha256,
        ).hexdigest()
        vote = engine.submit_vote("voter-1", "up", ts, sig)
        assert vote.voter == "voter-1"

    def test_vote_with_new_key(
        self, setup: tuple[FailoverEngine, VoterRegistryEngine]
    ) -> None:

        engine, registry = setup
        new_psk = registry.rotate_key("voter-1")
        engine.update_voter_keys(registry._load_voter_keys())

        ts = int(time.time())
        msg = f"voter-1|up|{ts}".encode()
        sig = hmac.new(
            new_psk.encode(), msg, hashlib.sha256,
        ).hexdigest()
        vote = engine.submit_vote("voter-1", "up", ts, sig)
        assert vote.voter == "voter-1"

    def test_vote_with_old_key_after_grace_fails(
        self, setup: tuple[FailoverEngine, VoterRegistryEngine]
    ) -> None:

        engine, registry = setup
        old_psk = registry._load_voter_keys()["voter-1"]

        registry.rotate_key("voter-1")
        engine.update_voter_keys(registry._load_voter_keys())

        # Expire grace period
        registry._grace_keys["voter-1"].expires_at = time.time() - 1

        ts = int(time.time())
        msg = f"voter-1|up|{ts}".encode()
        sig = hmac.new(
            old_psk.encode(), msg, hashlib.sha256,
        ).hexdigest()
        with pytest.raises(AuthenticationError, match="Invalid signature"):
            engine.submit_vote("voter-1", "up", ts, sig)


# ── API endpoint tests ──────────────────────────────────────────────────────


class TestServersAPI:
    """Tests for /api/v1/servers endpoints."""

    @pytest.mark.asyncio
    async def test_list_servers(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/servers")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data

    @pytest.mark.asyncio
    async def test_promote_server(
        self, client: AsyncClient, mock_pool: TechnitiumPool
    ) -> None:
        # Add a standby first
        mock_candidate = AsyncMock(spec=TechnitiumClient)
        mock_candidate.server_name = "candidate"
        mock_candidate._base_url = "https://candidate:53443"
        mock_pool._clients["candidate"] = mock_candidate
        mock_pool._roles["candidate"] = "candidate"
        mock_pool._priorities["candidate"] = 10

        resp = await client.post("/api/v1/servers/candidate/promote")
        assert resp.status_code == 200
        assert resp.json()["new_role"] == "active"

    @pytest.mark.asyncio
    async def test_promote_unknown_server(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/servers/nonexistent/promote")
        assert resp.status_code == 400


class TestVotersAPI:
    """Tests for /api/v1/voters endpoints."""

    @pytest.mark.asyncio
    async def test_generate_token(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert len(data["token"]) == 64

    @pytest.mark.asyncio
    async def test_generate_token_with_bind_ip(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/voters/tokens", json={"bind_ip": "10.0.0.1"}
        )
        assert resp.status_code == 200
        assert resp.json()["bind_ip"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_register_voter(self, client: AsyncClient) -> None:
        # Generate token
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]

        # Register
        resp = await client.post(
            "/api/v1/voters/register",
            json={"name": "test-host", "token": token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["voter_name"] == "test-host"
        assert data["status"] == "pending"
        assert data["psk"] is None

    @pytest.mark.asyncio
    async def test_register_with_static_token(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/voters/register",
            json={"name": "static-host", "token": "static-test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    @pytest.mark.asyncio
    async def test_register_invalid_token(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/voters/register",
            json={"name": "bad-host", "token": "invalid-token"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_approve_voter(self, client: AsyncClient) -> None:
        # Register
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]
        await client.post(
            "/api/v1/voters/register",
            json={"name": "approve-host", "token": token},
        )

        # Approve
        resp = await client.post("/api/v1/voters/approve-host/approve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert "psk" in data
        assert len(data["psk"]) == 64

    @pytest.mark.asyncio
    async def test_revoke_voter(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]
        await client.post(
            "/api/v1/voters/register",
            json={"name": "revoke-host", "token": token},
        )
        await client.post("/api/v1/voters/revoke-host/approve")

        resp = await client.post("/api/v1/voters/revoke-host/revoke")
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_delete_voter(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]
        await client.post(
            "/api/v1/voters/register",
            json={"name": "del-host", "token": token},
        )
        await client.post("/api/v1/voters/del-host/approve")

        resp = await client.delete("/api/v1/voters/del-host")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_rotate_key(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]
        await client.post(
            "/api/v1/voters/register",
            json={"name": "rotate-host", "token": token},
        )
        await client.post("/api/v1/voters/rotate-host/approve")

        resp = await client.post("/api/v1/voters/rotate-host/rotate-key")
        assert resp.status_code == 200
        data = resp.json()
        assert "new_psk" in data
        assert data["grace_period"] > 0

    @pytest.mark.asyncio
    async def test_list_voters(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/voters")
        assert resp.status_code == 200
        assert "voters" in resp.json()

    @pytest.mark.asyncio
    async def test_list_pending(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/voters/pending")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_tokens(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/voters/tokens")
        assert resp.status_code == 200
        assert "tokens" in resp.json()


# ── Config backward compatibility ────────────────────────────────────────────


class TestConfigBackwardCompat:
    """Tests for DhcpServer config backward compatibility."""

    def test_legacy_urls(self) -> None:
        """Legacy primary_url/standby_url should auto-create server list."""
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            settings = Settings(
                primary_url="https://dns-1:53443",
                standby_url="https://dns-2:53443",
                servers="",
            )
            servers = settings.get_servers()
        assert len(servers) == 2
        assert servers[0].role == "active"
        assert servers[1].role == "candidate"

    def test_servers_json_string(self) -> None:
        servers_json = json.dumps([
            {"name": "a", "url": "https://a:53443", "role": "active"},
            {"name": "b", "url": "https://b:53443", "role": "candidate"},
        ])
        settings = Settings(servers=servers_json)
        servers = settings.get_servers()
        assert len(servers) == 2
        assert servers[0].name == "a"

    def test_servers_file(self, tmp_path: Path) -> None:
        f = tmp_path / "servers.json"
        f.write_text(json.dumps([
            {"name": "x", "url": "https://x:53443", "role": "active", "priority": 0},
        ]))
        settings = Settings(servers_file=f, servers="")
        servers = settings.get_servers()
        assert len(servers) == 1
        assert servers[0].name == "x"
