"""Card management API routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from src.api.dependencies import FlashcardServiceDep, SessionManagerDep, rate_limit
from src.domain.services.session_manager import (
    SessionExpiredError,
    SessionNotFoundError,
)
from src.domain.value_objects.rating import Rating

router = APIRouter(prefix="/api/cards", tags=["cards"])


# =============================================================================
# Request/Response Models
# =============================================================================


class RateCardRequest(BaseModel):
    """Request body for rating a card."""

    rating: int = Field(..., ge=1, le=4, description="1=Again, 2=Hard, 3=Good, 4=Easy")
    session_id: str


class NextCardResponse(BaseModel):
    """Next card in the queue."""

    id: int
    question_html: str
    answer_html: str
    deck_name: str
    image_url: str | None = None


class RateCardResponse(BaseModel):
    """Response for card rating."""

    success: bool
    next_card: NextCardResponse | None
    remaining_count: int
    session_state: str


class SkipCardRequest(BaseModel):
    """Request body for skipping a card."""

    session_id: str


# =============================================================================
# Routes
# =============================================================================


@router.post(
    "/{card_id}/rate",
    response_model=RateCardResponse,
    responses={
        400: {"description": "Invalid rating or card mismatch"},
        401: {"description": "Session expired"},
        404: {"description": "Session not found"},
    },
)
async def rate_card(
    card_id: int,
    request: RateCardRequest,
    session_manager: SessionManagerDep,
    _: Annotated[None, Depends(rate_limit("/api/cards/{card_id}/rate"))],
) -> RateCardResponse:
    """Submit a rating for the current card.

    Records the rating and returns the next card in the queue.
    The rating will be synced to Anki when the session ends.
    """
    try:
        # Convert to Rating enum
        rating = Rating(request.rating)

        next_card, remaining = await session_manager.record_rating(
            session_id=request.session_id,
            card_id=card_id,
            rating=rating,
        )

        # Get session state
        session = session_manager.get_active_session()
        session_state = session.state.value if session else "unknown"

        # Convert next card to response
        next_card_response = None
        if next_card:
            next_card_response = NextCardResponse(
                id=next_card.id,
                question_html=next_card.front,
                answer_html=next_card.back,
                deck_name=next_card.deck_name,
                image_url=f"/api/cards/{next_card.id}/image" if next_card.image_filename else None,
            )

        return RateCardResponse(
            success=True,
            next_card=next_card_response,
            remaining_count=remaining,
            session_state=session_state,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_RATING",
                    "message": str(e),
                }
            },
        ) from None
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


@router.post(
    "/{card_id}/skip",
    response_model=RateCardResponse,
    responses={
        401: {"description": "Session expired"},
        404: {"description": "Session not found"},
    },
)
async def skip_card(
    card_id: int,
    request: SkipCardRequest,
    session_manager: SessionManagerDep,
) -> RateCardResponse:
    """Skip the current card and move it to end of queue.

    The card will be shown again after all other cards have been reviewed.
    """
    try:
        next_card, remaining = await session_manager.skip_card(request.session_id)

        # Get session state
        session = session_manager.get_active_session()
        session_state = session.state.value if session else "unknown"

        # Convert next card to response
        next_card_response = None
        if next_card:
            next_card_response = NextCardResponse(
                id=next_card.id,
                question_html=next_card.front,
                answer_html=next_card.back,
                deck_name=next_card.deck_name,
                image_url=f"/api/cards/{next_card.id}/image" if next_card.image_filename else None,
            )

        return RateCardResponse(
            success=True,
            next_card=next_card_response,
            remaining_count=remaining,
            session_state=session_state,
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


@router.get(
    "/{card_id}/image",
    responses={
        200: {"content": {"image/*": {}}},
        404: {"description": "Image not found"},
    },
)
async def get_card_image(
    card_id: int,
    session_manager: SessionManagerDep,
    flashcard_service: FlashcardServiceDep,
) -> Response:
    """Get card image from Anki media.

    Proxies image requests to AnkiConnect and caches the response.
    Returns a placeholder if the image is not found.
    """
    # Get active session to find the card's image filename
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

    # Find the card in the session
    card = None
    for c in session.cards:
        if c.id == card_id:
            card = c
            break

    if card is None or card.image_filename is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "IMAGE_NOT_FOUND",
                    "message": "Card has no image",
                }
            },
        )

    # Fetch image from AnkiConnect
    image_data = await flashcard_service.get_card_image(card.image_filename)

    if image_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "IMAGE_NOT_FOUND",
                    "message": "Image file not found in Anki media",
                }
            },
        )

    # Determine content type from filename
    filename_lower = card.image_filename.lower()
    if filename_lower.endswith(".png"):
        content_type = "image/png"
    elif filename_lower.endswith((".jpg", ".jpeg")):
        content_type = "image/jpeg"
    elif filename_lower.endswith(".gif"):
        content_type = "image/gif"
    elif filename_lower.endswith(".webp"):
        content_type = "image/webp"
    else:
        content_type = "image/png"  # Default

    return Response(
        content=image_data,
        media_type=content_type,
        headers={
            "Cache-Control": "max-age=3600",  # 1 hour cache
        },
    )
