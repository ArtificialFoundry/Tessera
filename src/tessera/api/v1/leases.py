"""Lease query endpoints."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from tessera.api.schemas import AllLeasesResponse, LeasesResponse
from tessera.deps import get_technitium_client
from tessera.exceptions import TechnitiumError

if TYPE_CHECKING:
    from tessera.engines.technitium import TechnitiumClient

router = APIRouter()


def _filter_leases_by_scope(
    leases: list[dict[str, Any]],
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    """Filter leases to only those within a scope's address range.

    Technitium's ``/api/dhcp/leases/list`` ignores the ``name`` parameter
    and always returns *all* leases.  We filter client-side by checking
    each lease address falls within the scope's start–end range.
    """
    try:
        start = ipaddress.IPv4Address(scope["startingAddress"])
        end = ipaddress.IPv4Address(scope["endingAddress"])
    except (KeyError, ValueError):
        return leases  # can't filter without range — return unfiltered

    filtered: list[dict[str, Any]] = []
    for lease in leases:
        addr_str = lease.get("address", "")
        try:
            addr = ipaddress.IPv4Address(addr_str)
        except ValueError:
            continue
        if start <= addr <= end:
            filtered.append(lease)
    return filtered


@router.get("")
async def all_leases(
    client: TechnitiumClient = Depends(get_technitium_client),
) -> AllLeasesResponse:
    """Get active leases across all scopes, grouped by scope."""
    try:
        scopes = await client.list_scopes()
        all_lease_list = await client.get_leases("")
        result: dict[str, list[dict[str, Any]]] = {}
        for scope in scopes:
            name: str = scope.get("name", "")
            if name:
                result[name] = _filter_leases_by_scope(all_lease_list, scope)
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
        scope_detail = await client.get_scope(scope_name)
        all_leases_list = await client.get_leases(scope_name)
        leases = _filter_leases_by_scope(all_leases_list, scope_detail)
    except TechnitiumError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return LeasesResponse(scope=scope_name, leases=leases)
