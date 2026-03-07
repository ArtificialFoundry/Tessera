"""DHCP server management endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from tessera.api.schemas import (
    AddServerRequest,
    AddServerResponse,
    PromoteDemoteResponse,
    RemoveServerResponse,
    ServerInfo,
    ServersResponse,
)
from tessera.deps import get_technitium_pool, require_admin

if TYPE_CHECKING:
    from tessera.engines.technitium import TechnitiumPool

router = APIRouter()


@router.get("/servers", response_model=ServersResponse)
async def list_servers(
    pool: TechnitiumPool = Depends(get_technitium_pool),
) -> ServersResponse:
    """List all DHCP servers and their current state."""
    states = await pool.get_server_states()
    return ServersResponse(
        servers=[ServerInfo(**s) for s in states],
    )


@router.post(
    "/servers/{name}/promote",
    response_model=PromoteDemoteResponse,
    dependencies=[Depends(require_admin)],
)
async def promote_server(
    name: str,
    pool: TechnitiumPool = Depends(get_technitium_pool),
) -> PromoteDemoteResponse:
    """Promote a server to primary."""
    pool.promote(name)
    return PromoteDemoteResponse(
        name=name,
        new_role="active",
        message=f"Server {name} promoted to active",
    )


@router.post(
    "/servers/{name}/demote",
    response_model=PromoteDemoteResponse,
    dependencies=[Depends(require_admin)],
)
async def demote_server(
    name: str,
    pool: TechnitiumPool = Depends(get_technitium_pool),
) -> PromoteDemoteResponse:
    """Demote a server to standby."""
    pool.demote(name)
    return PromoteDemoteResponse(
        name=name,
        new_role="candidate",
        message=f"Server {name} demoted to candidate",
    )


@router.post(
    "/servers",
    response_model=AddServerResponse,
    dependencies=[Depends(require_admin)],
)
async def add_server(
    body: AddServerRequest,
    pool: TechnitiumPool = Depends(get_technitium_pool),
) -> AddServerResponse:
    """Add a DHCP server to the pool."""
    await pool.add_server(
        name=body.name,
        url=body.url,
        role=body.role,
        priority=body.priority,
        token=body.token,
    )
    return AddServerResponse(
        name=body.name,
        role=body.role,
        message=f"Server {body.name} added as {body.role}",
    )


@router.delete(
    "/servers/{name}",
    response_model=RemoveServerResponse,
    dependencies=[Depends(require_admin)],
)
async def remove_server(
    name: str,
    pool: TechnitiumPool = Depends(get_technitium_pool),
) -> RemoveServerResponse:
    """Remove a DHCP server from the pool."""
    await pool.remove_server(name)
    return RemoveServerResponse(
        name=name,
        message=f"Server {name} removed from pool",
    )
