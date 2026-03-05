"""Tests for failover API endpoints."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from httpx import AsyncClient


def _sign(voter: str, status: str, ts: int, psk: str) -> str:
    """Compute vote HMAC."""
    message = f"{voter}|{status}|{ts}"
    return hmac.new(psk.encode(), message.encode(), hashlib.sha256).hexdigest()


class TestVoteEndpoint:
    """Tests for POST /api/v1/vote."""

    async def test_valid_vote(
        self,
        client: AsyncClient,
        voter_keys: dict[str, str],
    ) -> None:
        """Valid vote returns accepted."""
        ts = int(time.time())
        sig = _sign("voter-1", "up", ts, voter_keys["voter-1"])
        resp = await client.post(
            "/api/v1/vote",
            json={
                "voter": "voter-1",
                "status": "up",
                "timestamp": ts,
                "signature": sig,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] is True

    async def test_invalid_signature_returns_401(self, client: AsyncClient) -> None:
        """Bad signature returns 401."""
        ts = int(time.time())
        resp = await client.post(
            "/api/v1/vote",
            json={
                "voter": "voter-1",
                "status": "up",
                "timestamp": ts,
                "signature": "bad",
            },
        )
        assert resp.status_code == 401

    async def test_unknown_voter_returns_401(self, client: AsyncClient) -> None:
        """Unknown voter returns 401."""
        ts = int(time.time())
        resp = await client.post(
            "/api/v1/vote",
            json={
                "voter": "unknown",
                "status": "up",
                "timestamp": ts,
                "signature": "x",
            },
        )
        assert resp.status_code == 401


class TestStatusEndpoint:
    """Tests for GET /api/v1/status."""

    async def test_status_returns_state(self, client: AsyncClient) -> None:
        """Status endpoint returns failover state."""
        resp = await client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "standby"
        assert "config" in data
