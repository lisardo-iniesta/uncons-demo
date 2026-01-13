"""Port interface for flashcard service (Anki integration)."""

from typing import Protocol, runtime_checkable

from src.domain.entities.card import Card
from src.domain.value_objects.deck_stats import DeckStats
from src.domain.value_objects.rating import Rating


@runtime_checkable
class FlashcardService(Protocol):
    """Port for flashcard operations.

    Abstracts the flashcard backend (AnkiConnect in production).
    Follows hexagonal architecture - domain doesn't know about Anki.
    """

    async def get_decks(self) -> list[str]:
        """Get list of deck names (flat, no hierarchy).

        Returns:
            List of deck names sorted alphabetically
        """
        ...

    async def get_due_cards(self, deck_name: str) -> list[Card]:
        """Get due cards for deck (review cards only).

        Args:
            deck_name: Name of the deck to query

        Returns:
            List of review cards due today
        """
        ...

    async def get_reviewable_cards(self, deck_name: str) -> list[Card]:
        """Get all reviewable cards (new, learning, due) for deck.

        Args:
            deck_name: Name of the deck to query

        Returns:
            List of cards available for study, ordered by priority:
            learning → due → new
        """
        ...

    async def get_next_card(self, deck_name: str) -> Card | None:
        """Get next due card from deck.

        Args:
            deck_name: Name of the deck to query

        Returns:
            Next card to review, or None if queue is empty
        """
        ...

    async def submit_review(self, card_id: int, rating: Rating) -> None:
        """Submit review rating for card.

        Args:
            card_id: ID of the card being reviewed
            rating: User's rating (1-4)
        """
        ...

    async def get_card_image(self, filename: str) -> bytes | None:
        """Get image file content (decoded from Base64).

        Args:
            filename: Media filename (e.g., 'image.jpg')

        Returns:
            Image bytes, or None if not found
        """
        ...

    async def sync(self) -> bool:
        """Trigger AnkiWeb sync.

        Returns:
            True if sync succeeded, False otherwise
        """
        ...

    async def get_decks_with_due_count(self) -> list[tuple[str, int]]:
        """Get decks sorted by due count (descending).

        Returns:
            List of (deck_name, due_count) tuples sorted by count
        """
        ...

    async def get_decks_with_card_counts(self) -> list[DeckStats]:
        """Get decks with new, learn, due counts sorted by total (descending).

        Returns:
            List of DeckStats with all card category counts
        """
        ...
