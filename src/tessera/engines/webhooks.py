"""Webhook notification engine.

Fires HTTP POST requests to configured URLs on state transitions:
failover, failback, voter registration, drift detected, backup failure, etc.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from tessera.registry import Engine, EngineHealth, EngineStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_MAX_QUEUE = 500


class WebhookEngine(Engine):
    """Fire-and-forget webhook notifications.

    Configure with ``TESSERA_WEBHOOK_URLS`` (comma-separated).
    Events are queued and dispatched asynchronously — webhook failures
    never block the main application.
    """

    name = "webhooks"
    version = "1.0.0"
    description = "Webhook notifications for state transitions"

    def __init__(
        self,
        *,
        urls: Sequence[str] = (),
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        super().__init__()
        self._urls = list(urls)
        self._timeout = timeout
        self._max_retries = max_retries
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=_DEFAULT_MAX_QUEUE
        )
        self._task: asyncio.Task[None] | None = None
        self._total_sent: int = 0
        self._total_failed: int = 0
        self._last_error: str = ""

    async def start(self) -> None:
        """Start the dispatch worker."""
        if self._urls:
            self._task = asyncio.create_task(self._dispatch_loop())
            self.health.status = EngineStatus.RUNNING
            self.health.message = f"Active ({len(self._urls)} endpoints)"
            logger.info("WebhookEngine started (%d URLs configured)", len(self._urls))
        else:
            self.health.status = EngineStatus.RUNNING
            self.health.message = "No webhook URLs configured"
            logger.info("WebhookEngine started (no URLs — notifications disabled)")

    async def stop(self) -> None:
        """Stop the dispatch worker."""
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.health.status = EngineStatus.STOPPED
        self.health.message = "Stopped"

    def notify(
        self,
        event: str,
        *,
        detail: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        """Queue a webhook notification.

        Args:
            event: Event type (e.g. ``failover.triggered``, ``drift.detected``).
            detail: Human-readable summary.
            data: Arbitrary JSON-serialisable payload.
        """
        if not self._urls:
            return
        payload: dict[str, Any] = {
            "event": event,
            "timestamp": time.time(),
            "detail": detail,
        }
        if data:
            payload["data"] = data
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("Webhook queue full — dropping event: %s", event)

    def get_metrics(self) -> dict[str, Any]:
        """Return webhook metrics."""
        return {
            "configured_urls": len(self._urls),
            "total_sent": self._total_sent,
            "total_failed": self._total_failed,
            "queue_size": self._queue.qsize(),
            "last_error": self._last_error,
        }

    async def check_health(self) -> EngineHealth:
        """Return health status."""
        return self.health

    async def _dispatch_loop(self) -> None:
        """Worker loop — drains queue and POSTs to all URLs."""
        import httpx

        while True:
            try:
                payload = await self._queue.get()
            except asyncio.CancelledError:
                return

            body = json.dumps(payload, separators=(",", ":"))
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Tessera-Webhook/1.0",
            }

            for url in self._urls:
                success = False
                for attempt in range(1, self._max_retries + 2):
                    try:
                        async with httpx.AsyncClient(timeout=self._timeout) as client:
                            resp = await client.post(url, content=body, headers=headers)
                        if resp.status_code < 400:
                            success = True
                            break
                        logger.warning(
                            "Webhook %s returned %d (attempt %d)",
                            url,
                            resp.status_code,
                            attempt,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Webhook %s failed (attempt %d): %s",
                            url,
                            attempt,
                            exc,
                        )
                    # Exponential backoff between retries
                    if attempt <= self._max_retries:
                        await asyncio.sleep(2**attempt)

                if success:
                    self._total_sent += 1
                else:
                    self._total_failed += 1
                    self._last_error = f"Failed to deliver to {url}"
                    logger.error(
                        "Webhook delivery failed after %d attempts: %s",
                        self._max_retries + 1,
                        url,
                    )
