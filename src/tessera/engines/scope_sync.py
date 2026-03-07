"""DHCP scope sync engine.

Periodically syncs DHCP reservations and scope options from
the active Technitium server to all candidate servers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from tessera.exceptions import ScopeSyncError
from tessera.registry import Engine, EngineHealth, EngineStatus

if TYPE_CHECKING:
    from tessera.engines.technitium import TechnitiumPool

logger = logging.getLogger(__name__)

# Fields that differ between active and candidate servers
SERVER_SPECIFIC_FIELDS = frozenset(
    {
        "serverAddress",
        "serverHostName",
        "enabled",
    }
)


class ScopeSyncEngine(Engine):
    """Periodic sync of DHCP reservations from active to all candidates.

    Attributes:
        name: Engine identifier.
        version: Engine version.
        depends_on: Required engines (needs technitium clients).
    """

    name: str = "scope_sync"
    version: str = "1.0.0"
    description: str = "DHCP scope reservation sync (active → candidates)"
    depends_on: tuple[str, ...] = ("technitium",)

    def __init__(
        self,
        *,
        sync_interval: int = 300,
    ) -> None:
        super().__init__()
        self._sync_interval = sync_interval
        self._task: asyncio.Task[None] | None = None
        self._last_sync: float = 0.0
        self._sync_count: int = 0
        self._last_error: str = ""
        self._pool: TechnitiumPool | None = None
        self._last_sync_status: dict[str, str] = {}
        # Legacy single-client mode
        self._active_client: Any = None
        self._candidate_client: Any = None

    @property
    def sync_status(self) -> dict[str, str]:
        """Return last sync result per candidate."""
        return dict(self._last_sync_status)

    def set_pool(self, pool: TechnitiumPool) -> None:
        """Set the TechnitiumPool for multi-server sync.

        Args:
            pool: The pool managing all DHCP servers.
        """
        self._pool = pool

    def set_clients(self, active: Any, candidate: Any) -> None:
        """Set the active and candidate Technitium clients (legacy).

        Args:
            active: TechnitiumClient for the active server.
            candidate: TechnitiumClient for the candidate server.
        """
        self._active_client = active
        self._candidate_client = candidate

    async def start(self) -> None:
        """Start the periodic sync task."""
        has_pool = self._pool is not None
        has_legacy = self._active_client and self._candidate_client
        if has_pool or has_legacy:
            if (
                not has_pool
                and self._candidate_client
                and hasattr(self._candidate_client, "start")
            ):
                await self._candidate_client.start()
            self._task = asyncio.create_task(self._sync_loop())
            logger.info("ScopeSyncEngine started (interval=%ds)", self._sync_interval)

    async def stop(self) -> None:
        """Cancel the periodic sync task."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._candidate_client and hasattr(self._candidate_client, "stop"):
            await self._candidate_client.stop()
        logger.info("ScopeSyncEngine stopped")

    async def _sync_loop(self) -> None:
        """Run sync on the configured interval."""
        while True:
            try:
                await self.sync_once()
            except Exception:
                logger.exception("Scope sync failed")
            await asyncio.sleep(self._sync_interval)

    def _get_active(self) -> Any:
        """Get the active client from pool or legacy."""
        if self._pool:
            return self._pool.get_active()
        return self._active_client

    def _get_candidates(self) -> list[Any]:
        """Get all candidate clients from pool or legacy."""
        if self._pool:
            return self._pool.get_candidates()
        if self._candidate_client:
            return [self._candidate_client]
        return []

    async def _sync_to_candidate(
        self, active_client: Any, candidate_client: Any
    ) -> dict[str, int]:
        """Sync all scopes from active to a single candidate.

        Returns:
            Dict with scopes_synced and reservations_synced counts.
        """
        result = {"scopes_synced": 0, "reservations_synced": 0}
        active_scopes = await active_client.list_scopes()

        for scope_info in active_scopes:
            scope_name: str = scope_info.get("name", "")
            if not scope_name:
                continue

            active_detail = await active_client.get_scope(scope_name)

            try:
                candidate_detail = await candidate_client.get_scope(scope_name)
            except Exception:
                sname = getattr(candidate_client, "server_name", "unknown")
                logger.warning(
                    "Scope %s not found on candidate %s, skipping",
                    scope_name,
                    sname,
                )
                continue

            active_reservations: list[dict[str, Any]] = active_detail.get(
                "reservedLeases", []
            )
            candidate_reservations: list[dict[str, Any]] = candidate_detail.get(
                "reservedLeases", []
            )

            candidate_macs = {
                r.get("hardwareAddress", "").upper() for r in candidate_reservations
            }
            active_macs = {
                r.get("hardwareAddress", "").upper() for r in active_reservations
            }

            for mac in candidate_macs - active_macs:
                await candidate_client.remove_reservation(
                    scope_name, hardware_address=mac
                )

            for reservation in active_reservations:
                mac = reservation.get("hardwareAddress", "").upper()
                if mac in candidate_macs:
                    await candidate_client.remove_reservation(
                        scope_name, hardware_address=mac
                    )
                await candidate_client.add_reservation(
                    scope_name,
                    hardware_address=reservation.get("hardwareAddress", ""),
                    address=reservation.get("address", ""),
                    host_name=reservation.get("hostName", ""),
                    comments=reservation.get("comments", ""),
                )
                result["reservations_synced"] += 1

            result["scopes_synced"] += 1

        return result

    async def sync_once(self) -> dict[str, Any]:
        """Run a single sync cycle to all candidates.

        Returns:
            Summary of sync results including per-candidate status.

        Raises:
            ScopeSyncError: If clients are not configured.
        """
        active = self._get_active()
        candidates = self._get_candidates()

        if not active or not candidates:
            msg = "Active or candidate clients not configured"
            raise ScopeSyncError(msg)

        import time

        results: dict[str, Any] = {
            "scopes_synced": 0,
            "reservations_synced": 0,
            "servers_synced": 0,
        }
        candidate_results: dict[str, str] = {}
        any_failed = False

        for candidate in candidates:
            sname = getattr(candidate, "server_name", "unknown")
            try:
                sub = await self._sync_to_candidate(active, candidate)
                results["scopes_synced"] += sub["scopes_synced"]
                results["reservations_synced"] += sub["reservations_synced"]
                results["servers_synced"] += 1
                candidate_results[sname] = "success"
                logger.info(
                    "Synced to %s: %d scopes, %d reservations",
                    sname,
                    sub["scopes_synced"],
                    sub["reservations_synced"],
                )
            except Exception as exc:
                candidate_results[sname] = f"error: {exc}"
                any_failed = True
                logger.exception("Sync to %s failed", sname)

        self._last_sync_status = candidate_results
        self._sync_count += 1
        self._last_sync = time.time()

        if any_failed:
            self._last_error = "Partial sync failure"
            self.health.status = EngineStatus.DEGRADED
            self.health.message = "Sync partial failure: " + ", ".join(
                f"{k}={v}" for k, v in candidate_results.items() if v != "success"
            )
        else:
            self._last_error = ""

        logger.info(
            "Sync complete: %d servers, %d scopes, %d reservations",
            results["servers_synced"],
            results["scopes_synced"],
            results["reservations_synced"],
        )
        results["candidate_results"] = candidate_results
        return results

    async def check_health(self) -> EngineHealth:
        """Return sync engine health."""
        if self._last_error:
            self.health.status = EngineStatus.DEGRADED
            self.health.message = f"Last error: {self._last_error}"
        else:
            self.health.status = EngineStatus.RUNNING
            self.health.message = f"Synced {self._sync_count} times"
        return self.health

    def get_metrics(self) -> dict[str, Any]:
        """Return sync metrics."""
        return {
            "sync_count": self._sync_count,
            "last_sync": self._last_sync,
            "last_error": self._last_error,
            "sync_interval": self._sync_interval,
        }
