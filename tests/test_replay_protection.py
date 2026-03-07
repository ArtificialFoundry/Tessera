"""Tests for vote replay protection (v2 nonce-based signatures)."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from tessera.engines.failover import (
    FailoverEngine,
    verify_vote_signature_v2,
)
from tessera.exceptions import AuthenticationError

VOTER = "test-voter"
PSK = "deadbeef" * 8


def _sign_v1(voter: str, status: str, ts: int, psk: str) -> str:
    msg = f"{voter}|{status}|{ts}"
    return hmac.new(psk.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _sign_v2(voter: str, status: str, ts: int, nonce: str, psk: str) -> str:
    msg = f"{voter}|{status}|{ts}|{nonce}"
    return hmac.new(psk.encode(), msg.encode(), hashlib.sha256).hexdigest()


class TestV2Signature:
    """Verify v2 signature function."""

    def test_valid_signature(self) -> None:
        ts = int(time.time())
        sig = _sign_v2(VOTER, "up", ts, "nonce1", PSK)
        assert verify_vote_signature_v2(VOTER, "up", ts, "nonce1", sig, PSK)

    def test_invalid_signature(self) -> None:
        ts = int(time.time())
        sig = _sign_v2(VOTER, "up", ts, "nonce1", PSK)
        assert not verify_vote_signature_v2(VOTER, "up", ts, "nonce2", sig, PSK)

    def test_v1_sig_fails_v2(self) -> None:
        ts = int(time.time())
        sig = _sign_v1(VOTER, "up", ts, PSK)
        assert not verify_vote_signature_v2(VOTER, "up", ts, "nonce1", sig, PSK)


class TestReplayProtection:
    """FailoverEngine rejects replayed nonces."""

    @pytest.fixture
    def engine(self) -> FailoverEngine:
        return FailoverEngine(
            quorum=1,
            voter_keys={VOTER: PSK},
            vote_cooldown=0,
        )

    def test_v2_vote_accepted(self, engine: FailoverEngine) -> None:
        ts = int(time.time())
        nonce = "unique-1"
        sig = _sign_v2(VOTER, "up", ts, nonce, PSK)
        vote = engine.submit_vote(VOTER, "up", ts, sig, nonce=nonce)
        assert vote.voter == VOTER

    def test_replay_rejected(self, engine: FailoverEngine) -> None:
        ts = int(time.time())
        nonce = "replay-me"
        sig = _sign_v2(VOTER, "up", ts, nonce, PSK)
        engine.submit_vote(VOTER, "up", ts, sig, nonce=nonce)

        # Same nonce again → replay
        ts2 = int(time.time())
        sig2 = _sign_v2(VOTER, "up", ts2, nonce, PSK)
        with pytest.raises(AuthenticationError, match="replay"):
            engine.submit_vote(VOTER, "up", ts2, sig2, nonce=nonce)

    def test_different_nonce_accepted(self, engine: FailoverEngine) -> None:
        ts = int(time.time())
        sig1 = _sign_v2(VOTER, "up", ts, "nonce-a", PSK)
        engine.submit_vote(VOTER, "up", ts, sig1, nonce="nonce-a")

        ts2 = int(time.time())
        sig2 = _sign_v2(VOTER, "up", ts2, "nonce-b", PSK)
        vote = engine.submit_vote(VOTER, "up", ts2, sig2, nonce="nonce-b")
        assert vote.voter == VOTER

    def test_v1_still_works_without_nonce(self, engine: FailoverEngine) -> None:
        ts = int(time.time())
        sig = _sign_v1(VOTER, "up", ts, PSK)
        vote = engine.submit_vote(VOTER, "up", ts, sig)
        assert vote.voter == VOTER
