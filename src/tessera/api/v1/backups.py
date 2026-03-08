"""Backup API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

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
    ScopeSnapshotResponse,
)
from tessera.deps import get_audit_engine, get_backup_engine, require_admin

if TYPE_CHECKING:
    from tessera.engines.audit import AuditEngine
    from tessera.engines.backup import BackupEngine

router = APIRouter()


@router.get("/settings", response_model=BackupSettingsResponse)
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


@router.put(
    "/settings",
    response_model=BackupSettingsResponse,
    dependencies=[Depends(require_admin)],
)
async def update_backup_settings(
    body: BackupSettingsRequest,
    engine: BackupEngine = Depends(get_backup_engine),
) -> BackupSettingsResponse:
    """Update backup engine settings at runtime."""
    engine.update_settings(
        auto_enabled=body.auto_enabled,
        cron_schedule=body.cron_schedule,
        max_backups=body.max_backups,
    )

    return BackupSettingsResponse(
        auto_enabled=engine.auto_enabled,
        cron_schedule=engine.cron_schedule,
        max_backups=engine.max_backups,
        stored_backups=len(list(engine.backup_dir.glob("*.json"))),
        next_run=engine.next_run,
        backup_dir=str(engine.backup_dir),
    )


@router.get("", response_model=BackupListResponse)
async def list_backups(
    offset: int = 0,
    limit: int = 20,
    engine: BackupEngine = Depends(get_backup_engine),
) -> BackupListResponse:
    """List stored backups (paginated)."""
    all_manifests = await engine.list_backups()
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


@router.post(
    "",
    response_model=BackupManifestResponse,
    dependencies=[Depends(require_admin)],
)
async def create_backup(
    request: Request,
    body: BackupCreateRequest,
    engine: BackupEngine = Depends(get_backup_engine),
    audit: AuditEngine = Depends(get_audit_engine),
) -> BackupManifestResponse:
    """Create a new DHCP state backup."""
    manifest = await engine.create_backup(description=body.description)
    audit.record(
        "backup.create",
        request.client.host if request.client else "unknown",
        manifest.backup_id,
        body.description or "",
    )

    return BackupManifestResponse(
        backup_id=manifest.backup_id,
        created_at=manifest.created_at,
        source=manifest.source,
        description=manifest.description,
        scope_count=manifest.scope_count,
        reservation_count=manifest.reservation_count,
    )


@router.get("/{backup_id}", response_model=BackupDetailResponse)
async def get_backup(
    backup_id: str,
    engine: BackupEngine = Depends(get_backup_engine),
) -> BackupDetailResponse:
    """Get a specific backup with full scope data."""
    backup = await engine.get_backup(backup_id)

    from dataclasses import asdict

    return BackupDetailResponse(
        manifest=BackupManifestResponse(**asdict(backup.manifest)),
        scopes=[ScopeSnapshotResponse(**asdict(s)) for s in backup.scopes],
    )


@router.delete(
    "/{backup_id}",
    response_model=MessageResponse,
    dependencies=[Depends(require_admin)],
)
async def delete_backup(
    request: Request,
    backup_id: str,
    engine: BackupEngine = Depends(get_backup_engine),
    audit: AuditEngine = Depends(get_audit_engine),
) -> MessageResponse:
    """Delete a stored backup."""
    engine.delete_backup(backup_id)
    audit.record(
        "backup.delete",
        request.client.host if request.client else "unknown",
        backup_id,
    )
    return MessageResponse(message=f"Backup '{backup_id}' deleted")


@router.post(
    "/{backup_id}/restore",
    response_model=RestoreResponse,
    dependencies=[Depends(require_admin)],
)
async def restore_backup(
    request: Request,
    backup_id: str,
    body: RestoreRequest,
    engine: BackupEngine = Depends(get_backup_engine),
    audit: AuditEngine = Depends(get_audit_engine),
) -> RestoreResponse:
    """Restore DHCP state from a backup.

    Default is dry_run=True (preview changes without applying).
    """
    result = await engine.restore_backup(backup_id, dry_run=body.dry_run)
    if not body.dry_run:
        audit.record(
            "backup.restore",
            request.client.host if request.client else "unknown",
            backup_id,
            f"{result.get('total_changes', 0)} changes applied",
        )

    return RestoreResponse(
        backup_id=result["backup_id"],
        dry_run=result["dry_run"],
        changes=result["changes"],
        total_changes=result["total_changes"],
    )
