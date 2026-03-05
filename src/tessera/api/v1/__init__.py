"""V1 API router — aggregates all v1 sub-routers."""

from __future__ import annotations

from fastapi import APIRouter

from tessera.api.v1.failover import router as failover_router
from tessera.api.v1.health import router as health_router
from tessera.api.v1.leases import router as leases_router
from tessera.api.v1.scopes import router as scopes_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(failover_router, tags=["failover"])
router.include_router(scopes_router, prefix="/scopes", tags=["scopes"])
router.include_router(leases_router, prefix="/leases", tags=["leases"])
