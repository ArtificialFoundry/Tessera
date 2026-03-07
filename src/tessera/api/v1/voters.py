"""Voter registration and management endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from tessera.api.schemas import (
    KeyRotateResponse,
    RegistrationTokenInfo,
    RegistrationTokenListResponse,
    RegistrationTokenRequest,
    RegistrationTokenResponse,
    VoterApproveResponse,
    VoterInfoResponse,
    VoterListResponse,
    VoterRegisterRequest,
    VoterRegisterResponse,
    VoterRevokeResponse,
)
from tessera.deps import get_voter_registry, require_admin

if TYPE_CHECKING:
    from tessera.engines.voter_registry import VoterRegistryEngine

router = APIRouter()


@router.post(
    "/voters/tokens",
    response_model=RegistrationTokenResponse,
    dependencies=[Depends(require_admin)],
)
async def generate_token(
    body: RegistrationTokenRequest,
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> RegistrationTokenResponse:
    """Generate a one-time registration token."""
    try:
        token = registry.generate_token(
            bind_ip=body.bind_ip,
            ttl=body.ttl,
        )
    except ValueError as exc:
        from tessera.exceptions import ValidationError

        raise ValidationError(str(exc)) from exc
    return RegistrationTokenResponse(
        token=token.token,
        created_at=token.created_at,
        expires_at=token.expires_at,
        bind_ip=token.bind_ip,
    )


@router.get(
    "/voters/tokens",
    response_model=RegistrationTokenListResponse,
)
async def list_tokens(
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> RegistrationTokenListResponse:
    """List all registration tokens."""
    tokens = registry.list_tokens()
    return RegistrationTokenListResponse(
        tokens=[
            RegistrationTokenInfo(
                token=t.token,
                created_at=t.created_at,
                expires_at=t.expires_at,
                used=t.used,
                used_by=t.used_by,
                bind_ip=t.bind_ip,
            )
            for t in tokens
        ],
    )


@router.delete(
    "/voters/tokens/{token_prefix}",
    dependencies=[Depends(require_admin)],
)
async def delete_token(
    token_prefix: str,
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> dict[str, str]:
    """Delete a registration token by hash prefix."""
    registry.delete_token(token_prefix)
    return {"message": "Token deleted"}


@router.post(
    "/voters/register",
    response_model=VoterRegisterResponse,
)
async def register_voter(
    body: VoterRegisterRequest,
    request: Request,
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> VoterRegisterResponse:
    """Register a new voter using a one-time token."""
    source_ip = ""
    if request.client:
        source_ip = request.client.host

    record, psk = registry.register_voter(
        name=body.name,
        token_str=body.token,
        source_ip=source_ip,
        callback_url=body.callback_url,
    )

    return VoterRegisterResponse(
        voter_name=record.name,
        psk=psk,
        status=record.status,
    )


@router.get("/voters", response_model=VoterListResponse)
async def list_voters(
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> VoterListResponse:
    """List all registered voters."""
    voters = registry.list_voters()
    return VoterListResponse(
        voters=[
            VoterInfoResponse(
                name=v.name,
                registered_at=v.registered_at,
                approved_at=v.approved_at,
                status=v.status,
                last_vote=v.last_vote,
                ip_address=v.ip_address,
                bind_ip=v.bind_ip,
            )
            for v in voters
        ],
    )


@router.get(
    "/voters/pending",
    response_model=VoterListResponse,
)
async def list_pending(
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> VoterListResponse:
    """List pending voter registrations."""
    voters = registry.list_pending()
    return VoterListResponse(
        voters=[
            VoterInfoResponse(
                name=v.name,
                registered_at=v.registered_at,
                approved_at=v.approved_at,
                status=v.status,
                last_vote=v.last_vote,
                ip_address=v.ip_address,
                bind_ip=v.bind_ip,
            )
            for v in voters
        ],
    )


@router.post(
    "/voters/{name}/approve",
    response_model=VoterApproveResponse,
    dependencies=[Depends(require_admin)],
)
async def approve_voter(
    name: str,
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> VoterApproveResponse:
    """Approve a pending voter registration."""
    record, psk = registry.approve_voter(name)

    return VoterApproveResponse(
        voter_name=record.name,
        psk=psk,
        status=record.status,
    )


@router.post(
    "/voters/{name}/revoke",
    response_model=VoterRevokeResponse,
    dependencies=[Depends(require_admin)],
)
async def revoke_voter(
    name: str,
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> VoterRevokeResponse:
    """Revoke a voter."""
    record = registry.revoke_voter(name)

    return VoterRevokeResponse(
        voter_name=record.name,
        status=record.status,
    )


@router.delete(
    "/voters/{name}",
    dependencies=[Depends(require_admin)],
)
async def delete_voter(
    name: str,
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> dict[str, str]:
    """Permanently delete a voter record."""
    registry.delete_voter(name)
    return {"voter_name": name, "message": "Voter deleted"}


@router.post(
    "/voters/{name}/rotate-key",
    response_model=KeyRotateResponse,
    dependencies=[Depends(require_admin)],
)
async def rotate_key(
    name: str,
    registry: VoterRegistryEngine = Depends(get_voter_registry),
) -> KeyRotateResponse:
    """Rotate a voter's PSK with a grace period."""
    new_psk = registry.rotate_key(name)

    grace = registry._psk_grace_period
    return KeyRotateResponse(
        voter_name=name,
        new_psk=new_psk,
        grace_period=grace,
        message=f"PSK rotated. Old key valid for {grace}s.",
    )
