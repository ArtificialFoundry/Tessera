"""Lease query endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from tessera.api.schemas import AllLeasesResponse, LeasesResponse
from tessera.deps import get_technitium_client
from tessera.exceptions import TechnitiumError

if TYPE_CHECKING:
    from tessera.engines.technitium import TechnitiumClient

router = APIRouter()


@router.get("")
async def all_leases(
    client: TechnitiumClient = Depends(get_technitium_client),
) -> AllLeasesResponse:
    """Get active leases across all scopes."""
    try:
        scopes = await client.list_scopes()
        result: dict[str, list[dict[str, Any]]] = {}
        for scope in scopes:
            name: str = scope.get("name", "")
            if name:
                leases = await client.get_leases(name)
                result[name] = leases
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AllLeasesResponse(scopes=result)


@router.get("/{scope_name}")
async def scope_leases(
    scope_name: str,
    client: TechnitiumClient = Depends(get_technitium_client),
) -> LeasesResponse:
    """Get active leases for a specific scope."""
    try:
        leases = await client.get_leases(scope_name)
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return LeasesResponse(scope=scope_name, leases=leases)
