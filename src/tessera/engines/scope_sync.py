"""DHCP scope sync engine.

Periodically syncs DHCP reservations and scope options from
the primary Technitium server to all standby servers.
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

# Fields that differ between primary and standby servers
SERVER_SPECIFIC_FIELDS = frozenset(
    {
        "serverAddress",
        "serverHostName",
        "enabled",
    }
)


class ScopeSyncEngine(Engine):
    """Periodic sync of DHCP reservations from primary to all standbys.

    Attributes:
        name: Engine identifier.
        version: Engine version.
        depends_on: Required engines (needs technitium clients).
    """

    name: str = "scope_sync"
    version: str = "1.0.0"
    description: str = "DHCP scope reservation sync (primary → standbys)"
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
        # Legacy single-client mode
        self._active_client: Any = None
        self._candidate_client: Any = None

    def set_pool(self, pool: TechnitiumPool) -> None:
        """Set the TechnitiumPool for multi-server sync.

        Args:
            pool: The pool managing all DHCP servers.
        """
        self._pool = pool

    def set_clients(self, primary: Any, standby: Any) -> None:
        """Set the primary and standby Technitium clients (legacy).

        Args:
            primary: TechnitiumClient for the primary server.
            standby: TechnitiumClient for the standby server.
        """
        self._active_client = primary
        self._candidate_client = standby

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
        """Get the primary client from pool or legacy."""
        if self._pool:
            return self._pool.get_active()
        return self._active_client

    def _get_candidates(self) -> list[Any]:
        """Get all standby clients from pool or legacy."""
        if self._pool:
            return self._pool.get_candidates()
        if self._candidate_client:
            return [self._candidate_client]
        return []

    async def _sync_to_standby(
        self, active_client: Any, standby: Any
    ) -> dict[str, int]:
        """Sync all scopes from primary to a single standby.

        Returns:
            Dict with scopes_synced and reservations_synced counts.
        """
        result = {"scopes_synced": 0, "reservations_synced": 0}
        primary_scopes = await active_client.list_scopes()

        for scope_info in primary_scopes:
            scope_name: str = scope_info.get("name", "")
            if not scope_name:
                continue

            primary_detail = await active_client.get_scope(scope_name)

            try:
                standby_detail = await standby.get_scope(scope_name)
            except Exception:
                sname = getattr(standby, "server_name", "unknown")
                logger.warning(
                    "Scope %s not found on standby %s, skipping",
                    scope_name,
                    sname,
                )
                continue

            primary_reservations: list[dict[str, Any]] = primary_detail.get(
                "reservedLeases", []
            )
            standby_reservations: list[dict[str, Any]] = standby_detail.get(
                "reservedLeases", []
            )

            standby_macs = {
                r.get("hardwareAddress", "").upper() for r in standby_reservations
            }
            primary_macs = {
                r.get("hardwareAddress", "").upper() for r in primary_reservations
            }

            for mac in standby_macs - primary_macs:
                await standby.remove_reservation(
                    scope_name, hardware_address=mac
                )

            for reservation in primary_reservations:
                mac = reservation.get("hardwareAddress", "").upper()
                if mac in standby_macs:
                    await standby.remove_reservation(
                        scope_name, hardware_address=mac
                    )
                await standby.add_reservation(
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
        """Run a single sync cycle to all standbys.

        Returns:
            Summary of sync results.

        Raises:
            ScopeSyncError: If clients are not configured.
        """
        primary = self._get_active()
        standbys = self._get_candidates()

        if not primary or not standbys:
            msg = "Primary or standby clients not configured"
            raise ScopeSyncError(msg)

        import time

        results: dict[str, Any] = {
            "scopes_synced": 0,
            "reservations_synced": 0,
            "servers_synced": 0,
        }

        try:
            for standby in standbys:
                sname = getattr(standby, "server_name", "unknown")
                try:
                    sub = await self._sync_to_standby(primary, standby)
                    results["scopes_synced"] += sub["scopes_synced"]
                    results["reservations_synced"] += sub["reservations_synced"]
                    results["servers_synced"] += 1
                    logger.info(
                        "Synced to %s: %d scopes, %d reservations",
                        sname,
                        sub["scopes_synced"],
                        sub["reservations_synced"],
                    )
                except Exception:
                    logger.exception("Sync to %s failed", sname)

            self._sync_count += 1
            self._last_sync = time.time()
            self._last_error = ""
            logger.info(
                "Sync complete: %d servers, %d scopes, %d reservations",
                results["servers_synced"],
                results["scopes_synced"],
                results["reservations_synced"],
            )
        except Exception as exc:
            self._last_error = str(exc)
            raise

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
