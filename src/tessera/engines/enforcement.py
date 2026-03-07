"""DHCP state enforcement engine.

Periodically compares the live DHCP configuration on the active server
against a pinned backup snapshot.  When drift is detected, the engine
either logs it (monitor mode) or automatically restores the pinned state
(enforce mode).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from tessera.exceptions import AppError
from tessera.registry import Engine, EngineHealth, EngineStatus

if TYPE_CHECKING:
    from tessera.engines.backup import BackupEngine
    from tessera.settings_store import SettingsStore

logger = logging.getLogger(__name__)


class EnforcementError(AppError):
    """An enforcement operation failed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class EnforcementMode(StrEnum):
    """Enforcement operating mode."""

    OFF = auto()
    MONITOR = auto()
    ENFORCE = auto()


@dataclass(slots=True)
class DriftEvent:
    """Records a drift detection event.

    Attributes:
        detected_at: Unix timestamp of detection.
        changes: List of changes needed to restore pinned state.
        drift_summary: List of changes from the human perspective (what drifted).
        change_count: Number of changes detected.
        action_taken: What the engine did (logged / restored / none).
        backup_id: Which backup was used for comparison.
    """

    detected_at: float
    changes: list[dict[str, str]]
    drift_summary: list[dict[str, str]]
    change_count: int
    action_taken: str
    backup_id: str
    count: int = 1
    last_seen: float = 0.0


@dataclass(slots=True)
class EnforcementState:
    """Current enforcement engine state.

    Attributes:
        mode: Current operating mode.
        pinned_backup_id: ID of the backup used as desired state.
        check_interval: Seconds between drift checks.
        last_check: Unix timestamp of last check.
        last_drift: Unix timestamp of last drift detection.
        drift_count: Total drifts detected since start.
        restore_count: Total auto-restores performed.
        history: Recent drift events (capped).
    """

    mode: EnforcementMode = EnforcementMode.OFF
    pinned_backup_id: str = ""
    check_interval: int = 300
    last_check: float = 0.0
    last_drift: float = 0.0
    drift_count: int = 0
    restore_count: int = 0
    backup_on_pin: bool = True
    auto_restore_cooldown: int = 60
    max_history: int = 50
    last_restore_at: float = 0.0
    history: list[DriftEvent] = field(default_factory=list)


class EnforcementEngine(Engine):
    """Drift detection and automatic state enforcement.

    Compares live DHCP config against a pinned backup snapshot.
    Three modes:
    - OFF: No checks.
    - MONITOR: Detect and log drift, no automatic correction.
    - ENFORCE: Detect drift and auto-restore from pinned backup.

    Attributes:
        name: Engine identifier.
        version: Engine version.
        depends_on: Required engines.
    """

    name: str = "enforcement"
    version: str = "1.0.0"
    description: str = "DHCP config drift detection and enforcement"
    depends_on: tuple[str, ...] = ("technitium", "backup")

    def __init__(
        self,
        *,
        check_interval: int = 300,
        backup_on_pin: bool = True,
        auto_restore_cooldown: int = 60,
        max_history: int = 50,
        settings_store: SettingsStore | None = None,
    ) -> None:
        super().__init__()
        self._store = settings_store
        self._state = EnforcementState(
            check_interval=check_interval,
            backup_on_pin=backup_on_pin,
            auto_restore_cooldown=auto_restore_cooldown,
            max_history=max_history,
        )
        self._backup_engine: BackupEngine | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_error: str = ""

        # Restore persisted settings
        self._restore_settings()

    def _restore_settings(self) -> None:
        """Load persisted settings from store."""
        if not self._store:
            return
        saved = self._store.get("enforcement")
        if not saved:
            return
        if "check_interval" in saved:
            self._state.check_interval = int(saved["check_interval"])
        if "backup_on_pin" in saved:
            self._state.backup_on_pin = bool(saved["backup_on_pin"])
        if "auto_restore_cooldown" in saved:
            self._state.auto_restore_cooldown = int(saved["auto_restore_cooldown"])
        if "max_history" in saved:
            self._state.max_history = int(saved["max_history"])
        if "mode" in saved:
            self._state.mode = EnforcementMode(saved["mode"])
        if saved.get("pinned_backup_id"):
            self._state.pinned_backup_id = saved["pinned_backup_id"]
        logger.info(
            "Enforcement settings restored: mode=%s, interval=%ds, pinned=%s",
            self._state.mode,
            self._state.check_interval,
            self._state.pinned_backup_id or "none",
        )

    def _persist_settings(self) -> None:
        """Save current settings to the store."""
        if not self._store:
            return
        self._store.put("enforcement", {
            "mode": str(self._state.mode),
            "check_interval": self._state.check_interval,
            "backup_on_pin": self._state.backup_on_pin,
            "auto_restore_cooldown": self._state.auto_restore_cooldown,
            "max_history": self._state.max_history,
            "pinned_backup_id": self._state.pinned_backup_id,
        })

    def set_backup_engine(self, engine: BackupEngine) -> None:
        """Set the backup engine for snapshot access.

        Args:
            engine: The BackupEngine instance.
        """
        self._backup_engine = engine

    @property
    def enforcement_state(self) -> EnforcementState:
        """Return the current enforcement state."""
        return self._state

    @property
    def mode(self) -> EnforcementMode:
        """Return the current enforcement mode."""
        return self._state.mode

    def update_settings(
        self,
        *,
        check_interval: int | None = None,
        backup_on_pin: bool | None = None,
        auto_restore_cooldown: int | None = None,
        max_history: int | None = None,
    ) -> None:
        """Update runtime enforcement settings.

        Args:
            check_interval: Seconds between drift checks.
            backup_on_pin: Auto-create backup before pinning.
            auto_restore_cooldown: Min seconds between auto-restores.
            max_history: Max drift events to retain.

        Raises:
            EnforcementError: If a value is out of range.
        """
        if check_interval is not None:
            if check_interval < 30:
                raise EnforcementError("check_interval must be >= 30 seconds")
            self._state.check_interval = check_interval
            if self._state.mode != EnforcementMode.OFF:
                self._manage_task()

        if backup_on_pin is not None:
            self._state.backup_on_pin = backup_on_pin

        if auto_restore_cooldown is not None:
            if auto_restore_cooldown < 0:
                raise EnforcementError("auto_restore_cooldown must be >= 0")
            self._state.auto_restore_cooldown = auto_restore_cooldown

        if max_history is not None:
            if max_history < 1:
                raise EnforcementError("max_history must be >= 1")
            self._state.max_history = max_history
            if len(self._state.history) > max_history:
                self._state.history = self._state.history[-max_history:]

        logger.info(
            "Enforcement settings updated: interval=%ds, backup_on_pin=%s, "
            "cooldown=%ds, max_history=%d",
            self._state.check_interval,
            self._state.backup_on_pin,
            self._state.auto_restore_cooldown,
            self._state.max_history,
        )
        self._persist_settings()

    async def accept_drift(self) -> str:
        """Accept current drift by snapshotting live state and pinning it.

        Returns:
            The new backup ID that was pinned.

        Raises:
            EnforcementError: If backup engine is not configured.
        """
        if not self._backup_engine:
            raise EnforcementError("Backup engine not configured")

        manifest = await self._backup_engine.create_backup(
            description="Accepted drift — pinned current state"
        )
        old_pin = self._state.pinned_backup_id
        self._state.pinned_backup_id = manifest.backup_id
        logger.info("Drift accepted: new pin %s (was %s)", manifest.backup_id, old_pin)
        self._persist_settings()
        return manifest.backup_id

    async def pin_backup(self, backup_id: str) -> None:
        """Pin a backup as the desired state.

        If ``backup_on_pin`` is enabled, a fresh backup is created first
        so the pinned state reflects the exact moment of pinning.

        Args:
            backup_id: ID of the backup to pin.

        Raises:
            NotFoundError: If the backup doesn't exist.
            EnforcementError: If the backup engine is not configured.
        """
        if not self._backup_engine:
            raise EnforcementError("Backup engine not configured")
        # Validate backup exists
        await self._backup_engine.get_backup(backup_id)
        self._state.pinned_backup_id = backup_id
        logger.info("Pinned backup: %s", backup_id)
        self._persist_settings()

    def unpin(self) -> None:
        """Remove the pinned backup and switch to OFF mode."""
        self._state.pinned_backup_id = ""
        self.set_mode(EnforcementMode.OFF)
        logger.info("Unpinned backup, enforcement OFF")
        self._persist_settings()

    def set_mode(self, mode: EnforcementMode) -> None:
        """Change the enforcement mode.

        Args:
            mode: New enforcement mode.

        Raises:
            EnforcementError: If trying to enforce without a pinned backup.
        """
        if (
            mode in (EnforcementMode.MONITOR, EnforcementMode.ENFORCE)
            and not self._state.pinned_backup_id
        ):
            raise EnforcementError(f"Cannot switch to {mode.value}: no backup pinned")
        old = self._state.mode
        self._state.mode = mode
        logger.info("Enforcement mode: %s → %s", old.value, mode.value)
        self._persist_settings()
        self._manage_task()

    def _manage_task(self) -> None:
        """Start or stop the drift check loop based on mode."""
        if self._state.mode == EnforcementMode.OFF:
            if self._task:
                self._task.cancel()
                self._task = None
        elif not self._task or self._task.done():
            self._task = asyncio.create_task(self._check_loop())

    async def start(self) -> None:
        """Start the enforcement engine."""
        logger.info(
            "EnforcementEngine started (mode=%s, interval=%ds)",
            self._state.mode.value,
            self._state.check_interval,
        )

    async def stop(self) -> None:
        """Stop the drift check loop."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("EnforcementEngine stopped")

    async def _check_loop(self) -> None:
        """Periodic drift detection loop."""
        while True:
            try:
                await self.check_drift()
            except Exception:
                logger.exception("Drift check failed")
            await asyncio.sleep(self._state.check_interval)

    async def check_drift(self) -> dict[str, Any]:
        """Run a single drift check against the pinned backup.

        Returns:
            Drift check results with changes found.

        Raises:
            EnforcementError: If backup engine or pinned backup is missing.
        """
        if not self._backup_engine:
            raise EnforcementError("Backup engine not configured")
        if not self._state.pinned_backup_id:
            raise EnforcementError("No backup pinned")

        backup = await self._backup_engine.get_backup(self._state.pinned_backup_id)
        self._state.last_check = time.time()

        # Use dry_run to detect drift
        result = await self._backup_engine._apply_state(backup, dry_run=True)
        changes: list[dict[str, str]] = result.get("changes", [])

        if not changes:
            self._last_error = ""
            return {
                "drift_detected": False,
                "changes": [],
                "action": "none",
            }

        # Drift detected
        self._state.drift_count += 1
        self._state.last_drift = time.time()

        if self._state.mode == EnforcementMode.ENFORCE:
            # Check cooldown
            cooldown = self._state.auto_restore_cooldown
            elapsed = time.time() - self._state.last_restore_at
            if cooldown > 0 and elapsed < cooldown:
                action = "logged (cooldown)"
                logger.info(
                    "Drift detected but restore on cooldown (%ds remaining)",
                    int(cooldown - elapsed),
                )
            else:
                # Auto-restore
                restore_result = await self._backup_engine._apply_state(
                    backup, dry_run=False
                )
                action = "restored"
                self._state.restore_count += 1
                self._state.last_restore_at = time.time()
                logger.warning(
                    "Drift detected and restored from %s: %d changes",
                    self._state.pinned_backup_id,
                    len(changes),
                )
        else:
            action = "logged"
            restore_result = result
            logger.warning(
                "Drift detected (monitor mode): %d changes from %s",
                len(changes),
                self._state.pinned_backup_id,
            )

        now = time.time()
        event = DriftEvent(
            detected_at=now,
            changes=changes,
            drift_summary=self._invert_changes(changes),
            change_count=len(changes),
            action_taken=action,
            backup_id=self._state.pinned_backup_id,
            last_seen=now,
        )

        # Deduplicate: if the last event has the same changes and action,
        # increment its count instead of creating a new entry.
        if self._state.history and self._is_duplicate(self._state.history[-1], event):
            prev = self._state.history[-1]
            prev.count += 1
            prev.last_seen = now
        else:
            self._state.history.append(event)
            if len(self._state.history) > self._state.max_history:
                self._state.history = self._state.history[-self._state.max_history :]

        self._last_error = ""
        return {
            "drift_detected": True,
            "changes": changes,
            "drift_summary": self._invert_changes(changes),
            "total_changes": len(changes),
            "action": action,
            "restore_result": restore_result if action == "restored" else None,
        }

    @staticmethod
    def _is_duplicate(prev: DriftEvent, current: DriftEvent) -> bool:
        """Check if two drift events represent the same drift.

        Compares backup_id, action_taken, and the sorted change list.
        """
        if prev.backup_id != current.backup_id:
            return False
        if prev.action_taken != current.action_taken:
            return False
        if prev.change_count != current.change_count:
            return False

        def _sort_key(c: dict[str, str]) -> tuple[str, str, str]:
            return (c.get("scope", ""), c.get("action", ""), c.get("detail", ""))

        return sorted(prev.changes, key=_sort_key) == sorted(
            current.changes, key=_sort_key
        )

    @staticmethod
    def _invert_changes(changes: list[dict[str, str]]) -> list[dict[str, str]]:
        """Invert restore-perspective changes to human-perspective drift.

        Restore says "reservation_added" (will add back) → drift says
        "reservation_deleted" (someone removed it).  Settings changes
        show the arrow flipped.

        Args:
            changes: Restore-perspective change list.

        Returns:
            Human-perspective drift summary.
        """
        inverse_actions: dict[str, str] = {
            "reservation_added": "reservation_deleted",
            "reservation_removed": "reservation_added",
            "created": "scope_deleted",
        }
        result: list[dict[str, str]] = []
        for c in changes:
            action = c.get("action", "")
            detail = c.get("detail", "")
            if action == "setting_changed" and " → " in detail:
                # Flip "key: actual → expected" to "key: expected → actual"
                key_part, _, arrow_part = detail.partition(": ")
                parts = arrow_part.split(" → ", 1)
                if len(parts) == 2:
                    detail = f"{key_part}: {parts[1]} → {parts[0]}"
            result.append(
                {
                    "scope": c.get("scope", ""),
                    "action": inverse_actions.get(action, action),
                    "detail": detail,
                }
            )
        return result

    async def check_health(self) -> EngineHealth:
        """Return enforcement engine health."""
        if self._last_error:
            self.health.status = EngineStatus.DEGRADED
            self.health.message = f"Last error: {self._last_error}"
        else:
            self.health.status = EngineStatus.RUNNING
            self.health.message = (
                f"Mode: {self._state.mode.value}, drifts: {self._state.drift_count}"
            )
        return self.health

    def get_metrics(self) -> dict[str, Any]:
        """Return enforcement metrics."""
        return {
            "mode": self._state.mode.value,
            "pinned_backup_id": self._state.pinned_backup_id,
            "check_interval": self._state.check_interval,
            "last_check": self._state.last_check,
            "last_drift": self._state.last_drift,
            "drift_count": self._state.drift_count,
            "restore_count": self._state.restore_count,
            "backup_on_pin": self._state.backup_on_pin,
            "auto_restore_cooldown": self._state.auto_restore_cooldown,
            "max_history": self._state.max_history,
        }
