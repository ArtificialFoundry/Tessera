"""Health and liveness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from tessera.api.schemas import EngineHealthResponse, HealthResponse, PingResponse
from tessera.deps import get_engine_registry
from tessera.registry import EngineRegistry, EngineStatus

router = APIRouter()


@router.get("/health")
async def health(
    engines: EngineRegistry = Depends(get_engine_registry),
) -> HealthResponse:
    """Aggregate health check across all engines."""
    engine_health = await engines.health_check()
    all_running = all(h.status == EngineStatus.RUNNING for h in engine_health.values())
    return HealthResponse(
        status="healthy" if all_running else "degraded",
        engines={
            name: EngineHealthResponse(status=h.status.value, message=h.message)
            for name, h in engine_health.items()
        },
    )


@router.get("/ping")
async def ping() -> PingResponse:
    """Liveness probe — always returns OK."""
    return PingResponse(status="ok")
