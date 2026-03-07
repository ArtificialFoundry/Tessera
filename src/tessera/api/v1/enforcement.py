"""Enforcement API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from tessera.api.schemas import (
    AcceptDriftResponse,
    DriftCheckResponse,
    EnforcementModeRequest,
    EnforcementSettingsRequest,
    EnforcementStatusResponse,
    MessageResponse,
    PaginationMeta,
    PinBackupRequest,
)
from tessera.deps import get_enforcement_engine
from tessera.engines.enforcement import EnforcementMode

if TYPE_CHECKING:
    from tessera.engines.enforcement import EnforcementEngine

router = APIRouter()


@router.get("", response_model=EnforcementStatusResponse)
async def get_enforcement_status(
    history_offset: int = 0,
    history_limit: int = 20,
    engine: EnforcementEngine = Depends(get_enforcement_engine),
) -> EnforcementStatusResponse:
    """Get current enforcement state (history paginated)."""
    state = engine.enforcement_state
    from dataclasses import asdict

    all_history = state.history
    total = len(all_history)
    page = all_history[
        history_offset : history_offset + history_limit
    ]

    return EnforcementStatusResponse(
        mode=state.mode.value,
        pinned_backup_id=state.pinned_backup_id,
        check_interval=state.check_interval,
        last_check=state.last_check,
        last_drift=state.last_drift,
        drift_count=state.drift_count,
        restore_count=state.restore_count,
        backup_on_pin=state.backup_on_pin,
        auto_restore_cooldown=state.auto_restore_cooldown,
        max_history=state.max_history,
        history=[asdict(e) for e in page],
        history_pagination=PaginationMeta(
            total=total,
            offset=history_offset,
            limit=history_limit,
        ),
    )


@router.post("/mode", response_model=MessageResponse)
async def set_enforcement_mode(
    body: EnforcementModeRequest,
    engine: EnforcementEngine = Depends(get_enforcement_engine),
) -> MessageResponse:
    """Change enforcement mode (off / monitor / enforce)."""
    try:
        mode = EnforcementMode(body.mode.lower())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid mode: {body.mode}. "
                "Must be: off, monitor, enforce"
            ),
        ) from exc

    engine.set_mode(mode)
    return MessageResponse(
        message=f"Enforcement mode set to {mode.value}"
    )


@router.post("/pin", response_model=MessageResponse)
async def pin_backup(
    body: PinBackupRequest,
    engine: EnforcementEngine = Depends(get_enforcement_engine),
) -> MessageResponse:
    """Pin a backup as the desired DHCP state."""
    await engine.pin_backup(body.backup_id)
    return MessageResponse(
        message=f"Pinned backup '{body.backup_id}' as desired state"
    )


@router.post("/unpin", response_model=MessageResponse)
async def unpin_backup(
    engine: EnforcementEngine = Depends(get_enforcement_engine),
) -> MessageResponse:
    """Unpin the current backup and disable enforcement."""
    engine.unpin()
    return MessageResponse(
        message="Unpinned backup, enforcement disabled"
    )


@router.post("/check", response_model=DriftCheckResponse)
async def check_drift(
    engine: EnforcementEngine = Depends(get_enforcement_engine),
) -> DriftCheckResponse:
    """Manually trigger a drift check."""
    result = await engine.check_drift()

    return DriftCheckResponse(
        drift_detected=result["drift_detected"],
        changes=result.get("changes", []),
        drift_summary=result.get("drift_summary", []),
        total_changes=result.get("total_changes", 0),
        action=result.get("action", "none"),
    )


@router.put("/settings", response_model=MessageResponse)
async def update_enforcement_settings(
    body: EnforcementSettingsRequest,
    engine: EnforcementEngine = Depends(get_enforcement_engine),
) -> MessageResponse:
    """Update enforcement engine settings at runtime."""
    engine.update_settings(
        check_interval=body.check_interval,
        backup_on_pin=body.backup_on_pin,
        auto_restore_cooldown=body.auto_restore_cooldown,
        max_history=body.max_history,
    )
    return MessageResponse(message="Enforcement settings updated")


@router.post("/accept", response_model=AcceptDriftResponse)
async def accept_drift(
    engine: EnforcementEngine = Depends(get_enforcement_engine),
) -> AcceptDriftResponse:
    """Accept current drift by snapshotting live state."""
    new_id = await engine.accept_drift()

    return AcceptDriftResponse(
        new_backup_id=new_id,
        message=f"Drift accepted — new pin: {new_id}",
    )
