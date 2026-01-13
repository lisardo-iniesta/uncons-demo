"""Session management API routes."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from livekit.api import LiveKitAPI
from pydantic import BaseModel

from src.api.dependencies import SessionManagerDep, get_recovery_store, rate_limit
from src.api.routes.livekit import clear_room_dispatch
from src.config import get_livekit_api_key, get_livekit_api_secret, get_livekit_url
from src.domain.services.session_manager import (
    SessionConflictError,
    SessionExpiredError,
    SessionNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session", tags=["session"])


# =============================================================================
# Request/Response Models
# =============================================================================


class StartSessionRequest(BaseModel):
    """Request body for starting a session."""

    deck_name: str


class CardResponse(BaseModel):
    """Card in API response."""

    id: int
    question_html: str
    answer_html: str
    deck_name: str
    image_url: str | None = None


class StartSessionResponse(BaseModel):
    """Response for session start."""

    session_id: str
    deck_name: str
    state: str
    due_count: int
    cards: list[CardResponse]
    recovered_ratings: int


class EndSessionRequest(BaseModel):
    """Request body for ending a session."""

    session_id: str


class SessionStatsResponse(BaseModel):
    """Session statistics."""

    cards_reviewed: int
    ratings: dict[str, int]
    synced_count: int
    failed_count: int
    duration_minutes: float


class EndSessionResponse(BaseModel):
    """Response for session end."""

    session_id: str
    state: str
    stats: SessionStatsResponse
    warning: str | None = None


class CurrentSessionResponse(BaseModel):
    """Response for current session."""

    session_id: str
    deck_name: str
    state: str
    current_card: CardResponse | None
    remaining_count: int
    cards_reviewed: int


class ErrorDetail(BaseModel):
    """Error detail structure."""

    code: str
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    """Error response wrapper."""

    error: ErrorDetail


# =============================================================================
# Routes
# =============================================================================


@router.post(
    "/start",
    response_model=StartSessionResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Session conflict"},
        503: {"model": ErrorResponse, "description": "Anki unavailable"},
    },
)
async def start_session(
    request: StartSessionRequest,
    session_manager: SessionManagerDep,
    _: Annotated[None, Depends(rate_limit("/api/session/start"))],
) -> StartSessionResponse:
    """Start a new review session.

    Fetches due cards from Anki and creates a new session.
    Only one active session is allowed at a time.
    """
    try:
        result = await session_manager.start_session(request.deck_name)
        session = result.session

        # Convert cards to response format
        cards = [
            CardResponse(
                id=card.id,
                question_html=card.front,
                answer_html=card.back,
                deck_name=card.deck_name,
                image_url=f"/api/cards/{card.id}/image" if card.image_filename else None,
            )
            for card in session.cards
        ]

        return StartSessionResponse(
            session_id=session.id,
            deck_name=session.deck_name,
            state=session.state.value,
            due_count=len(session.cards),
            cards=cards,
            recovered_ratings=result.recovered_ratings,
        )

    except SessionConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "SESSION_CONFLICT",
                    "message": "Another session is active",
                    "details": {
                        "existing_session_id": e.existing_session_id,
                        "started_at": e.started_at.isoformat(),
                    },
                }
            },
        ) from None
    except Exception as e:
        # AnkiConnect unavailable or other error
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "ANKI_UNAVAILABLE",
                    "message": f"Could not connect to Anki: {str(e)}",
                }
            },
        ) from None


@router.post(
    "/end",
    response_model=EndSessionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        401: {"model": ErrorResponse, "description": "Session expired"},
    },
)
async def end_session(
    request: EndSessionRequest,
    session_manager: SessionManagerDep,
    _: Annotated[None, Depends(rate_limit("/api/session/end"))],
) -> EndSessionResponse:
    """End the active session and sync ratings to Anki.

    Returns session statistics and any warnings about failed syncs.
    """
    try:
        result = await session_manager.end_session(request.session_id)

        # Clear the room from LiveKit dispatch cache so new sessions can get fresh dispatches
        room_name = f"session-{request.session_id}"
        if clear_room_dispatch(room_name):
            logger.info(f"Cleared LiveKit dispatch cache for room: {room_name}")

        # Build stats response
        stats = SessionStatsResponse(
            cards_reviewed=result.stats.get("cards_reviewed", 0),
            ratings=result.stats.get("ratings", {}),
            synced_count=result.stats.get("synced_count", 0),
            failed_count=result.stats.get("failed_count", 0),
            duration_minutes=result.stats.get("duration_minutes", 0.0),
        )

        return EndSessionResponse(
            session_id=result.session_id,
            state=result.state.value,
            stats=stats,
            warning=result.warning,
        )

    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "SESSION_NOT_FOUND",
                    "message": "Session not found or already ended",
                }
            },
        ) from None
    except SessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "SESSION_EXPIRED",
                    "message": "Session has timed out due to inactivity",
                }
            },
        ) from None


@router.head(
    "/current",
    responses={
        204: {"description": "Session exists"},
        404: {"description": "No active session"},
    },
)
async def head_current_session(
    session_manager: SessionManagerDep,
) -> None:
    """Check if an active session exists (HEAD request, no body).

    Returns 204 if session exists, 404 if not.
    Used by frontend to check session existence without triggering error logs.
    """
    session = session_manager.get_active_session()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Return 204 No Content (success with no body)
    from fastapi.responses import Response

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/current",
    response_model=CurrentSessionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "No active session"},
        401: {"model": ErrorResponse, "description": "Session expired"},
    },
)
async def get_current_session(
    session_manager: SessionManagerDep,
) -> CurrentSessionResponse:
    """Get the current active session.

    Returns session state, current card, and progress.
    Used by frontend to resume after refresh.
    """
    try:
        session = session_manager.get_active_session()

        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "SESSION_NOT_FOUND",
                        "message": "No active session",
                    }
                },
            )

        # Get current card
        current_card = session.get_current_card()
        current_card_response = None
        if current_card:
            current_card_response = CardResponse(
                id=current_card.id,
                question_html=current_card.front,
                answer_html=current_card.back,
                deck_name=current_card.deck_name,
                image_url=(
                    f"/api/cards/{current_card.id}/image" if current_card.image_filename else None
                ),
            )

        return CurrentSessionResponse(
            session_id=session.id,
            deck_name=session.deck_name,
            state=session.state.value,
            current_card=current_card_response,
            remaining_count=session.get_remaining_count(),
            cards_reviewed=len(session.pending_ratings),
        )

    except SessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "SESSION_EXPIRED",
                    "message": "Session has timed out due to inactivity",
                }
            },
        ) from None


class ForceEndResponse(BaseModel):
    """Response for force-end endpoint."""

    ended_sessions: int
    message: str


@router.delete(
    "/force-end",
    response_model=ForceEndResponse,
    responses={
        403: {"model": ErrorResponse, "description": "Not available in production"},
    },
)
async def force_end_session(
    session_manager: SessionManagerDep,
) -> ForceEndResponse:
    """Force-end any active session (DEV ONLY).

    This endpoint is only available in development mode.
    Used to recover from stale sessions during testing.
    """
    import os

    if os.getenv("ENVIRONMENT", "development") == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not available in production",
                }
            },
        )

    # Get active session IDs before force-ending (to clear dispatch cache)
    active_sessions = (
        session_manager.get_active_session_ids()
        if hasattr(session_manager, "get_active_session_ids")
        else []
    )

    count = await session_manager.force_end_all_sessions()

    # Clear dispatch cache for all force-ended sessions
    for session_id in active_sessions:
        room_name = f"session-{session_id}"
        if clear_room_dispatch(room_name):
            logger.info(f"Cleared LiveKit dispatch cache for force-ended room: {room_name}")

    return ForceEndResponse(
        ended_sessions=count,
        message=f"Force-ended {count} session(s)",
    )


class ResetTestStateResponse(BaseModel):
    """Response for reset-test-state endpoint."""

    status: str
    ended_sessions: int
    cleared_dispatches: int
    stale_sessions_reset: int
    deck_cards_reset: int
    rooms_deleted: int
    message: str


async def reset_e2e_deck_scheduling() -> int:
    """Reset E2E test deck to clean state with exactly 3 cards.

    If the deck has != 3 cards, deletes all and recreates fresh.
    Otherwise, uses forgetCards + setDueDate to reset scheduling.

    Returns number of cards reset, or 0 if failed.
    """
    import httpx

    anki_connect_url = "http://localhost:8765"
    test_deck_name = "E2E Test Deck"
    test_cards = [
        {"front": "E2E Test Question 1: What is 1+1?", "back": "2"},
        {"front": "E2E Test Question 2: What color is the sky?", "back": "Blue"},
        {"front": "E2E Test Question 3: How many days in a week?", "back": "7"},
    ]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Find all cards in test deck
            find_response = await client.post(
                anki_connect_url,
                json={
                    "action": "findCards",
                    "version": 6,
                    "params": {"query": f'deck:"{test_deck_name}"'},
                },
            )
            find_result = find_response.json()
            if find_result.get("error"):
                logger.warning(f"Failed to find cards: {find_result['error']}")
                return 0

            cards = find_result.get("result", [])

            # If we have exactly 3 cards, just reset scheduling
            if len(cards) == 3:
                # Use forgetCards to reset cards to "new" state
                forget_response = await client.post(
                    anki_connect_url,
                    json={"action": "forgetCards", "version": 6, "params": {"cards": cards}},
                )
                forget_result = forget_response.json()
                if forget_result.get("error"):
                    logger.warning(f"forgetCards failed: {forget_result['error']}")

                # Also set due date
                reset_response = await client.post(
                    anki_connect_url,
                    json={
                        "action": "setDueDate",
                        "version": 6,
                        "params": {"cards": cards, "days": "0"},
                    },
                )
                if reset_response.json().get("error"):
                    logger.warning(f"setDueDate failed: {reset_response.json()['error']}")

                logger.info(f"Reset {len(cards)} E2E test deck cards to due")
                return len(cards)

            # Wrong number of cards - delete all and recreate fresh
            logger.warning(f"E2E deck has {len(cards)} cards (expected 3), recreating fresh")

            # Get note IDs and delete them
            if cards:
                notes_response = await client.post(
                    anki_connect_url,
                    json={"action": "cardsToNotes", "version": 6, "params": {"cards": cards}},
                )
                notes = notes_response.json().get("result", [])
                if notes:
                    await client.post(
                        anki_connect_url,
                        json={
                            "action": "deleteNotes",
                            "version": 6,
                            "params": {"notes": list(set(notes))},
                        },
                    )
                    logger.info(f"Deleted {len(set(notes))} existing notes")

            # Add fresh cards
            notes_to_add = [
                {
                    "deckName": test_deck_name,
                    "modelName": "Basic",
                    "fields": {"Front": card["front"], "Back": card["back"]},
                    "options": {"allowDuplicate": False},
                }
                for card in test_cards
            ]
            add_response = await client.post(
                anki_connect_url,
                json={"action": "addNotes", "version": 6, "params": {"notes": notes_to_add}},
            )
            add_result = add_response.json()
            added = [n for n in add_result.get("result", []) if n]
            logger.info(f"Added {len(added)} fresh E2E test cards")

            return len(added)

    except Exception as e:
        logger.warning(f"Failed to reset E2E deck: {e}")
        return 0


async def delete_all_livekit_rooms() -> int:
    """Delete all LiveKit rooms to ensure clean state between tests.

    Returns number of rooms deleted.
    """
    import asyncio

    api_key = get_livekit_api_key()
    api_secret = get_livekit_api_secret()
    livekit_url = get_livekit_url()

    if not api_key or not api_secret or not livekit_url:
        logger.warning("LiveKit not configured, skipping room cleanup")
        return 0

    deleted_count = 0
    try:
        # Convert wss:// to https:// for API calls
        api_url = livekit_url.replace("wss://", "https://").replace("ws://", "http://")
        lk_api = LiveKitAPI(api_url, api_key, api_secret)

        # List all rooms
        rooms = await lk_api.room.list_rooms()
        session_rooms = [r for r in rooms if r.name.startswith("session-")]
        logger.info(
            f"Found {len(session_rooms)} session rooms to delete: {[r.name for r in session_rooms]}"
        )

        # Delete rooms that match session pattern
        for room in session_rooms:
            try:
                await lk_api.room.delete_room(room.name)
                deleted_count += 1
                logger.info(f"Deleted LiveKit room: {room.name}")
            except Exception as e:
                logger.warning(f"Failed to delete room {room.name}: {e}")

        await lk_api.aclose()

        # Wait for agents to fully disconnect after room deletion
        if deleted_count > 0:
            logger.info(f"Waiting 2s for {deleted_count} agents to disconnect...")
            await asyncio.sleep(2)

    except Exception as e:
        logger.warning(f"Failed to clean up LiveKit rooms: {e}")

    return deleted_count


@router.post(
    "/reset-test-state",
    response_model=ResetTestStateResponse,
    responses={
        403: {"model": ErrorResponse, "description": "Not available in production"},
    },
)
async def reset_test_state(
    session_manager: SessionManagerDep,
) -> ResetTestStateResponse:
    """Reset all test state between E2E tests (DEV ONLY).

    This endpoint is only available in development mode.
    Used to ensure clean state between E2E tests.

    Actions:
    - Force-ends any active sessions
    - Clears LiveKit dispatch cache for all rooms
    - Resets stale sessions in recovery store (marks as crashed)
    """
    import os

    if os.getenv("ENVIRONMENT", "development") == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not available in production",
                }
            },
        )

    # Get active session IDs before force-ending
    active_sessions = (
        session_manager.get_active_session_ids()
        if hasattr(session_manager, "get_active_session_ids")
        else []
    )

    # Force-end all sessions
    ended_count = await session_manager.force_end_all_sessions()

    # Delete all LiveKit rooms FIRST (critical: disconnects agents before clearing dispatch cache)
    rooms_deleted = await delete_all_livekit_rooms()

    # Clear dispatch cache for all sessions
    cleared_count = 0
    for session_id in active_sessions:
        room_name = f"session-{session_id}"
        if clear_room_dispatch(room_name):
            cleared_count += 1
            logger.info(f"Cleared LiveKit dispatch cache for room: {room_name}")

    # Reset stale sessions in recovery store (critical for E2E tests)
    # This prevents worker from recovering old sessions from previous test runs
    recovery_store = get_recovery_store()
    stale_reset_count = await recovery_store.reset_stale_processing()
    if stale_reset_count > 0:
        logger.info(f"Reset {stale_reset_count} stale sessions in recovery store")

    # Reset E2E test deck card scheduling (makes all cards due again)
    deck_reset_count = await reset_e2e_deck_scheduling()

    logger.info(
        f"Reset test state: ended {ended_count} sessions, cleared {cleared_count} dispatches, deleted {rooms_deleted} rooms, reset {stale_reset_count} stale, reset {deck_reset_count} deck cards"
    )

    return ResetTestStateResponse(
        status="reset",
        ended_sessions=ended_count,
        cleared_dispatches=cleared_count,
        stale_sessions_reset=stale_reset_count,
        deck_cards_reset=deck_reset_count,
        rooms_deleted=rooms_deleted,
        message=f"Reset complete: ended {ended_count} session(s), cleared {cleared_count} dispatch(es), deleted {rooms_deleted} room(s), reset {stale_reset_count} stale session(s), reset {deck_reset_count} deck card(s)",
    )
