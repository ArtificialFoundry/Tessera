"""Audit trail engine — structured logging of admin actions."""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from tessera.fileutil import atomic_write
from tessera.registry import Engine, EngineHealth, EngineStatus

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MAX_EVENTS = 10_000
_DEFAULT_RETENTION_DAYS = 90


@dataclass(slots=True)
class AuditEvent:
    """A single audit trail entry."""

    timestamp: float
    action: str
    actor: str
    target: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to dictionary."""
        return asdict(self)


class AuditEngine(Engine):
    """Append-only audit trail for admin actions.

    Events are kept in memory (capped ring buffer) and persisted to a
    JSON-Lines file on disk.  The ``/api/v1/audit`` endpoint reads from
    the in-memory buffer for fast queries.
    """

    name = "audit"
    version = "1.0.0"
    description = "Structured audit trail for admin actions"

    def __init__(
        self,
        audit_dir: Path,
        *,
        max_events: int = _DEFAULT_MAX_EVENTS,
        retention_days: int = _DEFAULT_RETENTION_DAYS,
    ) -> None:
        super().__init__()
        self._audit_dir = audit_dir
        self._max_events = max_events
        self._retention_days = retention_days
        self._events: deque[AuditEvent] = deque(maxlen=max_events)
        self._log_file = audit_dir / "audit.jsonl"
        self._total_events: int = 0

    async def start(self) -> None:
        """Create audit directory and load existing events."""
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._load_existing()
        self.health.status = EngineStatus.RUNNING
        self.health.message = f"Audit trail active ({len(self._events)} events loaded)"
        logger.info(
            "AuditEngine started (dir=%s, loaded=%d, retention=%dd)",
            self._audit_dir,
            len(self._events),
            self._retention_days,
        )

    async def stop(self) -> None:
        """Mark stopped."""
        self.health.status = EngineStatus.STOPPED
        self.health.message = "Stopped"

    def record(
        self,
        action: str,
        actor: str,
        target: str = "",
        detail: str = "",
    ) -> AuditEvent:
        """Record an audit event.

        Args:
            action: What happened (e.g. ``backup.create``, ``voter.approve``).
            actor: Who did it (IP address or system identifier).
            target: What was affected (backup ID, voter name, etc.).
            detail: Free-form additional context.

        Returns:
            The created event.
        """
        event = AuditEvent(
            timestamp=time.time(),
            action=action,
            actor=actor,
            target=target,
            detail=detail,
        )
        self._events.append(event)
        self._total_events += 1
        self._append_to_disk(event)
        return event

    def query(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        action_filter: str = "",
        actor_filter: str = "",
    ) -> tuple[list[AuditEvent], int]:
        """Query audit events with optional filters.

        Returns:
            Tuple of (events, total_matching).
        """
        events = list(self._events)
        events.reverse()  # newest first

        if action_filter:
            events = [e for e in events if action_filter in e.action]
        if actor_filter:
            events = [e for e in events if actor_filter in e.actor]

        total = len(events)
        page = events[offset : offset + limit]
        return page, total

    def get_metrics(self) -> dict[str, Any]:
        """Return audit metrics."""
        return {
            "total_events": self._total_events,
            "buffered_events": len(self._events),
            "max_events": self._max_events,
            "retention_days": self._retention_days,
        }

    async def check_health(self) -> EngineHealth:
        """Return health status."""
        return self.health

    def _append_to_disk(self, event: AuditEvent) -> None:
        """Append a single event to the JSONL file."""
        try:
            line = json.dumps(event.to_dict(), separators=(",", ":"))
            with self._log_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            logger.exception("Failed to write audit event to disk")

    def _load_existing(self) -> None:
        """Load existing events from JSONL file."""
        if not self._log_file.is_file():
            return
        cutoff = time.time() - (self._retention_days * 86400)
        kept_lines: list[str] = []
        for line in self._log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("timestamp", 0) < cutoff:
                continue
            kept_lines.append(line)
            event = AuditEvent(
                timestamp=data["timestamp"],
                action=data["action"],
                actor=data["actor"],
                target=data.get("target", ""),
                detail=data.get("detail", ""),
            )
            self._events.append(event)
            self._total_events += 1

        # Rewrite file without expired entries
        if kept_lines:
            atomic_write(self._log_file, "\n".join(kept_lines) + "\n")
        elif self._log_file.is_file():
            self._log_file.unlink()

    async def enforce_retention(self) -> int:
        """Remove events older than retention period.

        Returns:
            Number of events pruned.
        """
        cutoff = time.time() - (self._retention_days * 86400)
        before = len(self._events)
        self._events = deque(
            (e for e in self._events if e.timestamp >= cutoff),
            maxlen=self._max_events,
        )
        pruned = before - len(self._events)
        if pruned:
            # Rewrite disk file
            lines = [
                json.dumps(e.to_dict(), separators=(",", ":")) for e in self._events
            ]
            if lines:
                atomic_write(self._log_file, "\n".join(lines) + "\n")
            elif self._log_file.is_file():
                self._log_file.unlink()
            logger.info("Audit retention: pruned %d events", pruned)
        return pruned
