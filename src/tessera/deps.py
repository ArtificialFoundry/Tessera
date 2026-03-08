"""FastAPI dependency injection providers.

All shared state is injected via ``Depends()`` so that tests can
override them cleanly without ``unittest.mock.patch``.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, Request

from tessera.config import Settings
from tessera.engines.backup import BackupEngine
from tessera.engines.config_watcher import ConfigWatcherEngine
from tessera.engines.enforcement import EnforcementEngine
from tessera.engines.failover import FailoverEngine
from tessera.engines.scope_sync import ScopeSyncEngine
from tessera.engines.technitium import TechnitiumClient, TechnitiumPool
from tessera.engines.voter_registry import VoterRegistryEngine
from tessera.exceptions import (
    AppError,
    AuthenticationError,
    RateLimitError,
    ServiceUnavailableError,
)
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

    # Voter keys are managed exclusively by the voter registry engine.
    # The voter_keys_file is written by the registry and read by it on start.

    # Create multi-server pool
    servers = settings.get_servers()
    pool = TechnitiumPool.from_servers(
        servers,
        token,
        ca_cert_file=settings.ca_cert_file,
        skip_tls_verify=settings.skip_tls_verify,
    )

    # Register the active client as the "technitium" engine
    active_client = pool.get_active()
    registry.register(active_client)

    # Failover engine — voter keys will be injected by voter registry on start
    failover = FailoverEngine(
        quorum=settings.quorum,
        failover_rounds=settings.failover_rounds,
        failback_rounds=settings.failback_rounds,
        vote_ttl=settings.vote_ttl,
        state_file=settings.backup_dir.parent / "failover-state.json",
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


# Admin auth rate limiting: per-IP tracking
_auth_failures: dict[str, list[float]] = defaultdict(list)
_AUTH_WINDOW = 60.0  # seconds
_AUTH_MAX_ATTEMPTS = 5  # max failures per window per IP


def _check_auth_rate_limit(client_ip: str) -> None:
    """Reject if too many failed auth attempts from this IP.

    Raises:
        RateLimitError: If the IP has exceeded the failure threshold.
    """
    now = time.time()
    cutoff = now - _AUTH_WINDOW
    attempts = _auth_failures[client_ip]
    # Prune expired entries
    _auth_failures[client_ip] = [t for t in attempts if t > cutoff]
    if len(_auth_failures[client_ip]) >= _AUTH_MAX_ATTEMPTS:
        raise RateLimitError(
            f"auth:{client_ip}",
            _AUTH_WINDOW - (now - _auth_failures[client_ip][0]),
        )


def _record_auth_failure(client_ip: str) -> None:
    """Record a failed auth attempt for rate limiting."""
    _auth_failures[client_ip].append(time.time())


def reset_auth_rate_limits() -> None:
    """Clear all rate limit state. Used in tests."""
    _auth_failures.clear()


async def require_admin(
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    """Verify the request carries a valid admin API key.

    Rate-limits failed attempts per source IP (5 failures per 60s window).

    Raises ``ServiceUnavailableError`` when the key is not configured,
    ``RateLimitError`` when too many failed attempts, and
    ``AuthenticationError`` when the token is missing or invalid.
    """
    client_ip = request.client.host if request.client else "unknown"

    if not settings.admin_api_key:
        raise ServiceUnavailableError("Admin API key not configured")

    _check_auth_rate_limit(client_ip)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        _record_auth_failure(client_ip)
        logger.warning("Admin auth: missing Bearer token from %s", client_ip)
        raise AuthenticationError("Missing Bearer token")
    token = auth.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, settings.admin_api_key):
        _record_auth_failure(client_ip)
        logger.warning("Admin auth: invalid API key from %s", client_ip)
        raise AuthenticationError("Invalid API key")
