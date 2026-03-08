"""DHCP scope CRUD endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from tessera.api.schemas import (
    MessageResponse,
    ReservationRequest,
    ScopeCreateRequest,
    ScopeDetailResponse,
    ScopeListItem,
    ScopesResponse,
    ScopeUpdateRequest,
)
from tessera.deps import (
    dhcp_write_guard,
    get_technitium_client,
    require_admin,
)
from tessera.exceptions import TechnitiumError

if TYPE_CHECKING:
    from tessera.engines.enforcement import EnforcementEngine
    from tessera.engines.technitium import TechnitiumClient

router = APIRouter()


@router.get("", response_model=ScopesResponse)
async def list_scopes(
    client: TechnitiumClient = Depends(get_technitium_client),
) -> ScopesResponse:
    """List all DHCP scopes."""
    scopes = await client.list_scopes()

    return ScopesResponse(
        scopes=[
            ScopeListItem(
                name=s.get("name", ""),
                enabled=s.get("enabled", False),
                start_address=s.get("startingAddress", ""),
                end_address=s.get("endingAddress", ""),
                subnet_mask=s.get("subnetMask", ""),
            )
            for s in scopes
        ]
    )


@router.post(
    "",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def create_scope(
    body: ScopeCreateRequest,
    client: TechnitiumClient = Depends(get_technitium_client),
    enforcement: EnforcementEngine = Depends(dhcp_write_guard),
) -> MessageResponse:
    """Create a new DHCP scope."""
    settings: dict[str, str] = {
        "startingAddress": body.starting_address,
        "endingAddress": body.ending_address,
        "subnetMask": body.subnet_mask,
    }
    if body.router_address:
        settings["routerAddress"] = body.router_address
    if body.domain_name:
        settings["domainName"] = body.domain_name
    if body.dns_servers:
        settings["dnsServers"] = ",".join(body.dns_servers)
    if body.lease_time_days is not None:
        settings["leaseTimeDays"] = str(body.lease_time_days)
    if body.lease_time_hours is not None:
        settings["leaseTimeHours"] = str(body.lease_time_hours)
    if body.lease_time_minutes is not None:
        settings["leaseTimeMinutes"] = str(body.lease_time_minutes)
    await client.set_scope(body.name, settings)
    await enforcement.notify_dhcp_write()

    return MessageResponse(message=f"Scope '{body.name}' created")


@router.get("/{name}", response_model=ScopeDetailResponse)
async def get_scope(
    name: str,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> ScopeDetailResponse:
    """Get detailed scope information."""
    try:
        detail = await client.get_scope(name)
    except TechnitiumError as exc:
        msg = str(exc).lower()
        if "was not found" in msg or "does not exist" in msg:
            raise HTTPException(
                status_code=404,
                detail=f"Scope '{name}' not found",
            ) from exc
        raise

    return ScopeDetailResponse(name=name, data=detail)


@router.put(
    "/{name}",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def update_scope(
    name: str,
    body: ScopeUpdateRequest,
    client: TechnitiumClient = Depends(get_technitium_client),
    enforcement: EnforcementEngine = Depends(dhcp_write_guard),
) -> MessageResponse:
    """Update scope settings."""
    settings_dict: dict[str, object] = dict(body.settings)
    str_settings = {k: str(v) for k, v in settings_dict.items()}
    await client.set_scope(name, str_settings)
    await enforcement.notify_dhcp_write()

    return MessageResponse(message=f"Scope '{name}' updated")


@router.delete(
    "/{name}",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def delete_scope(
    name: str,
    client: TechnitiumClient = Depends(get_technitium_client),
    enforcement: EnforcementEngine = Depends(dhcp_write_guard),
) -> MessageResponse:
    """Delete a DHCP scope."""
    await client.delete_scope(name)
    await enforcement.notify_dhcp_write()
    return MessageResponse(message=f"Scope '{name}' deleted")


@router.post(
    "/{name}/enable",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def enable_scope(
    name: str,
    client: TechnitiumClient = Depends(get_technitium_client),
    enforcement: EnforcementEngine = Depends(dhcp_write_guard),
) -> MessageResponse:
    """Enable a DHCP scope."""
    await client.enable_scope(name)
    await enforcement.notify_dhcp_write()
    return MessageResponse(message=f"Scope '{name}' enabled")


@router.post(
    "/{name}/disable",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def disable_scope(
    name: str,
    client: TechnitiumClient = Depends(get_technitium_client),
    enforcement: EnforcementEngine = Depends(dhcp_write_guard),
) -> MessageResponse:
    """Disable a DHCP scope."""
    await client.disable_scope(name)
    await enforcement.notify_dhcp_write()
    return MessageResponse(message=f"Scope '{name}' disabled")


@router.post(
    "/{name}/reservations",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def add_reservation(
    name: str,
    body: ReservationRequest,
    client: TechnitiumClient = Depends(get_technitium_client),
    enforcement: EnforcementEngine = Depends(dhcp_write_guard),
) -> MessageResponse:
    """Add a DHCP reservation to a scope."""
    await client.add_reservation(
        name,
        hardware_address=body.hardware_address,
        address=body.address,
        host_name=body.host_name,
        comments=body.comments,
    )
    await enforcement.notify_dhcp_write()

    return MessageResponse(
        message=(f"Reservation added: {body.hardware_address} → {body.address}")
    )


@router.put(
    "/{name}/reservations/{mac}",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def update_reservation(
    name: str,
    mac: str,
    body: ReservationRequest,
    client: TechnitiumClient = Depends(get_technitium_client),
    enforcement: EnforcementEngine = Depends(dhcp_write_guard),
) -> MessageResponse:
    """Update an existing DHCP reservation."""
    await client.add_reservation(
        name,
        hardware_address=mac,
        address=body.address,
        host_name=body.host_name,
        comments=body.comments,
    )
    await enforcement.notify_dhcp_write()

    return MessageResponse(message=f"Reservation updated: {mac} → {body.address}")


@router.delete(
    "/{name}/reservations/{mac}",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def remove_reservation(
    name: str,
    mac: str,
    client: TechnitiumClient = Depends(get_technitium_client),
    enforcement: EnforcementEngine = Depends(dhcp_write_guard),
) -> MessageResponse:
    """Remove a DHCP reservation from a scope."""
    await client.remove_reservation(name, hardware_address=mac)
    await enforcement.notify_dhcp_write()
    return MessageResponse(message=f"Reservation removed: {mac}")
