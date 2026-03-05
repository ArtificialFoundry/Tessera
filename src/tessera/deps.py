"""FastAPI dependency injection providers.

All shared state is injected via ``Depends()`` so that tests can
override them cleanly without ``unittest.mock.patch``.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from tessera.config import Settings
from tessera.engines.failover import FailoverEngine
from tessera.engines.scope_sync import ScopeSyncEngine
from tessera.engines.technitium import TechnitiumClient
from tessera.registry import EngineRegistry, ModuleRegistry

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

    # Create and register engines
    primary_client = TechnitiumClient(base_url=settings.primary_url, token=token)
    registry.register(primary_client)

    failover = FailoverEngine(
        quorum=settings.quorum,
        failover_rounds=settings.failover_rounds,
        failback_rounds=settings.failback_rounds,
        vote_ttl=settings.vote_ttl,
        voter_keys=voter_keys,
    )
    registry.register(failover)

    standby_client = TechnitiumClient(base_url=settings.standby_url, token=token)
    # Don't register standby as engine (same name conflict)
    # Store it for scope_sync

    scope_sync = ScopeSyncEngine(sync_interval=settings.sync_interval)
    scope_sync.set_clients(primary_client, standby_client)
    registry.register(scope_sync)

    return registry


def get_failover_engine() -> FailoverEngine:
    """Return the failover engine from the registry."""
    engine = get_engine_registry().get("failover")
    assert isinstance(engine, FailoverEngine)
    return engine


def get_technitium_client() -> TechnitiumClient:
    """Return the primary Technitium client from the registry."""
    engine = get_engine_registry().get("technitium")
    assert isinstance(engine, TechnitiumClient)
    return engine


@lru_cache(maxsize=1)
def get_module_registry() -> ModuleRegistry:
    """Return the singleton module registry."""
    return ModuleRegistry()
