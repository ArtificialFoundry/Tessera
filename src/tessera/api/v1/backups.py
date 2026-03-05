"""Backup API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from tessera.api.schemas import (
    BackupCreateRequest,
    BackupDetailResponse,
    BackupListResponse,
    BackupManifestResponse,
    BackupSettingsRequest,
    BackupSettingsResponse,
    MessageResponse,
    PaginationMeta,
    RestoreRequest,
    RestoreResponse,
)
from tessera.deps import get_backup_engine
from tessera.engines.backup import BackupError
from tessera.exceptions import NotFoundError

if TYPE_CHECKING:
    from tessera.engines.backup import BackupEngine

router = APIRouter()


@router.get("/settings")
async def get_backup_settings(
    engine: BackupEngine = Depends(get_backup_engine),
) -> BackupSettingsResponse:
    """Get current backup engine settings."""
    return BackupSettingsResponse(
        auto_enabled=engine.auto_enabled,
        cron_schedule=engine.cron_schedule,
        max_backups=engine.max_backups,
        stored_backups=len(list(engine.backup_dir.glob("*.json"))),
        next_run=engine.next_run,
        backup_dir=str(engine.backup_dir),
    )


@router.put("/settings")
async def update_backup_settings(
    body: BackupSettingsRequest,
    engine: BackupEngine = Depends(get_backup_engine),
) -> BackupSettingsResponse:
    """Update backup engine settings at runtime."""
    try:
        engine.update_settings(
            auto_enabled=body.auto_enabled,
            cron_schedule=body.cron_schedule,
            max_backups=body.max_backups,
        )
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return BackupSettingsResponse(
        auto_enabled=engine.auto_enabled,
        cron_schedule=engine.cron_schedule,
        max_backups=engine.max_backups,
        stored_backups=len(list(engine.backup_dir.glob("*.json"))),
        next_run=engine.next_run,
        backup_dir=str(engine.backup_dir),
    )


@router.get("")
async def list_backups(
    offset: int = 0,
    limit: int = 20,
    engine: BackupEngine = Depends(get_backup_engine),
) -> BackupListResponse:
    """List stored backups (paginated)."""
    all_manifests = engine.list_backups()
    total = len(all_manifests)
    page = all_manifests[offset : offset + limit]
    return BackupListResponse(
        backups=[
            BackupManifestResponse(
                backup_id=m.backup_id,
                created_at=m.created_at,
                source=m.source,
                description=m.description,
                scope_count=m.scope_count,
                reservation_count=m.reservation_count,
            )
            for m in page
        ],
        pagination=PaginationMeta(total=total, offset=offset, limit=limit),
    )


@router.post("")
async def create_backup(
    body: BackupCreateRequest,
    engine: BackupEngine = Depends(get_backup_engine),
) -> BackupManifestResponse:
    """Create a new DHCP state backup."""
    try:
        manifest = await engine.create_backup(description=body.description)
    except BackupError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return BackupManifestResponse(
        backup_id=manifest.backup_id,
        created_at=manifest.created_at,
        source=manifest.source,
        description=manifest.description,
        scope_count=manifest.scope_count,
        reservation_count=manifest.reservation_count,
    )


@router.get("/{backup_id}")
async def get_backup(
    backup_id: str,
    engine: BackupEngine = Depends(get_backup_engine),
) -> BackupDetailResponse:
    """Get a specific backup with full scope data."""
    try:
        backup = engine.get_backup(backup_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    from dataclasses import asdict

    return BackupDetailResponse(
        manifest=BackupManifestResponse(**asdict(backup.manifest)),
        scopes=[asdict(s) for s in backup.scopes],
    )


@router.delete("/{backup_id}")
async def delete_backup(
    backup_id: str,
    engine: BackupEngine = Depends(get_backup_engine),
) -> MessageResponse:
    """Delete a stored backup."""
    try:
        engine.delete_backup(backup_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return MessageResponse(message=f"Backup '{backup_id}' deleted")


@router.post("/{backup_id}/restore")
async def restore_backup(
    backup_id: str,
    body: RestoreRequest,
    engine: BackupEngine = Depends(get_backup_engine),
) -> RestoreResponse:
    """Restore DHCP state from a backup.

    Default is dry_run=True (preview changes without applying).
    """
    try:
        result = await engine.restore_backup(backup_id, dry_run=body.dry_run)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BackupError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return RestoreResponse(
        backup_id=result["backup_id"],
        dry_run=result["dry_run"],
        changes=result["changes"],
        total_changes=result["total_changes"],
    )
