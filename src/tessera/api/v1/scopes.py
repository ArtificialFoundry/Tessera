"""DHCP scope CRUD endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from tessera.api.schemas import (
    MessageResponse,
    ReservationRequest,
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


@router.get("")
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


@router.get("/{name}")
async def get_scope(
    name: str,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> ScopeDetailResponse:
    """Get detailed scope information."""
    try:
        detail = await client.get_scope(name)
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ScopeDetailResponse(name=name, data=detail)


@router.put("/{name}")
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


@router.post("/{name}/reservations")
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


@router.delete("/{name}/reservations/{mac}")
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
