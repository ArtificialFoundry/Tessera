"""Failover status and vote submission endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from tessera.api.schemas import (
    FailoverStatusResponse,
    PaginationMeta,
    TransitionInfo,
    VoteRequest,
    VoteResponse,
    VoterInfo,
)
from tessera.deps import get_failover_engine
from tessera.exceptions import AuthenticationError, RateLimitError

if TYPE_CHECKING:
    from tessera.engines.failover import FailoverEngine

router = APIRouter()


@router.post("/vote", response_model=VoteResponse)
async def submit_vote(
    payload: VoteRequest,
    failover: FailoverEngine = Depends(get_failover_engine),
) -> VoteResponse:
    """Submit a voter health check."""
    try:
        vote = failover.submit_vote(
            voter=payload.voter,
            status=payload.status,
            timestamp_val=payload.timestamp,
            signature=payload.signature,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(int(exc.retry_after) + 1)},
        ) from exc

    await failover.evaluate_quorum()

    return VoteResponse(
        accepted=True,
        voter=vote.voter,
        status=vote.status.value,
    )


@router.get("/status", response_model=FailoverStatusResponse)
async def failover_status(
    transitions_offset: int = 0,
    transitions_limit: int = 20,
    failover: FailoverEngine = Depends(get_failover_engine),
) -> FailoverStatusResponse:
    """Get current failover status, votes, and transition history."""
    evaluation = await failover.evaluate_quorum()
    votes = failover.votes
    all_transitions = failover.transitions
    total = len(all_transitions)
    page = all_transitions[transitions_offset : transitions_offset + transitions_limit]

    return FailoverStatusResponse(
        state=evaluation["state"],
        has_quorum=evaluation["has_quorum"],
        active_votes=evaluation["active_votes"],
        up_count=evaluation["up_count"],
        down_count=evaluation["down_count"],
        consecutive_down=evaluation["consecutive_down"],
        consecutive_up=evaluation["consecutive_up"],
        voters={
            name: VoterInfo(
                voter=v.voter,
                status=v.status.value,
                timestamp=v.timestamp,
                received_at=v.received_at,
            )
            for name, v in votes.items()
        },
        transitions=[
            TransitionInfo(
                from_state=t.from_state.value,
                to_state=t.to_state.value,
                timestamp=t.timestamp,
                reason=t.reason,
            )
            for t in page
        ],
        transitions_pagination=PaginationMeta(
            total=total,
            offset=transitions_offset,
            limit=transitions_limit,
        ),
        config=failover.config,
    )
