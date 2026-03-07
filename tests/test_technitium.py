"""Tests for the Technitium client engine."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tessera.engines.technitium import (
    DhcpClientProtocol,
    TechnitiumClient,
    TechnitiumPool,
)
from tessera.exceptions import TechnitiumError


@pytest.fixture
def technitium_client() -> TechnitiumClient:
    """Create a TechnitiumClient with test config."""
    return TechnitiumClient(base_url="https://test:53443", token="test-token")


class TestTechnitiumClient:
    """Tests for TechnitiumClient."""

    def test_name_and_version(self, technitium_client: TechnitiumClient) -> None:
        """Client has correct engine metadata."""
        assert technitium_client.name == "technitium"
        assert technitium_client.version == "1.0.0"

    def test_client_not_started_raises(
        self, technitium_client: TechnitiumClient
    ) -> None:
        """Accessing client before start raises."""
        with pytest.raises(TechnitiumError, match="not started"):
            _ = technitium_client.client

    async def test_start_creates_client(
        self, technitium_client: TechnitiumClient
    ) -> None:
        """Start initializes the HTTP client."""
        await technitium_client.start()
        try:
            assert technitium_client.client is not None
        finally:
            await technitium_client.stop()

    async def test_stop_closes_client(
        self, technitium_client: TechnitiumClient
    ) -> None:
        """Stop closes the HTTP client."""
        await technitium_client.start()
        await technitium_client.stop()
        with pytest.raises(TechnitiumError, match="not started"):
            _ = technitium_client.client

    async def test_list_scopes_parses_response(
        self, technitium_client: TechnitiumClient
    ) -> None:
        """list_scopes returns scope list from response."""
        mock_resp: dict[str, Any] = {
            "status": "ok",
            "response": {
                "scopes": [
                    {"name": "LAN", "enabled": True},
                    {"name": "IoT", "enabled": False},
                ]
            },
        }
        await technitium_client.start()
        try:
            with patch.object(
                technitium_client, "_request", new_callable=AsyncMock
            ) as mock_req:
                mock_req.return_value = mock_resp
                scopes = await technitium_client.list_scopes()
                assert len(scopes) == 2
                assert scopes[0]["name"] == "LAN"
        finally:
            await technitium_client.stop()

    async def test_get_leases_parses_response(
        self, technitium_client: TechnitiumClient
    ) -> None:
        """get_leases returns lease list."""
        mock_resp: dict[str, Any] = {
            "status": "ok",
            "response": {"leases": [{"address": "192.168.1.10", "type": "Dynamic"}]},
        }
        await technitium_client.start()
        try:
            with patch.object(
                technitium_client, "_request", new_callable=AsyncMock
            ) as mock_req:
                mock_req.return_value = mock_resp
                leases = await technitium_client.get_leases("LAN")
                assert len(leases) == 1
        finally:
            await technitium_client.stop()


if TYPE_CHECKING:
    from pathlib import Path


class TestDhcpClientProtocol:
    """TechnitiumClient satisfies the typed DHCP protocol."""

    def test_client_implements_dhcp_protocol(self) -> None:
        """TechnitiumClient satisfies DhcpClientProtocol."""
        client = TechnitiumClient(
            base_url="https://test",
            token="tok",
        )
        assert isinstance(client, DhcpClientProtocol)


class TestPoolRolePersistence:
    """Persisting pool role changes to servers file."""

    def test_promote_writes_updated_roles_to_servers_file(self, tmp_path: Path) -> None:
        """Promote writes roles to servers file."""
        sf = tmp_path / "servers.json"
        pool = TechnitiumPool(token="tok", servers_file=sf)
        c1 = TechnitiumClient(
            base_url="https://s1",
            token="tok",
            server_name="s1",
        )
        c2 = TechnitiumClient(
            base_url="https://s2",
            token="tok",
            server_name="s2",
        )
        pool._clients = {"s1": c1, "s2": c2}
        pool._roles = {"s1": "active", "s2": "candidate"}
        pool._priorities = {"s1": 0, "s2": 1}
        pool.promote("s2")
        assert sf.is_file()
        data = json.loads(sf.read_text())
        roles = {s["name"]: s["role"] for s in data}
        assert roles["s2"] == "active"
        assert roles["s1"] == "candidate"

    def test_demote_writes_updated_role_to_servers_file(self, tmp_path: Path) -> None:
        """Demote writes roles to servers file."""
        sf = tmp_path / "servers.json"
        pool = TechnitiumPool(token="tok", servers_file=sf)
        c1 = TechnitiumClient(
            base_url="https://s1",
            token="tok",
            server_name="s1",
        )
        pool._clients = {"s1": c1}
        pool._roles = {"s1": "active"}
        pool._priorities = {"s1": 0}
        pool.demote("s1")
        data = json.loads(sf.read_text())
        assert data[0]["role"] == "candidate"


class TestTlsVerificationWarning:
    """TLS disabled warning logged once per URL."""

    @pytest.mark.asyncio
    async def test_no_warning_when_tls_enabled_by_default(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        TechnitiumClient._tls_warned_urls.clear()
        c = TechnitiumClient("https://tls-test:53443", "tok")
        with caplog.at_level(logging.WARNING):
            await c.start()
        assert not any("TLS verification" in r.message for r in caplog.records)
        await c.stop()

    @pytest.mark.asyncio
    async def test_warns_when_skip_tls_verify(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        TechnitiumClient._tls_warned_urls.clear()
        c = TechnitiumClient("https://tls-skip:53443", "tok", skip_tls_verify=True)
        with caplog.at_level(logging.ERROR):
            await c.start()
        assert any("TLS verification DISABLED" in r.message for r in caplog.records)
        await c.stop()

    @pytest.mark.asyncio
    async def test_duplicate_url_does_not_warn_twice(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        TechnitiumClient._tls_warned_urls.clear()
        url = "https://tls-dedup:53443"
        c1 = TechnitiumClient(url, "tok", skip_tls_verify=True)
        c2 = TechnitiumClient(url, "tok", skip_tls_verify=True)
        with caplog.at_level(logging.ERROR):
            await c1.start()
            await c2.start()
        tls = [r for r in caplog.records if "TLS verification DISABLED" in r.message]
        assert len(tls) == 1
        await c1.stop()
        await c2.stop()


class TestHttpxRetryLogic:
    """Retry transient errors with exponential backoff."""

    @pytest.mark.asyncio
    async def test_retries_on_connect_error_then_succeeds(self) -> None:
        TechnitiumClient._tls_warned_urls.clear()
        c = TechnitiumClient("https://retry-1:53443", "tok")
        await c.start()

        call_count = 0

        async def _mock_get(*_a: object, **_kw: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("refused")
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"status": "ok"}
            resp.raise_for_status = MagicMock()
            return resp

        with patch.object(c.client, "get", side_effect=_mock_get):
            result = await c._request("GET", "/api/test")
        assert call_count == 3
        assert result["status"] == "ok"
        await c.stop()

    @pytest.mark.asyncio
    async def test_raises_after_max_retries_exhausted(self) -> None:
        TechnitiumClient._tls_warned_urls.clear()
        c = TechnitiumClient("https://retry-2:53443", "tok")
        await c.start()

        with (
            patch.object(
                c.client,
                "get",
                side_effect=httpx.ConnectError("refused"),
            ),
            pytest.raises(TechnitiumError),
        ):
            await c._request("GET", "/api/test")
        await c.stop()

    @pytest.mark.asyncio
    async def test_retries_on_502_then_succeeds(self) -> None:
        TechnitiumClient._tls_warned_urls.clear()
        c = TechnitiumClient("https://retry-3:53443", "tok")
        await c.start()

        call_count = 0

        async def _mock_get(*_a: object, **_kw: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count < 3:
                resp.status_code = 502
                resp.text = "Bad Gateway"
                resp.request = MagicMock()
                resp.raise_for_status = MagicMock(
                    side_effect=httpx.HTTPStatusError(
                        "502",
                        request=resp.request,
                        response=resp,
                    )
                )
            else:
                resp.status_code = 200
                resp.json.return_value = {"status": "ok"}
                resp.raise_for_status = MagicMock()
            return resp

        with patch.object(c.client, "get", side_effect=_mock_get):
            await c._request("GET", "/api/test")
        assert call_count == 3
        await c.stop()
