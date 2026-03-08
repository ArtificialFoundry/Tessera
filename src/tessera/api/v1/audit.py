"""Audit trail endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from tessera.api.schemas import PaginationMeta
from tessera.deps import get_audit_engine, require_admin

if TYPE_CHECKING:
    from tessera.engines.audit import AuditEngine

router = APIRouter()


@router.get(
    "",
    dependencies=[Depends(require_admin)],
)
async def list_audit_events(
    engine: AuditEngine = Depends(get_audit_engine),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    action: str = Query(""),
    actor: str = Query(""),
) -> dict[str, object]:
    """List audit trail events (newest first)."""
    events, total = engine.query(
        offset=offset,
        limit=limit,
        action_filter=action,
        actor_filter=actor,
    )
    return {
        "events": [e.to_dict() for e in events],
        "pagination": PaginationMeta(
            total=total,
            offset=offset,
            limit=limit,
        ).model_dump(),
    }
