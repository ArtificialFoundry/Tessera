"""DHCP failover engine with voter quorum and state machine.

Manages primary/standby failover for Technitium DHCP service.
Voters submit health checks; when quorum determines primary is down,
standby DHCP scopes are activated.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Any

from tessera.exceptions import AuthenticationError
from tessera.registry import Engine, EngineHealth, EngineStatus

logger = logging.getLogger(__name__)


class FailoverState(StrEnum):
    """Failover state machine states."""

    STANDBY = auto()
    ACTIVE = auto()


class VoteStatus(StrEnum):
    """Possible vote statuses from voters."""

    UP = auto()
    DOWN = auto()


@dataclass(slots=True)
class Vote:
    """A single voter's health check submission.

    Attributes:
        voter: Voter identifier.
        status: Reported status.
        timestamp: Unix timestamp of the vote.
        received_at: Server time when vote was received.
    """

    voter: str
    status: VoteStatus
    timestamp: float
    received_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class TransitionEvent:
    """Records a state transition.

    Attributes:
        from_state: Previous state.
        to_state: New state.
        timestamp: When the transition occurred.
        reason: Human-readable reason.
    """

    from_state: FailoverState
    to_state: FailoverState
    timestamp: float
    reason: str


def verify_vote_signature(
    voter: str,
    status: str,
    timestamp_val: int,
    signature: str,
    psk: str,
) -> bool:
    """Verify HMAC-SHA256 signature on a vote payload.

    Args:
        voter: Voter name.
        status: Vote status string.
        timestamp_val: Unix timestamp from payload.
        signature: Hex-encoded HMAC signature.
        psk: Pre-shared key for this voter.

    Returns:
        True if signature is valid.
    """
    import hashlib
    import hmac

    message = f"{voter}|{status}|{timestamp_val}"
    expected = hmac.new(psk.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class FailoverEngine(Engine):
    """Quorum-based DHCP failover state machine.

    Collects votes from VMs about primary DHCP health.
    When quorum says primary is down for enough consecutive rounds,
    activates standby DHCP scopes. When primary recovers,
    deactivates standby scopes (failback).

    Attributes:
        name: Engine identifier.
        version: Engine version.
        depends_on: Required engines.
    """

    name: str = "failover"
    version: str = "1.0.0"
    description: str = "DHCP failover with voter quorum"
    depends_on: tuple[str, ...] = ("technitium",)

    def __init__(
        self,
        *,
        quorum: int = 3,
        failover_rounds: int = 3,
        failback_rounds: int = 5,
        vote_ttl: int = 90,
        voter_keys: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._quorum = quorum
        self._failover_rounds = failover_rounds
        self._failback_rounds = failback_rounds
        self._vote_ttl = vote_ttl
        self._voter_keys: dict[str, str] = voter_keys or {}
        self._state = FailoverState.STANDBY
        self._votes: dict[str, Vote] = {}
        self._consecutive_down: int = 0
        self._consecutive_up: int = 0
        self._transitions: list[TransitionEvent] = []
        self._last_evaluation: float = 0.0
        self._standby_client: Any = None
        self._scope_names: list[str] = []

    def set_standby_client(self, client: Any) -> None:
        """Set the standby Technitium client for scope activation.

        Args:
            client: TechnitiumClient for the standby server.
        """
        self._standby_client = client

    def set_scope_names(self, names: list[str]) -> None:
        """Set the list of scope names to manage on failover.

        Args:
            names: DHCP scope names to enable/disable on standby.
        """
        self._scope_names = names

    @property
    def state(self) -> FailoverState:
        """Current failover state."""
        return self._state

    @property
    def votes(self) -> dict[str, Vote]:
        """Current vote map."""
        return dict(self._votes)

    @property
    def transitions(self) -> list[TransitionEvent]:
        """History of state transitions."""
        return list(self._transitions)

    @property
    def consecutive_down(self) -> int:
        """Consecutive rounds with down quorum."""
        return self._consecutive_down

    @property
    def consecutive_up(self) -> int:
        """Consecutive rounds with up quorum."""
        return self._consecutive_up

    @property
    def config(self) -> dict[str, Any]:
        """Return engine configuration for API exposure."""
        return {
            "quorum": self._quorum,
            "failover_rounds": self._failover_rounds,
            "failback_rounds": self._failback_rounds,
            "vote_ttl": self._vote_ttl,
            "voters": list(self._voter_keys.keys()),
        }

    def submit_vote(
        self,
        voter: str,
        status: str,
        timestamp_val: int,
        signature: str,
    ) -> Vote:
        """Submit and validate a voter's health check.

        Args:
            voter: Voter identifier.
            status: Vote status ("up" or "down").
            timestamp_val: Unix timestamp from payload.
            signature: HMAC-SHA256 hex signature.

        Returns:
            The accepted Vote.

        Raises:
            AuthenticationError: On unknown voter, stale timestamp, or bad sig.
        """
        if voter not in self._voter_keys:
            raise AuthenticationError(f"Unknown voter: {voter}")

        now = time.time()
        if abs(now - timestamp_val) > 60:
            raise AuthenticationError("Timestamp too far from server time")

        psk = self._voter_keys[voter]
        if not verify_vote_signature(voter, status, timestamp_val, signature, psk):
            raise AuthenticationError("Invalid signature")

        vote_status = VoteStatus(status.lower())
        vote = Vote(voter=voter, status=vote_status, timestamp=float(timestamp_val))
        self._votes[voter] = vote
        logger.info("Vote received: %s = %s", voter, status)
        return vote

    def _active_votes(self) -> list[Vote]:
        """Return votes that haven't expired."""
        cutoff = time.time() - self._vote_ttl
        return [v for v in self._votes.values() if v.received_at >= cutoff]

    async def _activate_standby_scopes(self) -> None:
        """Enable all DHCP scopes on the standby server."""
        if not self._standby_client:
            logger.error("No standby client configured — cannot activate scopes")
            return
        for name in self._scope_names:
            try:
                await self._standby_client.enable_scope(name)
                logger.info("Enabled standby scope: %s", name)
            except Exception:
                logger.exception("Failed to enable standby scope: %s", name)

    async def _deactivate_standby_scopes(self) -> None:
        """Disable all DHCP scopes on the standby server."""
        if not self._standby_client:
            logger.error("No standby client configured — cannot deactivate scopes")
            return
        for name in self._scope_names:
            try:
                await self._standby_client.disable_scope(name)
                logger.info("Disabled standby scope: %s", name)
            except Exception:
                logger.exception("Failed to disable standby scope: %s", name)

    async def evaluate_quorum(self) -> dict[str, Any]:
        """Evaluate current votes and potentially transition state.

        When a transition occurs, scopes on the standby server are
        enabled (failover) or disabled (failback) automatically.

        Returns:
            Dict with evaluation results including quorum status.
        """
        active = self._active_votes()
        up_count = sum(1 for v in active if v.status == VoteStatus.UP)
        down_count = sum(1 for v in active if v.status == VoteStatus.DOWN)
        has_quorum = len(active) >= self._quorum

        self._last_evaluation = time.time()

        if has_quorum and down_count > up_count:
            self._consecutive_down += 1
            self._consecutive_up = 0
        elif has_quorum and up_count >= down_count:
            self._consecutive_up += 1
            self._consecutive_down = 0
        # No quorum — don't change counters

        old_state = self._state
        if (
            self._state == FailoverState.STANDBY
            and self._consecutive_down >= self._failover_rounds
        ):
            self._state = FailoverState.ACTIVE
            self._consecutive_down = 0
            event = TransitionEvent(
                from_state=old_state,
                to_state=self._state,
                timestamp=time.time(),
                reason=(
                    f"Quorum: {down_count} down votes "
                    f"over {self._failover_rounds} rounds"
                ),
            )
            self._transitions.append(event)
            logger.warning("FAILOVER ACTIVATED: %s", event.reason)
            await self._activate_standby_scopes()

        elif (
            self._state == FailoverState.ACTIVE
            and self._consecutive_up >= self._failback_rounds
        ):
            self._state = FailoverState.STANDBY
            self._consecutive_up = 0
            event = TransitionEvent(
                from_state=old_state,
                to_state=self._state,
                timestamp=time.time(),
                reason=(
                    f"Quorum: {up_count} up votes over {self._failback_rounds} rounds"
                ),
            )
            self._transitions.append(event)
            logger.info("FAILBACK: returned to standby: %s", event.reason)
            await self._deactivate_standby_scopes()

        return {
            "state": self._state.value,
            "has_quorum": has_quorum,
            "active_votes": len(active),
            "up_count": up_count,
            "down_count": down_count,
            "consecutive_down": self._consecutive_down,
            "consecutive_up": self._consecutive_up,
            "transitioned": old_state != self._state,
        }

    async def check_health(self) -> EngineHealth:
        """Return failover engine health."""
        self.health.status = EngineStatus.RUNNING
        self.health.message = f"State: {self._state.value}"
        self.health.details = {
            "state": self._state.value,
            "active_votes": len(self._active_votes()),
        }
        return self.health

    def get_metrics(self) -> dict[str, Any]:
        """Return failover metrics."""
        active = self._active_votes()
        return {
            "state": self._state.value,
            "active_votes": len(active),
            "total_transitions": len(self._transitions),
            "consecutive_down": self._consecutive_down,
            "consecutive_up": self._consecutive_up,
        }
