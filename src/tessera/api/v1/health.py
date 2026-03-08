"""Health, liveness, and metrics endpoints."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from tessera.api.schemas import EngineHealthResponse, HealthResponse, PingResponse
from tessera.deps import get_engine_registry, require_admin
from tessera.registry import EngineRegistry, EngineStatus

if TYPE_CHECKING:
    from tessera.engines.technitium import TechnitiumPool

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(
    deep: bool = Query(default=False, description="Probe live dependencies"),
    engines: EngineRegistry = Depends(get_engine_registry),
) -> HealthResponse:
    """Aggregate health check across all engines.

    With ``deep=true``, also probes Technitium API connectivity and
    verifies config/data file readability. Returns 503 via status
    field if any critical dependency is down.
    """
    engine_health = await engines.health_check()

    deep_errors: dict[str, str] = {}
    if deep:
        deep_errors = await _deep_probe(engines)

    all_running = all(h.status == EngineStatus.RUNNING for h in engine_health.values())
    any_critical_down = bool(deep_errors)

    status = "healthy"
    if any_critical_down:
        status = "unhealthy"
    elif not all_running:
        status = "degraded"

    engine_responses: dict[str, EngineHealthResponse] = {
        name: EngineHealthResponse(status=h.status.value, message=h.message)
        for name, h in engine_health.items()
    }

    # Append deep probe results as virtual entries
    for key, msg in deep_errors.items():
        engine_responses[f"deep:{key}"] = EngineHealthResponse(
            status="failed", message=msg
        )

    return HealthResponse(
        status=status,
        engines=engine_responses,
    )


async def _deep_probe(engines: EngineRegistry) -> dict[str, str]:
    """Probe live dependencies. Returns dict of failed probe name → error."""
    errors: dict[str, str] = {}

    # Probe Technitium connectivity via TechnitiumPool
    from tessera.engines.technitium import TechnitiumPool

    pool: TechnitiumPool | None = None
    for engine in engines._engines.values():
        if isinstance(engine, TechnitiumPool):
            pool = engine
            break

    if pool:
        for client in pool.get_all():
            sname = getattr(client, "server_name", "unknown")
            try:
                await client.list_scopes()
            except Exception as exc:
                errors[f"technitium:{sname}"] = str(exc)
    else:
        # Try single-client fallback
        from tessera.engines.technitium import TechnitiumClient

        try:
            tc = engines.get("technitium")
        except Exception:
            tc = None
        if isinstance(tc, TechnitiumClient):
            try:
                await tc.list_scopes()
            except Exception as exc:
                errors["technitium"] = str(exc)

    # Verify voter keys file is parseable
    from tessera.engines.voter_registry import VoterRegistryEngine

    try:
        vr = engines.get("voter_registry")
    except Exception:
        vr = None
    if isinstance(vr, VoterRegistryEngine):
        try:
            vr._load_voter_keys()
        except Exception as exc:
            errors["voter_keys_file"] = str(exc)

    return errors


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(
    engines: EngineRegistry = Depends(get_engine_registry),
) -> str:
    """Prometheus-format metrics endpoint.

    Exposes key operational metrics without requiring prometheus-client.
    """
    lines: list[str] = []
    _ts = int(time.time() * 1000)

    # Engine health as gauges (1=running, 0.5=degraded, 0=failed/stopped)
    lines.append(
        "# HELP tessera_engine_health Engine health (1=running, 0.5=degraded, 0=failed)"
    )
    lines.append("# TYPE tessera_engine_health gauge")
    health_map = await engines.health_check()
    for name, h in health_map.items():
        if h.status == EngineStatus.RUNNING:
            val = 1.0
        elif h.status == EngineStatus.DEGRADED:
            val = 0.5
        else:
            val = 0.0
        lines.append(f'tessera_engine_health{{engine="{name}"}} {val}')

    # Per-engine metrics
    for name, engine in engines._engines.items():
        if hasattr(engine, "get_metrics"):
            m = engine.get_metrics()
            for key, value in m.items():
                if isinstance(value, (int, float)):
                    metric_name = f"tessera_{name}_{key}"
                    lines.append(f"{metric_name} {value}")

    lines.append("")
    return "\n".join(lines)


@router.get("/ping", response_model=PingResponse)
async def ping() -> PingResponse:
    """Liveness probe — always returns OK."""
    return PingResponse(status="ok")


@router.post(
    "/auth/verify",
    dependencies=[Depends(require_admin)],
)
async def verify_auth() -> dict[str, str]:
    """Verify the admin API key is valid. Returns 200 on success."""
    return {"status": "ok"}
