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
from tessera.deps import get_technitium_client
from tessera.exceptions import TechnitiumError

if TYPE_CHECKING:
    from tessera.engines.technitium import TechnitiumClient

router = APIRouter()


@router.get("", response_model=ScopesResponse)
async def list_scopes(
    client: TechnitiumClient = Depends(get_technitium_client),
) -> ScopesResponse:
    """List all DHCP scopes."""
    try:
        scopes = await client.list_scopes()
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

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


@router.post("", response_model=MessageResponse)
async def create_scope(
    body: ScopeCreateRequest,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> MessageResponse:
    """Create a new DHCP scope.

    Uses Technitium's ``scopes/set`` which creates if the scope doesn't exist.
    """
    try:
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
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

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
                status_code=404, detail=f"Scope '{name}' not found"
            ) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ScopeDetailResponse(name=name, data=detail)


@router.put("/{name}", response_model=MessageResponse)
async def update_scope(
    name: str,
    body: ScopeUpdateRequest,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> MessageResponse:
    """Update scope settings."""
    try:
        settings_dict: dict[str, object] = dict(body.settings)
        str_settings = {k: str(v) for k, v in settings_dict.items()}
        await client.set_scope(name, str_settings)
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return MessageResponse(message=f"Scope '{name}' updated")


@router.delete("/{name}", response_model=MessageResponse)
async def delete_scope(
    name: str,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> MessageResponse:
    """Delete a DHCP scope."""
    try:
        await client.delete_scope(name)
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return MessageResponse(message=f"Scope '{name}' deleted")


@router.post("/{name}/enable", response_model=MessageResponse)
async def enable_scope(
    name: str,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> MessageResponse:
    """Enable a DHCP scope."""
    try:
        await client.enable_scope(name)
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return MessageResponse(message=f"Scope '{name}' enabled")


@router.post("/{name}/disable", response_model=MessageResponse)
async def disable_scope(
    name: str,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> MessageResponse:
    """Disable a DHCP scope."""
    try:
        await client.disable_scope(name)
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return MessageResponse(message=f"Scope '{name}' disabled")


@router.post("/{name}/reservations", response_model=MessageResponse)
async def add_reservation(
    name: str,
    body: ReservationRequest,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> MessageResponse:
    """Add a DHCP reservation to a scope."""
    try:
        await client.add_reservation(
            name,
            hardware_address=body.hardware_address,
            address=body.address,
            host_name=body.host_name,
            comments=body.comments,
        )
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return MessageResponse(
        message=f"Reservation added: {body.hardware_address} → {body.address}"
    )


@router.put("/{name}/reservations/{mac}", response_model=MessageResponse)
async def update_reservation(
    name: str,
    mac: str,
    body: ReservationRequest,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> MessageResponse:
    """Update an existing DHCP reservation.

    The MAC in the URL identifies the reservation to update. The body
    can change the IP, hostname, and comments. To change the MAC itself,
    delete and re-create.
    """
    try:
        await client.add_reservation(
            name,
            hardware_address=mac,
            address=body.address,
            host_name=body.host_name,
            comments=body.comments,
        )
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return MessageResponse(
        message=f"Reservation updated: {mac} → {body.address}"
    )


@router.delete("/{name}/reservations/{mac}", response_model=MessageResponse)
async def remove_reservation(
    name: str,
    mac: str,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> MessageResponse:
    """Remove a DHCP reservation from a scope."""
    try:
        await client.remove_reservation(name, hardware_address=mac)
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return MessageResponse(message=f"Reservation removed: {mac}")
