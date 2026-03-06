"""Application settings loaded from environment variables.

All settings are prefixed with ``TESSERA_`` and can be overridden via
environment variables or a ``.env`` file.
"""

from __future__ import annotations

import json
import logging
import warnings
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
    """

    name: str
    url: str
    role: Literal["active", "candidate", "observer"] = "candidate"
    priority: int = 0


class Settings(BaseSettings):
    """Application configuration sourced from environment.

    Attributes:
        app_name: Display name used in OpenAPI docs.
        debug: Enable debug mode.
        host: Bind address for uvicorn.
        port: Bind port for uvicorn.
        primary_url: (DEPRECATED) Technitium primary server URL.
        standby_url: (DEPRECATED) Technitium standby server URL.
        servers: JSON list of DhcpServer configs (parsed from env).
        servers_file: Path to JSON file with server list.
        api_token_file: Path to file containing Technitium API token.
        voter_keys_file: Path to JSON file mapping voter names to PSKs.
        quorum: Minimum votes required for quorum.
        failover_rounds: Consecutive failed rounds before failover.
        failback_rounds: Consecutive healthy rounds before failback.
        vote_ttl: Seconds before a vote expires.
        sync_interval: Seconds between scope sync runs.
        voters: Comma-separated list of expected voter names.
        config_reload_interval: Seconds between config file change checks.
        registration_token: Token for voter self-registration API.
        registration_token_file: Path to file containing registration token.
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

    # Legacy (deprecated) — kept for backward compatibility
    primary_url: str = ""
    standby_url: str = ""

    # Multi-server config
    servers: str = ""  # JSON string parsed in validator
    servers_file: Path = Path("/etc/tessera/servers.json")

    api_token_file: Path = Path("/etc/tessera/token")
    voter_keys_file: Path = Path("/etc/tessera/voters.json")
    quorum: int = 3
    failover_rounds: int = 3
    failback_rounds: int = 5
    vote_ttl: int = 90
    sync_interval: int = 300
    voters: str = "voter-1,voter-2,voter-3,voter-4,voter-5"
    backup_dir: Path = Path("/var/lib/tessera/backups")
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

    # CORS
    cors_origins: list[str] = []

    # CA certificate trust
    ca_cert_file: str = ""

    @property
    def voter_list(self) -> list[str]:
        """Return voters as a list of names."""
        return [v.strip() for v in self.voters.split(",") if v.strip()]

    def get_servers(self) -> list[DhcpServer]:
        """Resolve the DHCP server list from all config sources.

        Priority:
        1. ``TESSERA_SERVERS`` env var (JSON string)
        2. ``TESSERA_SERVERS_FILE`` (JSON file)
        3. Legacy ``TESSERA_PRIMARY_URL`` + ``TESSERA_STANDBY_URL``

        Returns:
            List of DhcpServer configs.
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
                logger.error(
                    "Failed to parse %s: %s", self.servers_file, exc
                )

        # 3. Legacy fallback
        if self.primary_url or self.standby_url:
            warnings.warn(
                "TESSERA_PRIMARY_URL / TESSERA_STANDBY_URL are deprecated. "
                "Use TESSERA_SERVERS or TESSERA_SERVERS_FILE instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            logger.warning(
                "DEPRECATED: Using legacy primary_url/standby_url config. "
                "Migrate to TESSERA_SERVERS or TESSERA_SERVERS_FILE."
            )
            result: list[DhcpServer] = []
            if self.primary_url:
                result.append(
                    DhcpServer(
                        name="active",
                        url=self.primary_url,
                        role="active",
                        priority=0,
                    )
                )
            if self.standby_url:
                result.append(
                    DhcpServer(
                        name="candidate",
                        url=self.standby_url,
                        role="candidate",
                        priority=10,
                    )
                )
            return result

        # Default
        return [
            DhcpServer(
                name="active",
                url="https://192.0.2.1:53443",
                role="active",
                priority=0,
            ),
            DhcpServer(
                name="candidate",
                url="https://192.0.2.2:53443",
                role="candidate",
                priority=10,
            ),
        ]

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
