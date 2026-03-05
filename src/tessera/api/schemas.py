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


class LeaseItem(BaseModel):
    """A single DHCP lease."""

    data: dict[str, object]


class LeasesResponse(BaseModel):
    """List of DHCP leases."""

    scope: str
    leases: list[dict[str, object]]


class AllLeasesResponse(BaseModel):
    """Leases across all scopes."""

    scopes: dict[str, list[dict[str, object]]]
