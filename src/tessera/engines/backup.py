"""DHCP state backup engine.

Captures full DHCP configuration snapshots (scope settings + reservations)
from the active Technitium server and persists them as timestamped JSON
files.  Supports manual and cron-scheduled automatic backups with
configurable retention.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from croniter import croniter

from tessera.exceptions import AppError, NotFoundError
from tessera.registry import Engine, EngineHealth, EngineStatus

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class BackupError(AppError):
    """A backup operation failed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ScopeSnapshot:
    """Point-in-time capture of a single DHCP scope.

    Attributes:
        name: Scope name.
        enabled: Whether the scope is currently enabled.
        settings: Full scope settings dict from Technitium.
        reservations: List of reserved leases.
    """

    name: str
    enabled: bool
    settings: dict[str, Any]
    reservations: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Metadata and contents of a full DHCP backup.

    Attributes:
        backup_id: Unique identifier (timestamp-based).
        created_at: Unix timestamp of creation.
        source: Server URL that was backed up.
        description: Human-readable description.
        scope_count: Number of scopes in the backup.
        reservation_count: Total reservations across all scopes.
    """

    backup_id: str
    created_at: float
    source: str
    description: str
    scope_count: int
    reservation_count: int


@dataclass(slots=True)
class BackupData:
    """Full backup payload written to disk.

    Attributes:
        manifest: Backup metadata.
        scopes: List of scope snapshots.
    """

    manifest: BackupManifest
    scopes: list[ScopeSnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "manifest": asdict(self.manifest),
            "scopes": [asdict(s) for s in self.scopes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupData:
        """Deserialize from a dict (loaded from JSON)."""
        manifest = BackupManifest(**data["manifest"])
        scopes = [ScopeSnapshot(**s) for s in data.get("scopes", [])]
        return cls(manifest=manifest, scopes=scopes)


class BackupEngine(Engine):
    """DHCP state backup engine with retention management.

    Attributes:
        name: Engine identifier.
        version: Engine version.
        depends_on: Required engines.
    """

    name: str = "backup"
    version: str = "1.0.0"
    description: str = "DHCP state backup and restore"
    depends_on: tuple[str, ...] = ("technitium",)

    def __init__(
        self,
        *,
        backup_dir: Path,
        max_backups: int = 50,
        auto_interval: int = 0,
        cron_schedule: str = "",
    ) -> None:
        super().__init__()
        self._backup_dir = backup_dir
        self._max_backups = max_backups
        self._auto_interval = auto_interval  # legacy, ignored if cron set
        self._cron_schedule = cron_schedule
        self._auto_enabled = bool(cron_schedule) or auto_interval > 0
        self._active_client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._last_backup: float = 0.0
        self._backup_count: int = 0
        self._last_error: str = ""
        self._next_run: float = 0.0

        # Validate cron if provided
        if cron_schedule and not croniter.is_valid(cron_schedule):
            raise BackupError(f"Invalid cron expression: {cron_schedule}")

    def set_active_client(self, client: Any) -> None:
        """Set the active Technitium client for snapshotting."""
        self._active_client = client

    @property
    def backup_dir(self) -> Path:
        """Return the backup directory path."""
        return self._backup_dir

    @property
    def auto_enabled(self) -> bool:
        """Return whether automatic backups are enabled."""
        return self._auto_enabled

    @property
    def cron_schedule(self) -> str:
        """Return the cron schedule expression."""
        return self._cron_schedule

    @property
    def auto_interval(self) -> int:
        """Return the legacy auto-backup interval in seconds."""
        return self._auto_interval

    @property
    def max_backups(self) -> int:
        """Return the maximum number of backups to retain."""
        return self._max_backups

    @property
    def next_run(self) -> float:
        """Return the next scheduled backup as unix timestamp."""
        return self._next_run

    def _compute_next_run(self) -> float:
        """Compute the next backup time from cron or interval."""
        now = datetime.now(tz=UTC)
        if self._cron_schedule:
            cron = croniter(self._cron_schedule, now)
            next_dt: float = cron.get_next(float)
            return next_dt
        if self._auto_interval > 0:
            return time.time() + self._auto_interval
        return 0.0

    def update_settings(
        self,
        *,
        auto_enabled: bool | None = None,
        cron_schedule: str | None = None,
        max_backups: int | None = None,
    ) -> None:
        """Update runtime backup settings.

        Args:
            auto_enabled: Enable or disable automatic backups.
            cron_schedule: Cron expression for backup schedule.
            max_backups: Maximum backup retention count.
        """
        if max_backups is not None:
            if max_backups < 1:
                raise BackupError("max_backups must be >= 1")
            self._max_backups = max_backups
            self._enforce_retention_sync()

        if cron_schedule is not None:
            if cron_schedule and not croniter.is_valid(cron_schedule):
                raise BackupError(f"Invalid cron expression: {cron_schedule}")
            self._cron_schedule = cron_schedule
            # Clear legacy interval when using cron
            if cron_schedule:
                self._auto_interval = 0

        if auto_enabled is not None:
            self._auto_enabled = auto_enabled

        self._manage_auto_task()
        logger.info(
            "Backup settings updated: auto=%s, cron='%s', max=%d",
            self._auto_enabled,
            self._cron_schedule,
            self._max_backups,
        )

    def _manage_auto_task(self) -> None:
        """Start or stop the auto-backup loop based on settings."""
        if self._auto_enabled and (self._cron_schedule or self._auto_interval > 0):
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = asyncio.create_task(self._auto_backup_loop())
        elif self._task:
            self._task.cancel()
            self._task = None
            self._next_run = 0.0

    async def start(self) -> None:
        """Create backup directory and start auto-backup if configured."""
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._manage_auto_task()
        logger.info(
            "BackupEngine started (dir=%s, max=%d, auto=%s, cron='%s')",
            self._backup_dir,
            self._max_backups,
            self._auto_enabled,
            self._cron_schedule,
        )

    async def stop(self) -> None:
        """Cancel the auto-backup task."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("BackupEngine stopped")

    async def _auto_backup_loop(self) -> None:
        """Run automatic backups on the configured cron schedule or interval."""
        while True:
            self._next_run = self._compute_next_run()
            sleep_for = max(1.0, self._next_run - time.time())
            logger.debug("Next auto-backup in %.0fs", sleep_for)
            await asyncio.sleep(sleep_for)
            try:
                await self.create_backup(description="Automatic backup")
            except Exception:
                logger.exception("Auto-backup failed")

    async def create_backup(
        self,
        description: str = "",
    ) -> BackupManifest:
        """Capture a full DHCP state snapshot from the active server.

        Args:
            description: Human-readable description for the backup.

        Returns:
            The backup manifest.

        Raises:
            BackupError: If the active client is not configured or timeout.
        """
        if not self._active_client:
            raise BackupError("Primary client not configured")

        try:
            return await asyncio.wait_for(
                self._do_create_backup(description), timeout=300.0
            )
        except TimeoutError as exc:
            self._last_error = "Backup timed out after 300s"
            raise BackupError(self._last_error) from exc

    async def _do_create_backup(self, description: str) -> BackupManifest:
        """Inner coroutine for create_backup (wrapped by wait_for)."""
        now = time.time()
        backup_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))

        scopes_list = await self._active_client.list_scopes()
        scope_snapshots: list[ScopeSnapshot] = []
        total_reservations = 0

        for scope_info in scopes_list:
            scope_name: str = scope_info.get("name", "")
            if not scope_name:
                continue
            detail = await self._active_client.get_scope(scope_name)
            # Copy to avoid mutating cached/mock data
            detail = dict(detail)
            reservations: list[dict[str, Any]] = detail.pop("reservedLeases", [])
            total_reservations += len(reservations)
            scope_snapshots.append(
                ScopeSnapshot(
                    name=scope_name,
                    enabled=scope_info.get("enabled", False),
                    settings=detail,
                    reservations=reservations,
                )
            )

        manifest = BackupManifest(
            backup_id=backup_id,
            created_at=now,
            source=str(getattr(self._active_client, "_base_url", "unknown")),
            description=description or f"Manual backup ({len(scope_snapshots)} scopes)",
            scope_count=len(scope_snapshots),
            reservation_count=total_reservations,
        )

        backup = BackupData(manifest=manifest, scopes=scope_snapshots)
        filepath = self._backup_dir / f"{backup_id}.json"
        content = json.dumps(backup.to_dict(), indent=2)
        await asyncio.to_thread(filepath.write_text, content)

        self._last_backup = now
        self._backup_count += 1
        self._last_error = ""
        await self._enforce_retention()

        logger.info(
            "Backup created: %s (%d scopes, %d reservations)",
            backup_id,
            len(scope_snapshots),
            total_reservations,
        )
        return manifest

    async def list_backups(self) -> list[BackupManifest]:
        """List all stored backups, newest first.

        Returns:
            List of backup manifests sorted by creation time descending.
        """
        return await asyncio.to_thread(self._list_backups_sync)

    def _list_backups_sync(self) -> list[BackupManifest]:
        """Synchronous implementation of list_backups."""
        manifests: list[BackupManifest] = []
        for filepath in sorted(self._backup_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(filepath.read_text())
                manifests.append(BackupManifest(**data["manifest"]))
            except (json.JSONDecodeError, KeyError):
                logger.warning("Skipping corrupt backup: %s", filepath.name)
        return manifests

    async def get_backup(self, backup_id: str) -> BackupData:
        """Load a specific backup by ID.

        Args:
            backup_id: The backup identifier.

        Returns:
            Full backup data including scope snapshots.

        Raises:
            NotFoundError: If the backup does not exist.
        """
        filepath = self._backup_dir / f"{backup_id}.json"
        if not filepath.is_file():
            raise NotFoundError("Backup", backup_id)
        text = await asyncio.to_thread(filepath.read_text)
        data = json.loads(text)
        return BackupData.from_dict(data)

    def delete_backup(self, backup_id: str) -> None:
        """Delete a stored backup.

        Args:
            backup_id: The backup identifier.

        Raises:
            NotFoundError: If the backup does not exist.
        """
        filepath = self._backup_dir / f"{backup_id}.json"
        if not filepath.is_file():
            raise NotFoundError("Backup", backup_id)
        filepath.unlink()
        logger.info("Backup deleted: %s", backup_id)

    async def restore_backup(
        self,
        backup_id: str,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Restore DHCP state from a backup to the active server.

        Args:
            backup_id: The backup to restore from.
            dry_run: If True, compute changes without applying them.

        Returns:
            Summary of restore operations.

        Raises:
            BackupError: If the active client is not configured.
            NotFoundError: If the backup does not exist.
        """
        if not self._active_client:
            raise BackupError("Primary client not configured")

        backup = await self.get_backup(backup_id)
        return await self._apply_state(backup, dry_run=dry_run)

    async def _apply_state(
        self,
        backup: BackupData,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply a backup state to the active server.

        Compares current live state with the backup and applies the
        minimal set of changes needed to converge.

        Args:
            backup: The backup data to apply.
            dry_run: If True, only compute the diff.

        Returns:
            Summary of changes (applied or planned).
        """
        changes: list[dict[str, str]] = []

        for scope_snap in backup.scopes:
            # Get current state for comparison
            try:
                current_detail = await self._active_client.get_scope(scope_snap.name)
                current_detail = dict(current_detail)
            except Exception:
                # Scope doesn't exist — recreate it
                if not dry_run:
                    await self._active_client.set_scope(
                        scope_snap.name, scope_snap.settings
                    )
                changes.append(
                    {"scope": scope_snap.name, "action": "created", "detail": ""}
                )
                # Add all reservations
                for res in scope_snap.reservations:
                    if not dry_run:
                        await self._active_client.add_reservation(
                            scope_snap.name,
                            hardware_address=res.get("hardwareAddress", ""),
                            address=res.get("address", ""),
                            host_name=res.get("hostName", ""),
                            comments=res.get("comments", ""),
                        )
                    changes.append(
                        {
                            "scope": scope_snap.name,
                            "action": "reservation_added",
                            "detail": res.get("hardwareAddress", ""),
                        }
                    )
                continue

            # Compare settings (skip server-specific fields)
            current_reservations: list[dict[str, Any]] = current_detail.pop(
                "reservedLeases", []
            )
            setting_changes = self._diff_settings(scope_snap.settings, current_detail)
            if setting_changes:
                if not dry_run:
                    await self._active_client.set_scope(
                        scope_snap.name, scope_snap.settings
                    )
                for key, diff in setting_changes.items():
                    changes.append(
                        {
                            "scope": scope_snap.name,
                            "action": "setting_changed",
                            "detail": f"{key}: {diff['actual']} → {diff['expected']}",
                        }
                    )

            # Compare reservations
            res_changes = self._diff_reservations(
                scope_snap.reservations, current_reservations
            )
            for rc in res_changes:
                if not dry_run:
                    if rc["action"] == "reservation_added":
                        # Find the reservation data
                        for res in scope_snap.reservations:
                            if (
                                res.get("hardwareAddress", "").upper()
                                == rc["detail"].upper()
                            ):
                                await self._active_client.add_reservation(
                                    scope_snap.name,
                                    hardware_address=res.get("hardwareAddress", ""),
                                    address=res.get("address", ""),
                                    host_name=res.get("hostName", ""),
                                    comments=res.get("comments", ""),
                                )
                                break
                    elif rc["action"] == "reservation_removed":
                        await self._active_client.remove_reservation(
                            scope_snap.name, hardware_address=rc["detail"]
                        )
                changes.append({"scope": scope_snap.name, **rc})

        return {
            "backup_id": backup.manifest.backup_id,
            "dry_run": dry_run,
            "changes": changes,
            "total_changes": len(changes),
        }

    @staticmethod
    def _diff_settings(
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Compare scope settings and return differences.

        Args:
            expected: Settings from backup.
            actual: Current live settings.

        Returns:
            Dict of field name → {expected, actual} for changed fields.
        """
        # Skip volatile/server-specific fields
        skip_fields = frozenset(
            {
                "serverAddress",
                "interfaceAddress",
                "lastModified",
            }
        )
        diffs: dict[str, dict[str, Any]] = {}
        for key, expected_val in expected.items():
            if key in skip_fields:
                continue
            actual_val = actual.get(key)
            if actual_val != expected_val:
                diffs[key] = {"expected": expected_val, "actual": actual_val}
        return diffs

    @staticmethod
    def _diff_reservations(
        expected: list[dict[str, Any]],
        actual: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Compare reservation lists and return changes needed.

        Args:
            expected: Reservations from backup.
            actual: Current live reservations.

        Returns:
            List of change dicts with action and detail.
        """
        expected_macs = {r.get("hardwareAddress", "").upper() for r in expected}
        actual_macs = {r.get("hardwareAddress", "").upper() for r in actual}
        changes: list[dict[str, str]] = []

        for mac in expected_macs - actual_macs:
            changes.append({"action": "reservation_added", "detail": mac})
        for mac in actual_macs - expected_macs:
            changes.append({"action": "reservation_removed", "detail": mac})
        return changes

    async def _enforce_retention(self) -> None:
        """Delete oldest backups exceeding the retention limit."""
        await asyncio.to_thread(self._enforce_retention_sync)

    def _enforce_retention_sync(self) -> None:
        """Synchronous implementation of retention enforcement."""
        backups = sorted(self._backup_dir.glob("*.json"))
        while len(backups) > self._max_backups:
            oldest = backups.pop(0)
            oldest.unlink()
            logger.info("Retention: deleted old backup %s", oldest.name)

    async def check_health(self) -> EngineHealth:
        """Return backup engine health."""
        if self._last_error:
            self.health.status = EngineStatus.DEGRADED
            self.health.message = f"Last error: {self._last_error}"
        else:
            self.health.status = EngineStatus.RUNNING
            self.health.message = f"{self._backup_count} backups created"
        return self.health

    def get_metrics(self) -> dict[str, Any]:
        """Return backup metrics."""
        return {
            "backup_count": self._backup_count,
            "last_backup": self._last_backup,
            "stored_backups": len(list(self._backup_dir.glob("*.json"))),
            "max_backups": self._max_backups,
            "auto_enabled": self._auto_enabled,
            "cron_schedule": self._cron_schedule,
            "next_run": self._next_run,
        }
