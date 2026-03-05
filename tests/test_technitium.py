"""Tests for the Technitium client engine."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tessera.engines.technitium import TechnitiumClient
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
