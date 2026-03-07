"""Tests for TechnitiumPool, server roles, promotion/demotion, and failover."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from tessera.config import DhcpServer, Settings
from tessera.engines.failover import FailoverEngine
from tessera.engines.technitium import TechnitiumClient, TechnitiumPool
from tessera.exceptions import TechnitiumError

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient


class TestPoolActiveRetrieval:
    """TechnitiumPool returns the correct active server."""

    @pytest.fixture
    def servers(self) -> list[DhcpServer]:
        return [
            DhcpServer(
                name="dns-1", url="https://dns-1:53443", role="active", priority=0
            ),
            DhcpServer(
                name="dns-2", url="https://dns-2:53443", role="candidate", priority=10
            ),
            DhcpServer(
                name="dns-3", url="https://dns-3:53443", role="observer", priority=99
            ),
        ]

    @pytest.fixture
    def pool(self, servers: list[DhcpServer]) -> TechnitiumPool:
        return TechnitiumPool.from_servers(servers, token="test-token")

    def test_active_server_is_dns1(self, pool: TechnitiumPool) -> None:
        assert pool.get_active().server_name == "dns-1"

    def test_candidate_server_is_dns2(self, pool: TechnitiumPool) -> None:
        candidate = pool.get_candidate()
        assert candidate is not None
        assert candidate.server_name == "dns-2"

    def test_candidates_list_contains_only_dns2(self, pool: TechnitiumPool) -> None:
        candidates = pool.get_candidates()
        assert len(candidates) == 1
        assert candidates[0].server_name == "dns-2"

    def test_all_servers_returned(self, pool: TechnitiumPool) -> None:
        assert len(pool.get_all()) == 3

    async def test_server_states_include_all_names(self, pool: TechnitiumPool) -> None:
        states = await pool.get_server_states()
        assert len(states) == 3
        assert {s["name"] for s in states} == {"dns-1", "dns-2", "dns-3"}


class TestPoolPromotion:
    """TechnitiumPool promotion and demotion logic."""

    @pytest.fixture
    def servers(self) -> list[DhcpServer]:
        return [
            DhcpServer(
                name="dns-1", url="https://dns-1:53443", role="active", priority=0
            ),
            DhcpServer(
                name="dns-2", url="https://dns-2:53443", role="candidate", priority=10
            ),
            DhcpServer(
                name="dns-3", url="https://dns-3:53443", role="observer", priority=99
            ),
        ]

    @pytest.fixture
    def pool(self, servers: list[DhcpServer]) -> TechnitiumPool:
        return TechnitiumPool.from_servers(servers, token="test-token")

    def test_promote_candidate_becomes_active(self, pool: TechnitiumPool) -> None:
        pool.promote("dns-2")
        assert pool.get_role("dns-2") == "active"
        assert pool.get_role("dns-1") == "candidate"

    def test_demote_active_becomes_candidate(self, pool: TechnitiumPool) -> None:
        pool.demote("dns-1")
        assert pool.get_role("dns-1") == "candidate"

    def test_promote_observer_raises_error(self, pool: TechnitiumPool) -> None:
        with pytest.raises(TechnitiumError, match="Cannot promote observer"):
            pool.promote("dns-3")

    def test_promote_unknown_server_raises_error(self, pool: TechnitiumPool) -> None:
        with pytest.raises(TechnitiumError, match="Server not found"):
            pool.promote("nonexistent")

    def test_empty_pool_raises_on_get_active(self) -> None:
        pool = TechnitiumPool(token="x")
        with pytest.raises(TechnitiumError, match="No active"):
            pool.get_active()


class TestPoolServerUpdates:
    """TechnitiumPool add/remove server updates."""

    @pytest.fixture
    def pool(self) -> TechnitiumPool:
        servers = [
            DhcpServer(
                name="dns-1", url="https://dns-1:53443", role="active", priority=0
            ),
            DhcpServer(
                name="dns-2", url="https://dns-2:53443", role="candidate", priority=10
            ),
            DhcpServer(
                name="dns-3", url="https://dns-3:53443", role="observer", priority=99
            ),
        ]
        return TechnitiumPool.from_servers(servers, token="test-token")

    def test_update_removes_old_and_adds_new_servers(
        self, pool: TechnitiumPool
    ) -> None:
        new_servers = [
            DhcpServer(
                name="dns-1", url="https://dns-1:53443", role="active", priority=0
            ),
            DhcpServer(
                name="dns-4", url="https://dns-4:53443", role="candidate", priority=5
            ),
        ]
        changes = pool.update_servers(new_servers)
        assert any("removed server dns-2" in c for c in changes)
        assert any("removed server dns-3" in c for c in changes)
        assert any("added server dns-4" in c for c in changes)
        assert pool.get_client("dns-4") is not None
        assert pool.get_client("dns-2") is None


class TestMultiServerFailover:
    """Failover enables scopes on all standby servers."""

    @pytest.fixture
    def pool_with_mocks(self) -> TechnitiumPool:
        pool = TechnitiumPool(token="test")
        servers = [("p", "active", 0), ("s1", "candidate", 10), ("s2", "candidate", 20)]
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
            quorum=1,
            failover_rounds=1,
            failback_rounds=1,
            voter_keys={"v1": "key1"},
            vote_cooldown=0,
        )
        e.set_pool(pool_with_mocks)
        e.set_scope_names(["scope1"])
        return e

    @pytest.mark.asyncio
    async def test_failover_enables_scopes_on_all_standbys(
        self,
        engine: FailoverEngine,
        pool_with_mocks: TechnitiumPool,
    ) -> None:
        ts = int(time.time())
        sig = hmac.new(b"key1", f"v1|down|{ts}".encode(), hashlib.sha256).hexdigest()
        engine.submit_vote("v1", "down", ts, sig)
        result = await engine.evaluate_quorum()

        assert result["state"] == "active"
        p = pool_with_mocks._clients["p"]
        s2 = pool_with_mocks._clients["s2"]
        p.enable_scope.assert_called_with("scope1")
        s2.enable_scope.assert_called_with("scope1")


class TestServersAPI:
    """Tests for /api/v1/servers endpoints."""

    @pytest.mark.asyncio
    async def test_list_servers_returns_server_list(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/servers")
        assert resp.status_code == 200
        assert "servers" in resp.json()

    @pytest.mark.asyncio
    async def test_promote_candidate_via_api(
        self,
        client: AsyncClient,
        mock_pool: TechnitiumPool,
    ) -> None:
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
    async def test_promote_unknown_server_returns_502(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post("/api/v1/servers/nonexistent/promote")
        assert resp.status_code == 502


class TestConfigBackwardCompat:
    """DhcpServer config backward compatibility with legacy URL fields."""

    def test_legacy_urls_create_active_and_candidate(self) -> None:
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

    def test_servers_json_string_parsed_correctly(self) -> None:
        servers_json = json.dumps(
            [
                {"name": "a", "url": "https://a:53443", "role": "active"},
                {"name": "b", "url": "https://b:53443", "role": "candidate"},
            ]
        )
        settings = Settings(servers=servers_json)
        servers = settings.get_servers()
        assert len(servers) == 2
        assert servers[0].name == "a"

    def test_servers_loaded_from_file(self, tmp_path: Path) -> None:
        f = tmp_path / "servers.json"
        f.write_text(
            json.dumps(
                [
                    {
                        "name": "x",
                        "url": "https://x:53443",
                        "role": "active",
                        "priority": 0,
                    },
                ]
            )
        )
        settings = Settings(servers_file=f, servers="")
        servers = settings.get_servers()
        assert len(servers) == 1
        assert servers[0].name == "x"
