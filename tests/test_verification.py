"""Tests for server-side vote verification and conflict detection."""

from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import AsyncMock

import pytest

from tessera.engines.failover import (
    FailoverEngine,
    FailoverState,
    VoteVerification,
)


def _sign(voter: str, status: str, ts: int, psk: str) -> str:
    message = f"{voter}|{status}|{ts}"
    return hmac.new(psk.encode(), message.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def voter_keys_v() -> dict[str, str]:
    return {
        "voter-1": "test-key-ca1",
        "voter-2": "test-key-frontend1",
        "voter-3": "test-key-apps1",
    }


@pytest.fixture
def verified_engine(voter_keys_v: dict[str, str]) -> FailoverEngine:
    """Failover engine with a mock primary client."""
    return FailoverEngine(
        quorum=2,
        failover_rounds=2,
        failback_rounds=2,
        vote_ttl=90,
        voter_keys=voter_keys_v,
        vote_cooldown=0,
    )


class TestVoteVerification:
    """Test server-side vote cross-validation."""

    async def test_verified_when_both_agree_up(
        self, verified_engine: FailoverEngine, voter_keys_v: dict[str, str]
    ) -> None:
        """Vote is VERIFIED when voter says up and primary is healthy."""
        mock_client = AsyncMock()
        mock_client.list_scopes = AsyncMock(
            return_value=[{"name": "default", "enabled": True}]
        )
        verified_engine.set_primary_client(mock_client)

        ts = int(time.time())
        for voter in ["voter-1", "voter-2"]:
            sig = _sign(voter, "up", ts, voter_keys_v[voter])
            verified_engine.submit_vote(voter, "up", ts, sig)

        result = await verified_engine.evaluate_quorum()
        assert result["up_count"] == 2
        assert result["conflict_count"] == 0

        for vote in verified_engine.votes.values():
            assert vote.verification == VoteVerification.VERIFIED

    async def test_verified_when_both_agree_down(
        self, verified_engine: FailoverEngine, voter_keys_v: dict[str, str]
    ) -> None:
        """Vote is VERIFIED when voter says down and primary is unhealthy."""
        mock_client = AsyncMock()
        mock_client.list_scopes = AsyncMock(return_value=[])
        verified_engine.set_primary_client(mock_client)

        ts = int(time.time())
        for voter in ["voter-1", "voter-2"]:
            sig = _sign(voter, "down", ts, voter_keys_v[voter])
            verified_engine.submit_vote(voter, "down", ts, sig)

        result = await verified_engine.evaluate_quorum()
        assert result["down_count"] == 2
        assert result["conflict_count"] == 0

        for vote in verified_engine.votes.values():
            assert vote.verification == VoteVerification.VERIFIED

    async def test_conflict_voter_down_primary_healthy(
        self, verified_engine: FailoverEngine, voter_keys_v: dict[str, str]
    ) -> None:
        """CONFLICT when voter says down but primary is healthy."""
        mock_client = AsyncMock()
        mock_client.list_scopes = AsyncMock(
            return_value=[{"name": "default", "enabled": True}]
        )
        verified_engine.set_primary_client(mock_client)

        ts = int(time.time())
        for voter in ["voter-1", "voter-2"]:
            sig = _sign(voter, "down", ts, voter_keys_v[voter])
            verified_engine.submit_vote(voter, "down", ts, sig)

        result = await verified_engine.evaluate_quorum()
        assert result["conflict_count"] == 2
        assert result["down_count"] == 0
        assert not result["has_quorum"]

        for vote in verified_engine.votes.values():
            assert vote.verification == VoteVerification.CONFLICT

    async def test_conflict_voter_up_primary_unhealthy(
        self, verified_engine: FailoverEngine, voter_keys_v: dict[str, str]
    ) -> None:
        """CONFLICT when voter says up but primary is unreachable/unhealthy."""
        mock_client = AsyncMock()
        mock_client.list_scopes = AsyncMock(return_value=[])
        verified_engine.set_primary_client(mock_client)

        ts = int(time.time())
        for voter in ["voter-1", "voter-2"]:
            sig = _sign(voter, "up", ts, voter_keys_v[voter])
            verified_engine.submit_vote(voter, "up", ts, sig)

        result = await verified_engine.evaluate_quorum()
        assert result["conflict_count"] == 2
        assert result["up_count"] == 0
        assert not result["has_quorum"]

    async def test_unverified_when_primary_unreachable(
        self, verified_engine: FailoverEngine, voter_keys_v: dict[str, str]
    ) -> None:
        """Votes are UNVERIFIED (but counted) when primary times out."""
        mock_client = AsyncMock()
        mock_client.list_scopes = AsyncMock(side_effect=TimeoutError("timeout"))
        verified_engine.set_primary_client(mock_client)

        ts = int(time.time())
        for voter in ["voter-1", "voter-2"]:
            sig = _sign(voter, "down", ts, voter_keys_v[voter])
            verified_engine.submit_vote(voter, "down", ts, sig)

        result = await verified_engine.evaluate_quorum()
        assert result["down_count"] == 2
        assert result["conflict_count"] == 0
        assert result["has_quorum"]

        for vote in verified_engine.votes.values():
            assert vote.verification == VoteVerification.UNVERIFIED

    async def test_conflict_votes_excluded_from_quorum(
        self, verified_engine: FailoverEngine, voter_keys_v: dict[str, str]
    ) -> None:
        """CONFLICT votes don't prevent failover even with enough total votes."""
        mock_client = AsyncMock()
        mock_client.list_scopes = AsyncMock(
            return_value=[{"name": "default", "enabled": True}]
        )
        verified_engine.set_primary_client(mock_client)

        ts = int(time.time())
        # All voters say "down" but primary is healthy → all CONFLICT
        for voter in ["voter-1", "voter-2", "voter-3"]:
            sig = _sign(voter, "down", ts, voter_keys_v[voter])
            verified_engine.submit_vote(voter, "down", ts, sig)

        result = await verified_engine.evaluate_quorum()
        assert result["conflict_count"] == 3
        assert not result["has_quorum"]
        # Should NOT transition despite 3 "down" votes
        assert verified_engine.state == FailoverState.STANDBY

    async def test_no_primary_client_all_unverified(
        self, verified_engine: FailoverEngine, voter_keys_v: dict[str, str]
    ) -> None:
        """Without primary client, all votes are UNVERIFIED and counted."""
        # Don't set primary client
        ts = int(time.time())
        for voter in ["voter-1", "voter-2"]:
            sig = _sign(voter, "down", ts, voter_keys_v[voter])
            verified_engine.submit_vote(voter, "down", ts, sig)

        result = await verified_engine.evaluate_quorum()
        assert result["down_count"] == 2
        assert result["has_quorum"]

        for vote in verified_engine.votes.values():
            assert vote.verification == VoteVerification.UNVERIFIED
