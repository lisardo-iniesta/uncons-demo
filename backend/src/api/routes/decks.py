"""Deck management API routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.api.dependencies import FlashcardServiceDep, rate_limit

router = APIRouter(prefix="/api/decks", tags=["decks"])


# =============================================================================
# Response Models
# =============================================================================


class DeckInfo(BaseModel):
    """Deck with card counts by category."""

    name: str
    new_count: int
    learn_count: int
    due_count: int
    total_count: int


class DecksResponse(BaseModel):
    """Response for deck listing."""

    decks: list[DeckInfo]


# =============================================================================
# Routes
# =============================================================================


@router.get(
    "",
    response_model=DecksResponse,
    responses={
        503: {"description": "Anki unavailable"},
    },
)
async def list_decks(
    flashcard_service: FlashcardServiceDep,
    _: Annotated[None, Depends(rate_limit("/api/decks"))],
) -> DecksResponse:
    """List all decks with card counts (new, learn, due).

    Returns decks sorted by total count (highest first).
    """
    try:
        deck_stats = await flashcard_service.get_decks_with_card_counts()

        return DecksResponse(
            decks=[
                DeckInfo(
                    name=stats.name,
                    new_count=stats.new_count,
                    learn_count=stats.learn_count,
                    due_count=stats.due_count,
                    total_count=stats.total_count,
                )
                for stats in deck_stats
            ]
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "ANKI_UNAVAILABLE",
                    "message": f"Could not connect to Anki: {str(e)}",
                }
            },
        ) from None
