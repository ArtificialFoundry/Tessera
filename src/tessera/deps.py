"""FastAPI dependency injection providers.

All shared state is injected via ``Depends()`` so that tests can
override them cleanly without ``unittest.mock.patch``.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from tessera.config import Settings
from tessera.engines.backup import BackupEngine
from tessera.engines.config_watcher import ConfigWatcherEngine
from tessera.engines.enforcement import EnforcementEngine
from tessera.engines.failover import FailoverEngine
from tessera.engines.scope_sync import ScopeSyncEngine
from tessera.engines.technitium import TechnitiumClient, TechnitiumPool
from tessera.engines.voter_registry import VoterRegistryEngine
from tessera.exceptions import AppError
from tessera.registry import EngineRegistry, ModuleRegistry
from tessera.settings_store import SettingsStore

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton application settings."""
    return Settings()


@lru_cache(maxsize=1)
def get_engine_registry() -> EngineRegistry:
    """Return the singleton engine registry, pre-loaded with engines."""
    settings = get_settings()
    registry = EngineRegistry()

    # Load API token
    token = ""
    try:
        token = settings.api_token_file.read_text().strip()
    except FileNotFoundError:
        logger.warning("API token file not found: %s", settings.api_token_file)

    # Load voter keys
    voter_keys: dict[str, str] = {}
    try:
        voter_keys = json.loads(settings.voter_keys_file.read_text())
    except FileNotFoundError:
        logger.warning("Voter keys file not found: %s", settings.voter_keys_file)

    # Create multi-server pool
    servers = settings.get_servers()
    pool = TechnitiumPool.from_servers(
        servers, token, ca_cert_file=settings.ca_cert_file,
    )

    # Register the active client as the "technitium" engine
    active_client = pool.get_active()
    registry.register(active_client)

    # Failover engine
    failover = FailoverEngine(
        quorum=settings.quorum,
        failover_rounds=settings.failover_rounds,
        failback_rounds=settings.failback_rounds,
        vote_ttl=settings.vote_ttl,
        voter_keys=voter_keys,
    )
    failover.set_pool(pool)
    registry.register(failover)

    # Scope sync engine (syncs to ALL candidates)
    scope_sync = ScopeSyncEngine(sync_interval=settings.sync_interval)
    scope_sync.set_pool(pool)
    registry.register(scope_sync)

    # Persistent settings store (survives rebuilds)
    settings_store = SettingsStore(settings.backup_dir / "engine-settings.json")

    # Backup engine
    backup = BackupEngine(
        backup_dir=settings.backup_dir,
        max_backups=settings.max_backups,
        auto_interval=settings.auto_backup_interval,
        cron_schedule=settings.backup_cron_schedule,
        settings_store=settings_store,
    )
    backup.set_active_client(active_client)
    registry.register(backup)

    # Enforcement engine
    enforcement = EnforcementEngine(
        check_interval=settings.enforcement_interval,
        settings_store=settings_store,
    )
    enforcement.set_backup_engine(backup)
    registry.register(enforcement)

    # Voter registry engine
    reg_tokens_file = Path("/var/lib/tessera/reg-tokens.json")
    voter_registry = VoterRegistryEngine(
        voter_keys_file=settings.voter_keys_file,
        voter_registry_file=settings.voter_registry_file,
        reg_tokens_file=reg_tokens_file,
        static_registration_token=settings.get_registration_token(),
        auto_approve=settings.auto_approve_voters,
        token_ttl=getattr(settings, "registration_token_ttl", 3600),
        psk_grace_period=getattr(settings, "psk_grace_period", 60),
    )
    voter_registry.set_on_keys_changed(failover.update_voter_keys)
    failover.set_voter_registry(voter_registry)
    registry.register(voter_registry)

    # Config watcher engine
    config_watcher = ConfigWatcherEngine(
        check_interval=settings.config_reload_interval,
        voter_keys_file=settings.voter_keys_file,
        servers_file=settings.servers_file if settings.servers_file.is_file() else None,
        token_file=settings.api_token_file,
        reg_tokens_file=reg_tokens_file,
    )
    config_watcher.set_failover_engine(failover)
    config_watcher.set_pool(pool)
    config_watcher.set_voter_registry(voter_registry)
    registry.register(config_watcher)

    return registry


def get_failover_engine() -> FailoverEngine:
    """Return the failover engine from the registry."""
    engine = get_engine_registry().get("failover")
    if not isinstance(engine, FailoverEngine):
        raise AppError("Expected FailoverEngine")
    return engine


def get_technitium_client() -> TechnitiumClient:
    """Return the active Technitium client from the registry."""
    engine = get_engine_registry().get("technitium")
    if not isinstance(engine, TechnitiumClient):
        raise AppError("Expected TechnitiumClient")
    return engine


def get_technitium_pool() -> TechnitiumPool:
    """Return the TechnitiumPool from the engine registry.

    The pool is stored on the failover engine since it needs
    access to all server clients.
    """
    failover = get_failover_engine()
    if failover._pool is None:
        raise AppError("TechnitiumPool not configured")
    return failover._pool


def get_backup_engine() -> BackupEngine:
    """Return the backup engine from the registry."""
    engine = get_engine_registry().get("backup")
    if not isinstance(engine, BackupEngine):
        raise AppError("Expected BackupEngine")
    return engine


def get_enforcement_engine() -> EnforcementEngine:
    """Return the enforcement engine from the registry."""
    engine = get_engine_registry().get("enforcement")
    if not isinstance(engine, EnforcementEngine):
        raise AppError("Expected EnforcementEngine")
    return engine


def get_voter_registry() -> VoterRegistryEngine:
    """Return the voter registry engine from the registry."""
    engine = get_engine_registry().get("voter_registry")
    if not isinstance(engine, VoterRegistryEngine):
        raise AppError("Expected VoterRegistryEngine")
    return engine


@lru_cache(maxsize=1)
def get_module_registry() -> ModuleRegistry:
    """Return the singleton module registry."""
    return ModuleRegistry()
