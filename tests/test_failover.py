"""Tests for the failover engine state machine and HMAC auth."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from tessera.engines.failover import (
    FailoverEngine,
    FailoverState,
    verify_vote_signature,
)
from tessera.exceptions import AuthenticationError


def _sign(voter: str, status: str, ts: int, psk: str) -> str:
    """Helper to compute HMAC signature."""
    message = f"{voter}|{status}|{ts}"
    return hmac.new(psk.encode(), message.encode(), hashlib.sha256).hexdigest()


class TestVerifyVoteSignature:
    """Test HMAC signature verification."""

    def test_valid_signature(self) -> None:
        """Valid HMAC passes verification."""
        sig = _sign("voter-1", "up", 1000, "secret")
        assert verify_vote_signature("voter-1", "up", 1000, sig, "secret")

    def test_invalid_signature(self) -> None:
        """Bad signature is rejected."""
        assert not verify_vote_signature("voter-1", "up", 1000, "bad", "secret")

    def test_wrong_psk(self) -> None:
        """Wrong PSK produces different signature."""
        sig = _sign("voter-1", "up", 1000, "secret")
        assert not verify_vote_signature("voter-1", "up", 1000, sig, "wrong")


class TestFailoverEngine:
    """Test failover state machine transitions."""

    def test_initial_state_is_standby(self, failover_engine: FailoverEngine) -> None:
        """Engine starts in standby."""
        assert failover_engine.state == FailoverState.STANDBY

    def test_submit_vote_valid(
        self, failover_engine: FailoverEngine, voter_keys: dict[str, str]
    ) -> None:
        """Valid vote is accepted."""
        ts = int(time.time())
        sig = _sign("voter-1", "up", ts, voter_keys["voter-1"])
        vote = failover_engine.submit_vote("voter-1", "up", ts, sig)
        assert vote.voter == "voter-1"
        assert vote.status.value == "up"

    def test_submit_vote_unknown_voter(self, failover_engine: FailoverEngine) -> None:
        """Unknown voter is rejected."""
        ts = int(time.time())
        with pytest.raises(AuthenticationError, match="Unknown voter"):
            failover_engine.submit_vote("unknown", "up", ts, "sig")

    def test_submit_vote_stale_timestamp(
        self, failover_engine: FailoverEngine, voter_keys: dict[str, str]
    ) -> None:
        """Stale timestamp is rejected."""
        ts = int(time.time()) - 120
        sig = _sign("voter-1", "up", ts, voter_keys["voter-1"])
        with pytest.raises(AuthenticationError, match="Timestamp"):
            failover_engine.submit_vote("voter-1", "up", ts, sig)

    def test_submit_vote_bad_signature(self, failover_engine: FailoverEngine) -> None:
        """Bad HMAC is rejected."""
        ts = int(time.time())
        with pytest.raises(AuthenticationError, match="Invalid signature"):
            failover_engine.submit_vote("voter-1", "up", ts, "badsig")

    async def test_failover_transition(
        self, failover_engine: FailoverEngine, voter_keys: dict[str, str]
    ) -> None:
        """Enough down rounds triggers failover."""
        for _ in range(2):
            ts = int(time.time())
            for voter in ["voter-1", "voter-2"]:
                sig = _sign(voter, "down", ts, voter_keys[voter])
                failover_engine.submit_vote(voter, "down", ts, sig)
            await failover_engine.evaluate_quorum()

        assert failover_engine.state == FailoverState.ACTIVE
        assert len(failover_engine.transitions) == 1

    async def test_failback_transition(
        self, failover_engine: FailoverEngine, voter_keys: dict[str, str]
    ) -> None:
        """Enough up rounds after failover triggers failback."""
        # First trigger failover
        for _ in range(2):
            ts = int(time.time())
            for voter in ["voter-1", "voter-2"]:
                sig = _sign(voter, "down", ts, voter_keys[voter])
                failover_engine.submit_vote(voter, "down", ts, sig)
            await failover_engine.evaluate_quorum()
        assert failover_engine.state == FailoverState.ACTIVE

        # Then trigger failback
        for _ in range(2):
            ts = int(time.time())
            for voter in ["voter-1", "voter-2"]:
                sig = _sign(voter, "up", ts, voter_keys[voter])
                failover_engine.submit_vote(voter, "up", ts, sig)
            await failover_engine.evaluate_quorum()
        assert failover_engine.state == FailoverState.STANDBY
        assert len(failover_engine.transitions) == 2

    async def test_no_quorum_no_transition(
        self, failover_engine: FailoverEngine, voter_keys: dict[str, str]
    ) -> None:
        """Without quorum, no state change happens."""
        ts = int(time.time())
        sig = _sign("voter-1", "down", ts, voter_keys["voter-1"])
        failover_engine.submit_vote("voter-1", "down", ts, sig)
        for _ in range(5):
            await failover_engine.evaluate_quorum()
        assert failover_engine.state == FailoverState.STANDBY

    def test_get_metrics(self, failover_engine: FailoverEngine) -> None:
        """Metrics returns expected keys."""
        metrics = failover_engine.get_metrics()
        assert "state" in metrics
        assert "active_votes" in metrics

    def test_config_property(self, failover_engine: FailoverEngine) -> None:
        """Config returns expected keys."""
        config = failover_engine.config
        assert config["quorum"] == 2
        assert config["failover_rounds"] == 2


class TestVoteSignatureVerification:
    """HMAC signature verification uses constant-time comparison."""

    def test_signature_comparison_uses_constant_time_equality(self) -> None:
        """verify_vote_signature uses hmac.compare_digest."""
        import inspect

        source = inspect.getsource(verify_vote_signature)
        assert "hmac.compare_digest" in source


class TestFailoverStatePersistence:
    """Failover state persistence across restarts."""

    def test_state_file_created_on_failover(
        self, voter_keys: dict[str, str], tmp_path: Path
    ) -> None:
        """State file is written when failover activates."""
        state_file = tmp_path / "failover-state.json"
        engine = FailoverEngine(
            quorum=1,
            failover_rounds=1,
            failback_rounds=1,
            vote_ttl=90,
            voter_keys=voter_keys,
            vote_cooldown=0,
            state_file=state_file,
        )
        ts = int(time.time())
        sig = _sign("voter-1", "down", ts, voter_keys["voter-1"])
        engine.submit_vote("voter-1", "down", ts, sig)

        import asyncio

        asyncio.get_event_loop().run_until_complete(engine.evaluate_quorum())

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["state"] == "active"

    def test_state_restored_on_start(
        self, voter_keys: dict[str, str], tmp_path: Path
    ) -> None:
        """Engine resumes ACTIVE state from persisted file."""
        state_file = tmp_path / "failover-state.json"
        state_file.write_text(
            json.dumps(
                {
                    "state": "active",
                    "timestamp": time.time(),
                    "consecutive_down": 0,
                    "consecutive_up": 0,
                }
            )
        )
        engine = FailoverEngine(
            quorum=1,
            failover_rounds=1,
            failback_rounds=1,
            vote_ttl=90,
            voter_keys=voter_keys,
            vote_cooldown=0,
            state_file=state_file,
        )
        import asyncio

        asyncio.get_event_loop().run_until_complete(engine.start())
        assert engine.state == FailoverState.ACTIVE

    def test_corrupt_state_file_defaults_to_standby(
        self, voter_keys: dict[str, str], tmp_path: Path
    ) -> None:
        """Corrupt state file gracefully defaults to STANDBY."""
        state_file = tmp_path / "failover-state.json"
        state_file.write_text("not json{{{")
        engine = FailoverEngine(
            quorum=1,
            failover_rounds=1,
            failback_rounds=1,
            vote_ttl=90,
            voter_keys=voter_keys,
            vote_cooldown=0,
            state_file=state_file,
        )
        import asyncio

        asyncio.get_event_loop().run_until_complete(engine.start())
        assert engine.state == FailoverState.STANDBY

    def test_missing_state_file_defaults_to_standby(
        self, voter_keys: dict[str, str], tmp_path: Path
    ) -> None:
        """Missing state file gracefully defaults to STANDBY."""
        state_file = tmp_path / "failover-state.json"
        engine = FailoverEngine(
            quorum=1,
            failover_rounds=1,
            failback_rounds=1,
            vote_ttl=90,
            voter_keys=voter_keys,
            vote_cooldown=0,
            state_file=state_file,
        )
        import asyncio

        asyncio.get_event_loop().run_until_complete(engine.start())
        assert engine.state == FailoverState.STANDBY

    def test_no_state_file_configured(
        self,
        voter_keys: dict[str, str],
    ) -> None:
        """Engine works without state_file configured."""
        engine = FailoverEngine(
            quorum=1,
            failover_rounds=1,
            failback_rounds=1,
            vote_ttl=90,
            voter_keys=voter_keys,
            vote_cooldown=0,
        )
        import asyncio

        asyncio.get_event_loop().run_until_complete(engine.start())
        assert engine.state == FailoverState.STANDBY
