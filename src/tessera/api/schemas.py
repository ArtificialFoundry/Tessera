"""Pydantic response models for API endpoints."""

from __future__ import annotations

from pydantic import BaseModel

# -- Health -------------------------------------------------------------------


class EngineHealthResponse(BaseModel):
    """Health status of a single engine."""

    status: str
    message: str


class HealthResponse(BaseModel):
    """Aggregate health across all engines."""

    status: str
    engines: dict[str, EngineHealthResponse]


class PingResponse(BaseModel):
    """Liveness probe response."""

    status: str


# -- Pagination ---------------------------------------------------------------


class PaginationMeta(BaseModel):
    """Pagination metadata included in paginated responses."""

    total: int
    offset: int
    limit: int


# -- Registry -----------------------------------------------------------------


class EngineDetail(BaseModel):
    """Detailed info for a single registered engine."""

    version: str
    description: str
    status: str
    depends_on: tuple[str, ...]
    metrics: dict[str, object]


class EnginesResponse(BaseModel):
    """List of all registered engines."""

    engines: dict[str, EngineDetail]


class ModuleDetail(BaseModel):
    """Detailed info for a single registered module."""

    version: str
    description: str
    models: list[str]


class ModulesResponse(BaseModel):
    """List of all registered modules."""

    modules: dict[str, ModuleDetail]


# -- Failover -----------------------------------------------------------------


class VoteRequest(BaseModel):
    """Incoming vote payload from a voter."""

    voter: str
    status: str
    timestamp: int
    signature: str


class VoteResponse(BaseModel):
    """Response after accepting a vote."""

    accepted: bool
    voter: str
    status: str


class VoterInfo(BaseModel):
    """Info about a single voter's latest vote."""

    voter: str
    status: str
    timestamp: float
    received_at: float
    verification: str = "unverified"


class TransitionInfo(BaseModel):
    """A state transition event."""

    from_state: str
    to_state: str
    timestamp: float
    reason: str


class FailoverStatusResponse(BaseModel):
    """Full failover status."""

    state: str
    has_quorum: bool
    active_votes: int
    up_count: int
    down_count: int
    consecutive_down: int
    consecutive_up: int
    voters: dict[str, VoterInfo]
    transitions: list[TransitionInfo]
    transitions_pagination: PaginationMeta
    config: dict[str, object]


# -- DHCP Scopes --------------------------------------------------------------


class ScopeListItem(BaseModel):
    """Summary of a DHCP scope."""

    name: str
    enabled: bool
    start_address: str = ""
    end_address: str = ""
    subnet_mask: str = ""


class ScopesResponse(BaseModel):
    """List of all DHCP scopes."""

    scopes: list[ScopeListItem]


class ScopeDetailResponse(BaseModel):
    """Detailed DHCP scope information."""

    name: str
    data: dict[str, object]


class ScopeCreateRequest(BaseModel):
    """Request to create a new DHCP scope."""

    name: str
    starting_address: str
    ending_address: str
    subnet_mask: str
    router_address: str = ""
    domain_name: str = ""
    dns_servers: list[str] = []
    lease_time_days: int | None = None
    lease_time_hours: int | None = None
    lease_time_minutes: int | None = None


class ReservationRequest(BaseModel):
    """Request to add a DHCP reservation."""

    hardware_address: str
    address: str
    host_name: str = ""
    comments: str = ""


class MessageResponse(BaseModel):
    """Generic success message."""

    message: str


class ScopeUpdateRequest(BaseModel):
    """Request to update scope settings."""

    settings: dict[str, object]


# -- Leases -------------------------------------------------------------------


class LeaseEntry(BaseModel):
    """A single DHCP lease entry."""

    address: str = ""
    hardware_address: str = ""
    host_name: str = ""
    type: str = ""  # "Dynamic" or "Reserved"


class LeaseItem(BaseModel):
    """A single DHCP lease."""

    data: dict[str, object]


class LeasesResponse(BaseModel):
    """Paginated list of DHCP leases."""

    scope: str
    leases: list[LeaseEntry]
    pagination: PaginationMeta


class AllLeasesResponse(BaseModel):
    """Leases across all scopes."""

    scopes: dict[str, list[LeaseEntry]]


# -- Backups ------------------------------------------------------------------


class BackupManifestResponse(BaseModel):
    """A single backup's metadata."""

    backup_id: str
    created_at: float
    source: str
    description: str
    scope_count: int
    reservation_count: int


class BackupListResponse(BaseModel):
    """Paginated list of backups."""

    backups: list[BackupManifestResponse]
    pagination: PaginationMeta


class ScopeSnapshotResponse(BaseModel):
    """A scope snapshot inside a backup."""

    name: str
    enabled: bool
    settings: dict[str, object]
    reservations: list[dict[str, object]]


class BackupDetailResponse(BaseModel):
    """Full backup data including scopes."""

    manifest: BackupManifestResponse
    scopes: list[ScopeSnapshotResponse]


class BackupCreateRequest(BaseModel):
    """Request to create a backup."""

    description: str = ""


class RestoreRequest(BaseModel):
    """Request to restore from a backup."""

    dry_run: bool = True


class RestoreResponse(BaseModel):
    """Result of a restore operation."""

    backup_id: str
    dry_run: bool
    changes: list[dict[str, str]]
    total_changes: int


# -- Enforcement --------------------------------------------------------------


class DriftEventResponse(BaseModel):
    """A single drift event in enforcement history."""

    detected_at: float
    changes: list[dict[str, str]]
    drift_summary: list[dict[str, str]]
    change_count: int
    action_taken: str
    backup_id: str
    count: int = 1
    last_seen: float = 0.0


class EnforcementStatusResponse(BaseModel):
    """Full enforcement engine status."""

    mode: str
    pinned_backup_id: str
    check_interval: int
    last_check: float
    last_drift: float
    drift_count: int
    restore_count: int
    backup_on_pin: bool = True
    auto_restore_cooldown: int = 60
    max_history: int = 50
    history: list[DriftEventResponse]
    history_pagination: PaginationMeta


class EnforcementModeRequest(BaseModel):
    """Request to change enforcement mode."""

    mode: str


class PinBackupRequest(BaseModel):
    """Request to pin a backup as desired state."""

    backup_id: str


class DriftCheckResponse(BaseModel):
    """Result of a drift check."""

    drift_detected: bool
    changes: list[dict[str, str]]
    drift_summary: list[dict[str, str]] = []
    total_changes: int = 0
    action: str = "none"


class BackupSettingsRequest(BaseModel):
    """Request to update backup engine settings."""

    auto_enabled: bool | None = None
    cron_schedule: str | None = None
    max_backups: int | None = None


class BackupSettingsResponse(BaseModel):
    """Current backup engine settings."""

    auto_enabled: bool
    cron_schedule: str
    max_backups: int
    stored_backups: int
    next_run: float
    backup_dir: str


class EnforcementSettingsRequest(BaseModel):
    """Request to update enforcement settings."""

    check_interval: int | None = None
    backup_on_pin: bool | None = None
    auto_restore_cooldown: int | None = None
    max_history: int | None = None


class AcceptDriftResponse(BaseModel):
    """Result of accepting drift."""

    new_backup_id: str
    message: str


# -- Servers ------------------------------------------------------------------


class ServerInfo(BaseModel):
    """Info about a single DHCP server in the pool."""

    name: str
    url: str
    role: str
    priority: int
    status: str
    message: str


class ServersResponse(BaseModel):
    """List of all DHCP servers."""

    servers: list[ServerInfo]


class PromoteDemoteResponse(BaseModel):
    """Result of a promote/demote operation."""

    name: str
    new_role: str
    message: str


# -- Voter Registration -------------------------------------------------------


class VoterRegisterRequest(BaseModel):
    """Incoming voter registration request."""

    name: str
    callback_url: str = ""
    token: str


class VoterRegisterResponse(BaseModel):
    """Response to voter registration."""

    voter_name: str
    psk: str | None = None
    status: str


class VoterInfoResponse(BaseModel):
    """Info about a registered voter."""

    name: str
    registered_at: float
    approved_at: float | None
    status: str
    last_vote: float
    ip_address: str


class VoterListResponse(BaseModel):
    """List of registered voters."""

    voters: list[VoterInfoResponse]


class VoterApproveResponse(BaseModel):
    """Result of approving a voter."""

    voter_name: str
    psk: str
    status: str


class VoterRevokeResponse(BaseModel):
    """Result of revoking a voter."""

    voter_name: str
    status: str


class RegistrationTokenRequest(BaseModel):
    """Request to generate a registration token."""

    bind_ip: str | None = None
    ttl: int | None = None


class RegistrationTokenResponse(BaseModel):
    """A generated registration token."""

    token: str
    created_at: float
    expires_at: float
    bind_ip: str | None


class RegistrationTokenInfo(BaseModel):
    """Info about a registration token."""

    token: str
    created_at: float
    expires_at: float
    used: bool
    used_by: str | None
    bind_ip: str | None


class RegistrationTokenListResponse(BaseModel):
    """List of registration tokens."""

    tokens: list[RegistrationTokenInfo]


class KeyRotateResponse(BaseModel):
    """Result of PSK rotation."""

    voter_name: str
    new_psk: str
    grace_period: int
    message: str
