"""Hot-reloadable configuration watcher.

Polls config files for changes (mtime-based) and applies updates
atomically to running engines. Works on all platforms including
containers with bind mounts (no inotify dependency).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import time
from typing import TYPE_CHECKING, Any

from tessera.registry import Engine, EngineHealth, EngineStatus

if TYPE_CHECKING:
    from pathlib import Path

    from tessera.engines.failover import FailoverEngine
    from tessera.engines.technitium import TechnitiumPool
    from tessera.engines.voter_registry import VoterRegistryEngine

logger = logging.getLogger(__name__)


class ConfigWatcherEngine(Engine):
    """Watches config files for changes and hot-reloads them.

    Monitored files:
    - voter_keys_file: JSON mapping voter name → PSK
    - servers_file: JSON list of DhcpServer configs
    - token_file: API token (plain text)
    - reg_tokens_file: Registration token store

    Attributes:
        name: Engine identifier.
        version: Engine version.
    """

    name: str = "config_watcher"
    version: str = "1.0.0"
    description: str = "Hot-reload configuration files"

    def __init__(
        self,
        *,
        check_interval: int = 10,
        voter_keys_file: Path | None = None,
        servers_file: Path | None = None,
        token_file: Path | None = None,
        reg_tokens_file: Path | None = None,
    ) -> None:
        super().__init__()
        self._check_interval = check_interval
        self._voter_keys_file = voter_keys_file
        self._servers_file = servers_file
        self._token_file = token_file
        self._reg_tokens_file = reg_tokens_file
        self._task: asyncio.Task[None] | None = None
        self._mtimes: dict[str, float] = {}
        self._reload_count: int = 0
        self._last_reload: float = 0.0
        self._last_error: str = ""
        self._sighup_task: asyncio.Task[None] | None = None

        # Engines to notify on changes
        self._failover_engine: FailoverEngine | None = None
        self._pool: TechnitiumPool | None = None
        self._voter_registry: VoterRegistryEngine | None = None

    def set_failover_engine(self, engine: FailoverEngine) -> None:
        """Set the failover engine for voter key updates."""
        self._failover_engine = engine

    def set_pool(self, pool: TechnitiumPool) -> None:
        """Set the TechnitiumPool for server config updates."""
        self._pool = pool

    def set_voter_registry(self, registry: VoterRegistryEngine) -> None:
        """Set the voter registry for token cleanup."""
        self._voter_registry = registry

    async def start(self) -> None:
        """Start the file watcher loop and install SIGHUP handler."""
        # Record initial mtimes
        for path in self._watched_paths():
            self._mtimes[str(path)] = self._get_mtime(path)

        self._task = asyncio.create_task(self._watch_loop())

        # Install SIGHUP handler for immediate reload
        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGHUP, self._sighup_handler)
            logger.info("SIGHUP handler installed for config reload")
        except (NotImplementedError, OSError):
            # Windows or restricted environment
            logger.info("SIGHUP handler not available on this platform")

        logger.info(
            "ConfigWatcherEngine started (interval=%ds, files=%d)",
            self._check_interval,
            len(list(self._watched_paths())),
        )

    async def stop(self) -> None:
        """Stop the watcher loop."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        try:
            loop = asyncio.get_running_loop()
            loop.remove_signal_handler(signal.SIGHUP)
        except (NotImplementedError, OSError):
            pass

        logger.info("ConfigWatcherEngine stopped")

    def _sighup_handler(self) -> None:
        """Handle SIGHUP by scheduling an immediate reload."""
        logger.info("SIGHUP received — triggering config reload")
        self._sighup_task = asyncio.create_task(
            self._check_all_files()
        )

    def _watched_paths(self) -> list[Path]:
        """Return list of paths being watched."""
        paths: list[Path] = []
        if self._voter_keys_file:
            paths.append(self._voter_keys_file)
        if self._servers_file:
            paths.append(self._servers_file)
        if self._token_file:
            paths.append(self._token_file)
        if self._reg_tokens_file:
            paths.append(self._reg_tokens_file)
        return paths

    @staticmethod
    def _get_mtime(path: Path) -> float:
        """Get file modification time, or 0 if file doesn't exist."""
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    async def _watch_loop(self) -> None:
        """Poll files for changes at the configured interval."""
        while True:
            await asyncio.sleep(self._check_interval)
            try:
                await self._check_all_files()
            except Exception:
                logger.exception("Config watcher check failed")

    async def _check_all_files(self) -> None:
        """Check all watched files for changes."""
        for path in self._watched_paths():
            key = str(path)
            current_mtime = self._get_mtime(path)
            previous_mtime = self._mtimes.get(key, 0.0)

            if current_mtime != previous_mtime and current_mtime > 0:
                self._mtimes[key] = current_mtime
                await self._reload_file(path)

    async def _reload_file(self, path: Path) -> None:
        """Reload a changed file and apply updates."""
        logger.info("Config change detected: %s", path)

        try:
            if self._voter_keys_file and path == self._voter_keys_file:
                await self._reload_voter_keys(path)
            elif self._servers_file and path == self._servers_file:
                await self._reload_servers(path)
            elif self._token_file and path == self._token_file:
                self._reload_token(path)
            elif self._reg_tokens_file and path == self._reg_tokens_file:
                logger.info("Registration tokens file updated: %s", path)
                # Token file is read on-demand by voter_registry, no action needed

            self._reload_count += 1
            self._last_reload = time.time()
            self._last_error = ""
        except Exception as exc:
            self._last_error = f"Failed to reload {path.name}: {exc}"
            logger.exception("Failed to reload config file: %s", path)

    async def _reload_voter_keys(self, path: Path) -> None:
        """Reload voter keys from JSON file."""
        text = path.read_text()
        new_keys: dict[str, str] = json.loads(text)
        if not isinstance(new_keys, dict):
            logger.error("voter keys file is not a JSON object: %s", path)
            return

        if self._failover_engine:
            old_voters = set(self._failover_engine.config.get("voters", []))
            new_voters = set(new_keys.keys())
            added = new_voters - old_voters
            removed = old_voters - new_voters
            self._failover_engine.update_voter_keys(new_keys)
            if added:
                logger.info("Voters added: %s", ", ".join(sorted(added)))
            if removed:
                logger.info("Voters removed: %s", ", ".join(sorted(removed)))

    async def _reload_servers(self, path: Path) -> None:
        """Reload server configs from JSON file."""
        from tessera.config import DhcpServer

        text = path.read_text()
        raw = json.loads(text)
        if not isinstance(raw, list):
            logger.error("servers file is not a JSON array: %s", path)
            return

        servers = [DhcpServer(**s) for s in raw]
        if self._pool:
            changes = self._pool.update_servers(servers)
            for change in changes:
                logger.info("Server pool: %s", change)
            # Start any newly added clients
            for server in servers:
                client = self._pool.get_client(server.name)
                if client and client._client is None:
                    await client.start()

    def _reload_token(self, path: Path) -> None:
        """Reload API token from file."""
        new_token = path.read_text().strip()
        if self._pool:
            for client in self._pool.get_all():
                client._token = new_token
            logger.info("API token updated for %d clients", len(self._pool.get_all()))

    async def check_health(self) -> EngineHealth:
        """Return watcher health."""
        if self._last_error:
            self.health.status = EngineStatus.DEGRADED
            self.health.message = self._last_error
        else:
            self.health.status = EngineStatus.RUNNING
            self.health.message = f"{self._reload_count} reloads"
        return self.health

    def get_metrics(self) -> dict[str, Any]:
        """Return watcher metrics."""
        return {
            "reload_count": self._reload_count,
            "last_reload": self._last_reload,
            "check_interval": self._check_interval,
            "watched_files": len(list(self._watched_paths())),
            "last_error": self._last_error,
        }
