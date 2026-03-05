"""Application settings loaded from environment variables.

All settings are prefixed with ``TESSERA_`` and can be overridden via
environment variables or a ``.env`` file.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration sourced from environment.

    Attributes:
        app_name: Display name used in OpenAPI docs.
        debug: Enable debug mode.
        host: Bind address for uvicorn.
        port: Bind port for uvicorn.
        primary_url: Technitium primary server URL.
        standby_url: Technitium standby server URL.
        api_token_file: Path to file containing Technitium API token.
        voter_keys_file: Path to JSON file mapping voter names to PSKs.
        quorum: Minimum votes required for quorum.
        failover_rounds: Consecutive failed rounds before failover.
        failback_rounds: Consecutive healthy rounds before failback.
        vote_ttl: Seconds before a vote expires.
        sync_interval: Seconds between scope sync runs.
        voters: Comma-separated list of expected voter names.
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
    primary_url: str = "https://192.0.2.1:53443"
    standby_url: str = "https://192.0.2.2:53443"
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

    @property
    def voter_list(self) -> list[str]:
        """Return voters as a list of names."""
        return [v.strip() for v in self.voters.split(",") if v.strip()]
