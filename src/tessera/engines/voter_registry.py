"""Voter self-registration engine.

Manages voter registration lifecycle: one-time registration tokens,
pending/approved/revoked voters, PSK generation, and key rotation
with grace periods.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from tessera.exceptions import AppError, AuthenticationError, NotFoundError
from tessera.registry import Engine, EngineHealth, EngineStatus

logger = logging.getLogger(__name__)


class RegistrationError(AppError):
    """A voter registration operation failed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(slots=True)
class RegistrationToken:
    """A one-time-use registration token.

    Attributes:
        token: The hex token string.
        created_at: Unix timestamp of creation.
        expires_at: Unix timestamp of expiry.
        used: Whether the token has been consumed.
        used_by: Voter name that consumed the token (if any).
        bind_ip: If set, registration only accepted from this IP.
    """

    token: str
    created_at: float
    expires_at: float
    used: bool = False
    used_by: str | None = None
    bind_ip: str | None = None

    def is_expired(self) -> bool:
        """Check if the token has expired."""
        return time.time() > self.expires_at

    def is_valid(self) -> bool:
        """Check if the token can still be used."""
        return not self.used and not self.is_expired()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return {
            "token": self.token,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "used": self.used,
            "used_by": self.used_by,
            "bind_ip": self.bind_ip,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegistrationToken:
        """Deserialize from dict."""
        return cls(
            token=data["token"],
            created_at=data["created_at"],
            expires_at=data["expires_at"],
            used=data.get("used", False),
            used_by=data.get("used_by"),
            bind_ip=data.get("bind_ip"),
        )


@dataclass(slots=True)
class VoterRecord:
    """Metadata about a registered voter.

    Attributes:
        name: Voter identifier.
        registered_at: When the voter registered.
        approved_at: When the voter was approved (None if pending).
        status: Current status (pending, active, revoked).
        last_vote: Timestamp of last vote (0 if never voted).
        ip_address: IP address seen during registration.
        callback_url: Optional callback URL for readiness notification.
    """

    name: str
    registered_at: float
    approved_at: float | None = None
    status: str = "pending"
    last_vote: float = 0.0
    ip_address: str = ""
    callback_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return {
            "registered_at": self.registered_at,
            "approved_at": self.approved_at,
            "status": self.status,
            "last_vote": self.last_vote,
            "ip_address": self.ip_address,
            "callback_url": self.callback_url,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> VoterRecord:
        """Deserialize from dict."""
        return cls(
            name=name,
            registered_at=data.get("registered_at", 0.0),
            approved_at=data.get("approved_at"),
            status=data.get("status", "pending"),
            last_vote=data.get("last_vote", 0.0),
            ip_address=data.get("ip_address", ""),
            callback_url=data.get("callback_url", ""),
        )


@dataclass(slots=True)
class GracePeriodKey:
    """A PSK in its grace period (old key still accepted).

    Attributes:
        voter: Voter name.
        old_psk: Previous PSK.
        new_psk: New PSK.
        expires_at: When the grace period ends.
    """

    voter: str
    old_psk: str
    new_psk: str
    expires_at: float


class VoterRegistryEngine(Engine):
    """Voter registration and PSK management engine.

    Handles:
    - One-time registration token generation and validation
    - Voter registration (pending → approved → active)
    - PSK generation and rotation with grace periods
    - Voter revocation

    Attributes:
        name: Engine identifier.
        version: Engine version.
    """

    name: str = "voter_registry"
    version: str = "1.0.0"
    description: str = "Voter self-registration and PSK management"

    def __init__(
        self,
        *,
        voter_keys_file: str | os.PathLike[str],
        voter_registry_file: str | os.PathLike[str],
        reg_tokens_file: str | os.PathLike[str],
        static_registration_token: str = "",
        auto_approve: bool = False,
        token_ttl: int = 3600,
        psk_grace_period: int = 60,
    ) -> None:
        super().__init__()
        from pathlib import Path

        self._voter_keys_file = Path(voter_keys_file)
        self._voter_registry_file = Path(voter_registry_file)
        self._reg_tokens_file = Path(reg_tokens_file)
        self._static_token = static_registration_token
        self._auto_approve = auto_approve
        self._token_ttl = token_ttl
        self._psk_grace_period = psk_grace_period

        self._tokens: dict[str, RegistrationToken] = {}
        self._voters: dict[str, VoterRecord] = {}
        self._grace_keys: dict[str, GracePeriodKey] = {}

        # Callbacks
        self._on_keys_changed: Any = None

    def set_on_keys_changed(self, callback: Any) -> None:
        """Set callback invoked when voter keys file is updated."""
        self._on_keys_changed = callback

    async def start(self) -> None:
        """Load existing state from disk."""
        self._load_tokens()
        self._load_registry()
        logger.info(
            "VoterRegistryEngine started (%d tokens, %d voters)",
            len(self._tokens),
            len(self._voters),
        )

    async def stop(self) -> None:
        """Persist state on shutdown."""
        self._save_tokens()
        self._save_registry()
        logger.info("VoterRegistryEngine stopped")

    # ── Token management ─────────────────────────────────────────────────

    def generate_token(
        self,
        *,
        bind_ip: str | None = None,
        ttl: int | None = None,
    ) -> RegistrationToken:
        """Generate a one-time registration token.

        Args:
            bind_ip: Optional IP restriction.
            ttl: Override default TTL in seconds.

        Returns:
            The generated token.
        """
        now = time.time()
        token_str = secrets.token_hex(32)
        token = RegistrationToken(
            token=token_str,
            created_at=now,
            expires_at=now + (ttl if ttl is not None else self._token_ttl),
            bind_ip=bind_ip,
        )
        self._tokens[token_str] = token
        self._save_tokens()
        logger.info("Registration token generated (expires in %ds)", ttl if ttl is not None else self._token_ttl)
        return token

    def validate_token(
        self,
        token_str: str,
        *,
        source_ip: str = "",
    ) -> RegistrationToken:
        """Validate a registration token.

        Args:
            token_str: The token string to validate.
            source_ip: Source IP of the request.

        Returns:
            The valid token.

        Raises:
            AuthenticationError: If the token is invalid, expired, or used.
        """
        # Check static token first
        if self._static_token and token_str == self._static_token:
            return RegistrationToken(
                token=token_str,
                created_at=0.0,
                expires_at=float("inf"),
                used=False,
                bind_ip=None,
            )

        token = self._tokens.get(token_str)
        if not token:
            raise AuthenticationError("Invalid registration token")
        if token.used:
            raise AuthenticationError("Registration token already used")
        if token.is_expired():
            raise AuthenticationError("Registration token expired")
        if token.bind_ip and source_ip and token.bind_ip != source_ip:
            raise AuthenticationError(
                f"Token bound to {token.bind_ip}, request from {source_ip}"
            )
        return token

    def consume_token(self, token_str: str, voter_name: str) -> None:
        """Mark a token as consumed.

        Static tokens are never consumed.

        Args:
            token_str: The token to consume.
            voter_name: The voter that used it.
        """
        if self._static_token and token_str == self._static_token:
            return  # Static token is never consumed
        token = self._tokens.get(token_str)
        if token:
            token.used = True
            token.used_by = voter_name
            self._save_tokens()

    def list_tokens(self) -> list[RegistrationToken]:
        """Return all registration tokens."""
        return list(self._tokens.values())

    def cleanup_expired_tokens(self) -> int:
        """Remove expired tokens from storage.

        Returns:
            Number of tokens removed.
        """
        expired = [k for k, t in self._tokens.items() if t.is_expired()]
        for k in expired:
            del self._tokens[k]
        if expired:
            self._save_tokens()
            logger.info("Cleaned up %d expired registration tokens", len(expired))
        return len(expired)

    # ── Voter registration ───────────────────────────────────────────────

    def register_voter(
        self,
        name: str,
        token_str: str,
        *,
        source_ip: str = "",
        callback_url: str = "",
    ) -> tuple[VoterRecord, str | None]:
        """Register a new voter.

        Args:
            name: Voter name/hostname.
            token_str: Registration token.
            source_ip: Source IP of the request.
            callback_url: Optional callback for readiness notification.

        Returns:
            Tuple of (VoterRecord, psk_or_none).
            PSK is returned only if auto-approve is enabled.

        Raises:
            AuthenticationError: If token is invalid.
            RegistrationError: If voter already exists.
        """
        # Validate token
        self.validate_token(token_str, source_ip=source_ip)

        if name in self._voters and self._voters[name].status == "active":
            raise RegistrationError(f"Voter already registered and active: {name}")

        now = time.time()
        psk: str | None = None

        if self._auto_approve:
            psk = secrets.token_hex(32)
            record = VoterRecord(
                name=name,
                registered_at=now,
                approved_at=now,
                status="active",
                ip_address=source_ip,
                callback_url=callback_url,
            )
            self._add_voter_key(name, psk)
        else:
            record = VoterRecord(
                name=name,
                registered_at=now,
                status="pending",
                ip_address=source_ip,
                callback_url=callback_url,
            )

        self._voters[name] = record
        self.consume_token(token_str, name)
        self._save_registry()

        status = "auto-approved" if self._auto_approve else "pending"
        logger.info("Voter registered: %s (%s)", name, status)
        return record, psk

    def approve_voter(self, name: str) -> tuple[VoterRecord, str]:
        """Approve a pending voter registration.

        Args:
            name: Voter name.

        Returns:
            Tuple of (updated VoterRecord, generated PSK).

        Raises:
            NotFoundError: If voter not found.
            RegistrationError: If voter is not pending.
        """
        if name not in self._voters:
            raise NotFoundError("Voter", name)
        record = self._voters[name]
        if record.status != "pending":
            raise RegistrationError(f"Voter is {record.status}, not pending")

        psk = secrets.token_hex(32)
        record.approved_at = time.time()
        record.status = "active"
        self._add_voter_key(name, psk)
        self._save_registry()

        logger.info("Voter approved: %s", name)
        return record, psk

    def revoke_voter(self, name: str) -> VoterRecord:
        """Revoke a voter, removing their PSK.

        Args:
            name: Voter name.

        Returns:
            Updated VoterRecord.

        Raises:
            NotFoundError: If voter not found.
        """
        if name not in self._voters:
            raise NotFoundError("Voter", name)
        record = self._voters[name]
        record.status = "revoked"
        self._remove_voter_key(name)
        self._grace_keys.pop(name, None)
        self._save_registry()

        logger.info("Voter revoked: %s", name)
        return record

    def list_voters(self) -> list[VoterRecord]:
        """Return all voter records."""
        return list(self._voters.values())

    def list_pending(self) -> list[VoterRecord]:
        """Return only pending voter records."""
        return [v for v in self._voters.values() if v.status == "pending"]

    def get_voter(self, name: str) -> VoterRecord:
        """Get a voter record by name.

        Raises:
            NotFoundError: If voter not found.
        """
        if name not in self._voters:
            raise NotFoundError("Voter", name)
        return self._voters[name]

    # ── PSK rotation ─────────────────────────────────────────────────────

    def rotate_key(self, name: str) -> str:
        """Rotate a voter's PSK with a grace period.

        During the grace period, both old and new PSKs are accepted.

        Args:
            name: Voter name.

        Returns:
            The new PSK.

        Raises:
            NotFoundError: If voter not found or not active.
        """
        if name not in self._voters:
            raise NotFoundError("Voter", name)
        if self._voters[name].status != "active":
            raise RegistrationError(f"Voter is {self._voters[name].status}, not active")

        keys = self._load_voter_keys()
        old_psk = keys.get(name, "")
        new_psk = secrets.token_hex(32)

        # Set grace period
        if old_psk:
            self._grace_keys[name] = GracePeriodKey(
                voter=name,
                old_psk=old_psk,
                new_psk=new_psk,
                expires_at=time.time() + self._psk_grace_period,
            )

        # Write new key immediately
        self._add_voter_key(name, new_psk)
        logger.info(
            "PSK rotated for %s (grace period: %ds)", name, self._psk_grace_period
        )
        return new_psk

    def get_valid_psks(self, voter: str) -> list[str]:
        """Return all currently valid PSKs for a voter.

        During grace period, returns both old and new PSKs.
        After grace period, returns only the current PSK.

        Args:
            voter: Voter name.

        Returns:
            List of valid PSK strings.
        """
        keys = self._load_voter_keys()
        current = keys.get(voter)
        if not current:
            return []

        result = [current]

        grace = self._grace_keys.get(voter)
        if grace and time.time() < grace.expires_at:
            # Add old key if it's different from current
            if grace.old_psk != current and grace.old_psk not in result:
                result.append(grace.old_psk)
        elif grace:
            # Grace period expired, clean up
            del self._grace_keys[voter]

        return result

    def is_in_grace_period(self, voter: str) -> bool:
        """Check if a voter is currently in a PSK grace period."""
        grace = self._grace_keys.get(voter)
        if grace and time.time() < grace.expires_at:
            return True
        if grace:
            del self._grace_keys[voter]
        return False

    # ── File I/O ─────────────────────────────────────────────────────────

    def _load_voter_keys(self) -> dict[str, str]:
        """Load voter keys from disk."""
        try:
            return json.loads(self._voter_keys_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _add_voter_key(self, name: str, psk: str) -> None:
        """Add or update a voter key on disk and notify."""
        keys = self._load_voter_keys()
        keys[name] = psk
        self._voter_keys_file.parent.mkdir(parents=True, exist_ok=True)
        self._voter_keys_file.write_text(json.dumps(keys, indent=2))
        if self._on_keys_changed:
            self._on_keys_changed(keys)

    def _remove_voter_key(self, name: str) -> None:
        """Remove a voter key from disk and notify."""
        keys = self._load_voter_keys()
        keys.pop(name, None)
        self._voter_keys_file.write_text(json.dumps(keys, indent=2))
        if self._on_keys_changed:
            self._on_keys_changed(keys)

    def _load_tokens(self) -> None:
        """Load registration tokens from disk."""
        try:
            data = json.loads(self._reg_tokens_file.read_text())
            self._tokens = {
                k: RegistrationToken.from_dict(v) for k, v in data.items()
            }
        except (FileNotFoundError, json.JSONDecodeError):
            self._tokens = {}

    def _save_tokens(self) -> None:
        """Persist registration tokens to disk."""
        self._reg_tokens_file.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in self._tokens.items()}
        self._reg_tokens_file.write_text(json.dumps(data, indent=2))

    def _load_registry(self) -> None:
        """Load voter registry from disk."""
        try:
            data = json.loads(self._voter_registry_file.read_text())
            self._voters = {
                k: VoterRecord.from_dict(k, v) for k, v in data.items()
            }
        except (FileNotFoundError, json.JSONDecodeError):
            self._voters = {}

    def _save_registry(self) -> None:
        """Persist voter registry to disk."""
        self._voter_registry_file.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in self._voters.items()}
        self._voter_registry_file.write_text(json.dumps(data, indent=2))

    async def check_health(self) -> EngineHealth:
        """Return engine health."""
        active = sum(1 for v in self._voters.values() if v.status == "active")
        pending = sum(1 for v in self._voters.values() if v.status == "pending")
        self.health.status = EngineStatus.RUNNING
        self.health.message = f"{active} active, {pending} pending voters"
        return self.health

    def get_metrics(self) -> dict[str, Any]:
        """Return engine metrics."""
        return {
            "total_voters": len(self._voters),
            "active_voters": sum(
                1 for v in self._voters.values() if v.status == "active"
            ),
            "pending_voters": sum(
                1 for v in self._voters.values() if v.status == "pending"
            ),
            "revoked_voters": sum(
                1 for v in self._voters.values() if v.status == "revoked"
            ),
            "active_tokens": sum(
                1 for t in self._tokens.values() if t.is_valid()
            ),
            "grace_period_keys": len(self._grace_keys),
        }
