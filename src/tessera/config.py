"""Application settings loaded from environment variables.

All settings are prefixed with ``TESSERA_`` and can be overridden via
environment variables or a ``.env`` file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DhcpServer(BaseModel):
    """Configuration for a single DHCP server.

    Attributes:
        name: Human-readable server identifier.
        url: Base URL for the Technitium API.
        role: Server role — active, candidate, or observer.
        priority: Failover priority (lower = higher priority for promotion).
        token: Per-server API token (overrides the global api_token_file).
    """

    name: str
    url: str
    role: Literal["active", "candidate", "observer"] = "candidate"
    priority: int = 0
    token: str = ""


class Settings(BaseSettings):
    """Application configuration sourced from environment.

    Attributes:
        app_name: Display name used in OpenAPI docs.
        debug: Enable debug mode.
        host: Bind address for uvicorn.
        port: Bind port for uvicorn.
        servers: JSON list of DhcpServer configs (parsed from env).
        servers_file: Path to JSON file with server list.
        api_token_file: Path to file containing Technitium API token.
        voter_keys_file: Voter keys JSON path (voter registry engine).
        quorum: Minimum votes required for quorum.
        failover_rounds: Consecutive failed rounds before failover.
        failback_rounds: Consecutive healthy rounds before failback.
        vote_ttl: Seconds before a vote expires.
        sync_interval: Seconds between scope sync runs.
        config_reload_interval: Seconds between config file change checks.
        registration_token: Token for voter self-registration API.
        registration_token_file: Path to file containing registration token.
        registration_token_ttl: Seconds before a registration token expires.
        psk_grace_period: Seconds to keep old PSK valid after rotation.
        auto_approve_voters: Automatically approve voter registrations.
        voter_registry_file: Path to voter registration metadata file.
    """

    model_config = SettingsConfigDict(
        env_prefix="TESSERA_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    app_name: str = "tessera"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8780

    # Multi-server config
    servers: str = ""  # JSON string parsed in get_servers()
    servers_file: Path = Path("/etc/tessera/servers.json")

    api_token_file: Path = Path("/etc/tessera/token")
    voter_keys_file: Path = Path("/etc/tessera/voters.json")
    quorum: int = 3
    failover_rounds: int = 3
    failback_rounds: int = 5
    vote_ttl: int = 90
    sync_interval: int = 300
    backup_dir: Path = Path("/var/lib/tessera/backups")
    backup_encryption_key: str = ""
    webhook_urls: str = ""
    max_backups: int = 50
    auto_backup_interval: int = 3600
    backup_cron_schedule: str = ""
    enforcement_interval: int = 300
    config_reload_interval: int = 10
    registration_token_ttl: int = 3600
    psk_grace_period: int = 60

    # Voter registration
    registration_token: str = ""
    registration_token_file: Path = Path("/etc/tessera/registration-token")
    auto_approve_voters: bool = False
    voter_registry_file: Path = Path("/var/lib/tessera/voter-registry.json")

    # Admin authentication
    admin_api_key: str = ""

    # CORS
    cors_origins: list[str] = []

    # CA certificate trust
    ca_cert_file: str = ""
    skip_tls_verify: bool = False

    def get_servers(self) -> list[DhcpServer]:
        """Resolve the DHCP server list from all config sources.

        Priority:
        1. ``TESSERA_SERVERS`` env var (JSON string)
        2. ``TESSERA_SERVERS_FILE`` (JSON file)

        Returns:
            List of DhcpServer configs.

        Raises:
            RuntimeError: If no servers can be resolved from any source.
        """
        # 1. Inline JSON
        if self.servers:
            try:
                raw = json.loads(self.servers)
                return [DhcpServer(**s) for s in raw]
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.error("Failed to parse TESSERA_SERVERS: %s", exc)

        # 2. JSON file
        if self.servers_file.is_file():
            try:
                raw = json.loads(self.servers_file.read_text())
                return [DhcpServer(**s) for s in raw]
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.error("Failed to parse %s: %s", self.servers_file, exc)

        raise RuntimeError(
            "No DHCP servers configured. Set TESSERA_SERVERS (JSON string) "
            "or TESSERA_SERVERS_FILE (path to JSON file)."
        )

    def get_registration_token(self) -> str:
        """Resolve the registration token from env or file.

        Returns:
            The registration token string, or empty if not configured.
        """
        if self.registration_token:
            return self.registration_token
        if self.registration_token_file.is_file():
            try:
                return self.registration_token_file.read_text().strip()
            except OSError:
                pass
        return ""
