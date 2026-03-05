"""DHCP scope sync engine.

Periodically syncs DHCP reservations and scope options from
the primary Technitium server to the standby server.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from tessera.registry import Engine, EngineHealth, EngineStatus

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
    """Periodic sync of DHCP reservations from primary to standby.

    Attributes:
        name: Engine identifier.
        version: Engine version.
        depends_on: Required engines (needs technitium clients).
    """

    name: str = "scope_sync"
    version: str = "1.0.0"
    description: str = "DHCP scope reservation sync (primary → standby)"
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
        self._primary_client: Any = None
        self._standby_client: Any = None

    def set_clients(self, primary: Any, standby: Any) -> None:
        """Set the primary and standby Technitium clients.

        Args:
            primary: TechnitiumClient for the primary server.
            standby: TechnitiumClient for the standby server.
        """
        self._primary_client = primary
        self._standby_client = standby

    async def start(self) -> None:
        """Start the periodic sync task."""
        if self._primary_client and self._standby_client:
            # Standby client is not in the engine registry, start it manually
            if hasattr(self._standby_client, "start"):
                await self._standby_client.start()
            self._task = asyncio.create_task(self._sync_loop())
            logger.info("ScopeSyncEngine started (interval=%ds)", self._sync_interval)

    async def stop(self) -> None:
        """Cancel the periodic sync task."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._standby_client and hasattr(self._standby_client, "stop"):
            await self._standby_client.stop()
        logger.info("ScopeSyncEngine stopped")

    async def _sync_loop(self) -> None:
        """Run sync on the configured interval."""
        while True:
            try:
                await self.sync_once()
            except Exception:
                logger.exception("Scope sync failed")
            await asyncio.sleep(self._sync_interval)

    async def sync_once(self) -> dict[str, Any]:
        """Run a single sync cycle.

        Returns:
            Summary of sync results.

        Raises:
            RuntimeError: If clients are not configured.
        """
        if not self._primary_client or not self._standby_client:
            msg = "Clients not configured"
            raise RuntimeError(msg)

        import time

        results: dict[str, Any] = {"scopes_synced": 0, "reservations_synced": 0}

        try:
            primary_scopes = await self._primary_client.list_scopes()

            for scope_info in primary_scopes:
                scope_name: str = scope_info.get("name", "")
                if not scope_name:
                    continue

                primary_detail = await self._primary_client.get_scope(scope_name)

                # Get standby scope for comparison
                try:
                    standby_detail = await self._standby_client.get_scope(scope_name)
                except Exception:
                    logger.warning(
                        "Scope %s not found on standby, skipping", scope_name
                    )
                    continue

                # Sync reservations: remove-before-add
                primary_reservations: list[dict[str, Any]] = primary_detail.get(
                    "reservedLeases", []
                )
                standby_reservations: list[dict[str, Any]] = standby_detail.get(
                    "reservedLeases", []
                )

                # Build MAC sets
                standby_macs = {
                    r.get("hardwareAddress", "").upper() for r in standby_reservations
                }
                primary_macs = {
                    r.get("hardwareAddress", "").upper() for r in primary_reservations
                }

                # Remove reservations not in primary
                for mac in standby_macs - primary_macs:
                    await self._standby_client.remove_reservation(
                        scope_name, hardware_address=mac
                    )

                # Add/update reservations from primary
                for reservation in primary_reservations:
                    mac = reservation.get("hardwareAddress", "").upper()
                    if mac in standby_macs:
                        # Remove first (Technitium rejects duplicate MACs)
                        await self._standby_client.remove_reservation(
                            scope_name, hardware_address=mac
                        )
                    await self._standby_client.add_reservation(
                        scope_name,
                        hardware_address=reservation.get("hardwareAddress", ""),
                        address=reservation.get("address", ""),
                        host_name=reservation.get("hostName", ""),
                        comments=reservation.get("comments", ""),
                    )
                    results["reservations_synced"] += 1

                results["scopes_synced"] += 1

            self._sync_count += 1
            self._last_sync = time.time()
            self._last_error = ""
            logger.info(
                "Sync complete: %d scopes, %d reservations",
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
