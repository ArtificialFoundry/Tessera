"""DHCP server management endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from tessera.api.schemas import (
    PromoteDemoteResponse,
    ServerInfo,
    ServersResponse,
)
from tessera.deps import get_technitium_pool
from tessera.exceptions import TechnitiumError

if TYPE_CHECKING:
    from tessera.engines.technitium import TechnitiumPool

router = APIRouter()


@router.get("/servers", response_model=ServersResponse)
async def list_servers(
    pool: TechnitiumPool = Depends(get_technitium_pool),
) -> ServersResponse:
    """List all DHCP servers and their current state."""
    states = pool.get_server_states()
    return ServersResponse(
        servers=[ServerInfo(**s) for s in states],
    )


@router.post(
    "/servers/{name}/promote",
    response_model=PromoteDemoteResponse,
)
async def promote_server(
    name: str,
    pool: TechnitiumPool = Depends(get_technitium_pool),
) -> PromoteDemoteResponse:
    """Promote a server to primary."""
    try:
        pool.promote(name)
    except TechnitiumError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PromoteDemoteResponse(
        name=name,
        new_role="active",
        message=f"Server {name} promoted to active",
    )


@router.post(
    "/servers/{name}/demote",
    response_model=PromoteDemoteResponse,
)
async def demote_server(
    name: str,
    pool: TechnitiumPool = Depends(get_technitium_pool),
) -> PromoteDemoteResponse:
    """Demote a server to standby."""
    try:
        pool.demote(name)
    except TechnitiumError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PromoteDemoteResponse(
        name=name,
        new_role="candidate",
        message=f"Server {name} demoted to candidate",
    )
