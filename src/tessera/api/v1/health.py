"""Health, liveness, and metrics endpoints."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from tessera.api.schemas import EngineHealthResponse, HealthResponse, PingResponse
from tessera.deps import get_engine_registry, require_admin
from tessera.registry import EngineRegistry, EngineStatus

if TYPE_CHECKING:
    from pathlib import Path

router = APIRouter()


def _ts_to_iso(ts: float) -> str | None:
    """Convert a unix timestamp to ISO 8601 string, or None if zero."""
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


@router.get("/health", response_model=HealthResponse)
async def health(
    deep: bool = Query(default=False, description="Probe live dependencies"),
    engines: EngineRegistry = Depends(get_engine_registry),
) -> HealthResponse:
    """Aggregate health check across all engines.

    With ``deep=true``, also probes live connectivity to Technitium
    servers, verifies config file readability, and checks backup
    directory writability.
    """
    now = time.time()
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
        name: EngineHealthResponse(
            status=h.status.value,
            message=h.message,
            checked_at=_ts_to_iso(h.last_checked) or _ts_to_iso(now),
        )
        for name, h in engine_health.items()
    }

    # Append deep probe results as virtual entries
    for key, msg in deep_errors.items():
        engine_responses[f"deep:{key}"] = EngineHealthResponse(
            status="failed",
            message=msg,
            checked_at=_ts_to_iso(now),
        )

    return HealthResponse(
        status=status,
        checked_at=datetime.fromtimestamp(now, tz=UTC).isoformat(),
        engines=engine_responses,
    )


async def _deep_probe(engines: EngineRegistry) -> dict[str, str]:
    """Probe live dependencies beyond cached engine health.

    Checks:
    - Technitium API connectivity (list_scopes on each server)
    - Voter keys file parseability
    - Backup directory writability
    - Optional: OIDC issuer reachability (if configured via env)

    Returns:
        Dict of failed probe name → error message. Empty = all good.
    """
    errors: dict[str, str] = {}

    # 1. Probe Technitium connectivity via TechnitiumPool
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

    # 2. Verify voter keys file is parseable
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

    # 3. Backup directory writable
    from tessera.engines.backup import BackupEngine

    try:
        be = engines.get("backup")
    except Exception:
        be = None
    if isinstance(be, BackupEngine):
        errors.update(_check_dir_writable("backup_dir", be.backup_dir))

    # 4. Optional OIDC issuer probe (for SSO setups)
    oidc_url = os.environ.get("TESSERA_OIDC_ISSUER_URL")
    if oidc_url:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(f"{oidc_url}/.well-known/openid-configuration")
                if resp.status_code != 200:
                    errors["oidc_issuer"] = f"HTTP {resp.status_code} from {oidc_url}"
        except Exception as exc:
            errors["oidc_issuer"] = str(exc)

    return errors


def _check_dir_writable(name: str, path: Path) -> dict[str, str]:
    """Test that a directory exists and is writable."""
    errors: dict[str, str] = {}
    if not path.exists():
        errors[name] = f"Directory does not exist: {path}"
    elif not os.access(path, os.W_OK):
        errors[name] = f"Directory not writable: {path}"
    return errors


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(
    engines: EngineRegistry = Depends(get_engine_registry),
) -> str:
    """Prometheus-format metrics endpoint.

    Exposes key operational metrics without requiring prometheus-client.
    Only numeric values are emitted as Prometheus gauges.
    """
    lines: list[str] = []

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

    # Per-engine metrics (only numeric values)
    for name, engine in engines._engines.items():
        if hasattr(engine, "get_metrics"):
            m = engine.get_metrics()
            for key, value in m.items():
                if isinstance(value, bool):
                    # Booleans as 0/1 gauges
                    metric_name = f"tessera_{name}_{key}"
                    lines.append(f"{metric_name} {int(value)}")
                elif isinstance(value, (int, float)):
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
