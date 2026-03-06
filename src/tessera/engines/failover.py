"""DHCP failover engine with voter quorum and state machine.

Manages active/candidate failover for Technitium DHCP service.
Voters submit health checks; when quorum determines active server is down,
candidate DHCP scopes are activated.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from tessera.exceptions import AuthenticationError, RateLimitError
from tessera.registry import Engine, EngineHealth, EngineStatus

if TYPE_CHECKING:
    from tessera.engines.technitium import TechnitiumClient, TechnitiumPool
    from tessera.engines.voter_registry import VoterRegistryEngine

logger = logging.getLogger(__name__)


class FailoverState(StrEnum):
    """Failover state machine states."""

    STANDBY = auto()
    ACTIVE = auto()


class VoteStatus(StrEnum):
    """Possible vote statuses from voters."""

    UP = auto()
    DOWN = auto()


class VoteVerification(StrEnum):
    """Server-side verification result for a vote."""

    VERIFIED = auto()
    UNVERIFIED = auto()
    CONFLICT = auto()


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
    verification: VoteVerification = VoteVerification.UNVERIFIED


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

    Collects votes from VMs about active DHCP health.
    When quorum says active server is down for enough consecutive rounds,
    activates candidate DHCP scopes. When primary recovers,
    deactivates candidate scopes (failback).

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
        vote_cooldown: float = 10.0,
    ) -> None:
        super().__init__()
        self._quorum = quorum
        self._failover_rounds = failover_rounds
        self._failback_rounds = failback_rounds
        self._vote_ttl = vote_ttl
        self._vote_cooldown = vote_cooldown
        self._voter_keys: dict[str, str] = voter_keys or {}
        self._state = FailoverState.STANDBY
        self._votes: dict[str, Vote] = {}
        self._vote_timestamps: dict[str, float] = {}
        self._consecutive_down: int = 0
        self._consecutive_up: int = 0
        self._transitions: list[TransitionEvent] = []
        self._last_evaluation: float = 0.0
        self._pool: TechnitiumPool | None = None
        # Legacy single-client references (kept for backward compat)
        self._candidate_client: Any = None
        self._active_client: TechnitiumClient | None = None
        self._scope_names: list[str] = []
        self._voter_registry: VoterRegistryEngine | None = None

    def set_pool(self, pool: TechnitiumPool) -> None:
        """Set the TechnitiumPool for multi-server failover.

        Args:
            pool: The pool managing all DHCP servers.
        """
        self._pool = pool

    def set_voter_registry(self, registry: VoterRegistryEngine) -> None:
        """Set the voter registry for grace-period PSK lookups.

        Args:
            registry: The voter registry engine.
        """
        self._voter_registry = registry

    def set_candidate_client(self, client: Any) -> None:
        """Set the standby Technitium client for scope activation.

        Args:
            client: TechnitiumClient for the candidate server.
        """
        self._candidate_client = client

    def set_active_client(self, client: TechnitiumClient) -> None:
        """Set the primary Technitium client for vote verification.

        Args:
            client: TechnitiumClient for the primary server.
        """
        self._active_client = client

    def set_scope_names(self, names: list[str]) -> None:
        """Set the list of scope names to manage on failover.

        Args:
            names: DHCP scope names to enable/disable on standby.
        """
        self._scope_names = names

    def update_voter_keys(self, keys: dict[str, str]) -> None:
        """Atomically swap the voter PSK map.

        Existing votes from removed voters are immediately invalidated.

        Args:
            keys: New voter name → PSK mapping.
        """
        removed = set(self._voter_keys.keys()) - set(keys.keys())
        for voter in removed:
            self._votes.pop(voter, None)
            self._vote_timestamps.pop(voter, None)
            logger.info("Voter removed and votes invalidated: %s", voter)
        self._voter_keys = dict(keys)
        logger.info("Voter keys updated: %d voters", len(keys))

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

        # Per-voter rate limiting
        last_vote_time = self._vote_timestamps.get(voter, 0.0)
        elapsed = now - last_vote_time
        if elapsed < self._vote_cooldown:
            retry_after = self._vote_cooldown - elapsed
            raise RateLimitError(voter, retry_after)

        if abs(now - timestamp_val) > 60:
            raise AuthenticationError("Timestamp too far from server time")

        psk = self._voter_keys[voter]
        # Check against current PSK and any grace-period PSKs
        valid_psks = [psk]
        if self._voter_registry:
            valid_psks = self._voter_registry.get_valid_psks(voter) or valid_psks
        if not any(
            verify_vote_signature(voter, status, timestamp_val, signature, p)
            for p in valid_psks
        ):
            raise AuthenticationError("Invalid signature")

        vote_status = VoteStatus(status.lower())
        vote = Vote(voter=voter, status=vote_status, timestamp=float(timestamp_val))
        self._votes[voter] = vote
        self._vote_timestamps[voter] = now
        logger.info("Vote received: %s = %s", voter, status)
        return vote

    def _active_votes(self) -> list[Vote]:
        """Return votes that haven't expired."""
        cutoff = time.time() - self._vote_ttl
        return [v for v in self._votes.values() if v.received_at >= cutoff]

    def _get_active_client(self) -> TechnitiumClient | None:
        """Get the primary client from pool or legacy reference."""
        if self._pool:
            try:
                return self._pool.get_active()
            except Exception:
                return None
        return self._active_client

    def _get_candidate_client(self) -> Any:
        """Get the standby client from pool or legacy reference."""
        if self._pool:
            return self._pool.get_candidate()
        return self._candidate_client

    def _get_candidate_clients(self) -> list[Any]:
        """Get all candidate clients from pool or legacy single client."""
        if self._pool:
            return self._pool.get_candidates()
        if self._candidate_client:
            return [self._candidate_client]
        return []

    async def _verify_active_health(self) -> bool | None:
        """Query the primary Technitium for DHCP health.

        Returns:
            True if active is healthy (has enabled scopes),
            False if reachable but unhealthy,
            None if unreachable (timeout/connection error).
        """
        primary = self._get_active_client()
        if not primary:
            logger.warning("No active client configured — skipping verification")
            return None
        try:
            scopes = await asyncio.wait_for(
                primary.list_scopes(),
                timeout=10.0,
            )
            enabled = [s for s in scopes if s.get("enabled", False)]
            if not enabled:
                logger.info("Active reachable but no enabled scopes")
                return False
            return True
        except TimeoutError:
            logger.info("Active health check timed out")
            return None
        except Exception:
            logger.info("Active health check failed", exc_info=True)
            return None

    def _verify_votes(
        self, votes: list[Vote], active_healthy: bool | None
    ) -> list[Vote]:
        """Cross-validate votes against server-side active health.

        Args:
            votes: Active votes to verify.
            active_healthy: True/False/None from _verify_active_health.

        Returns:
            List of votes that should count (VERIFIED + UNVERIFIED).
        """
        counted: list[Vote] = []
        for vote in votes:
            if active_healthy is None:
                # Can't reach primary — trust voter, leave UNVERIFIED
                vote.verification = VoteVerification.UNVERIFIED
                counted.append(vote)
            elif active_healthy and vote.status == VoteStatus.DOWN:
                vote.verification = VoteVerification.CONFLICT
                logger.warning(
                    "CONFLICT: voter %s says down but active is healthy",
                    vote.voter,
                )
            elif not active_healthy and vote.status == VoteStatus.UP:
                vote.verification = VoteVerification.CONFLICT
                logger.warning(
                    "CONFLICT: voter %s says up but active is unhealthy",
                    vote.voter,
                )
            else:
                vote.verification = VoteVerification.VERIFIED
                counted.append(vote)
        return counted

    async def _activate_candidate_scopes(self) -> None:
        """Enable all DHCP scopes on all candidate servers."""
        standbys = self._get_candidate_clients()
        if not standbys:
            logger.error("No candidate clients configured — cannot activate scopes")
            return
        for standby in standbys:
            for name in self._scope_names:
                try:
                    await standby.enable_scope(name)
                    sname = getattr(standby, "server_name", "unknown")
                    logger.info("Enabled standby scope %s on %s", name, sname)
                except Exception:
                    logger.exception("Failed to enable standby scope: %s", name)

    async def _deactivate_candidate_scopes(self) -> None:
        """Disable all DHCP scopes on all candidate servers."""
        standbys = self._get_candidate_clients()
        if not standbys:
            logger.error("No candidate clients configured — cannot deactivate scopes")
            return
        for standby in standbys:
            for name in self._scope_names:
                try:
                    await standby.disable_scope(name)
                    sname = getattr(standby, "server_name", "unknown")
                    logger.info("Disabled standby scope %s on %s", name, sname)
                except Exception:
                    logger.exception("Failed to disable standby scope: %s", name)

    async def _do_failover(self) -> None:
        """Execute failover: promote highest-priority standby, activate scopes."""
        if self._pool:
            standby = self._pool.get_candidate()
            if standby:
                active_name: str | None = None
                for name, client in self._pool._clients.items():
                    if self._pool._roles.get(name) == "active":
                        active_name = name
                        break
                self._pool.promote(standby.server_name)
                logger.warning(
                    "FAILOVER: promoted 0 (was: %s)",
                    standby.server_name,
                    active_name,
                )
        await self._activate_candidate_scopes()

    async def _do_failback(self) -> None:
        """Execute failback: deactivate candidate scopes."""
        await self._deactivate_candidate_scopes()

    async def evaluate_quorum(self) -> dict[str, Any]:
        """Evaluate current votes and potentially transition state.

        Runs server-side verification against the primary before counting.
        CONFLICT votes are excluded from quorum. When a transition occurs,
        scopes on the candidate server are enabled (failover) or disabled
        (failback) automatically.

        Returns:
            Dict with evaluation results including quorum status.
        """
        active = self._active_votes()

        # Server-side cross-validation
        active_healthy = await self._verify_active_health()
        counted = self._verify_votes(active, active_healthy)

        up_count = sum(1 for v in counted if v.status == VoteStatus.UP)
        down_count = sum(1 for v in counted if v.status == VoteStatus.DOWN)
        conflict_count = len(active) - len(counted)
        has_quorum = len(counted) >= self._quorum

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
            await self._do_failover()

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
            logger.info("FAILBACK: returned to candidate: %s", event.reason)
            await self._do_failback()

        return {
            "state": self._state.value,
            "has_quorum": has_quorum,
            "active_votes": len(counted),
            "up_count": up_count,
            "down_count": down_count,
            "conflict_count": conflict_count,
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
