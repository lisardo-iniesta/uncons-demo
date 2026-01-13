"""Local test deck adapter for MVP development and testing.

This adapter bypasses AnkiConnect by loading cards from an embedded JSON file.
Use FLASHCARD_ADAPTER=local to enable.
"""

import json
from importlib import resources
from pathlib import Path

from src.domain.entities.card import Card
from src.domain.value_objects.deck_stats import DeckStats
from src.domain.value_objects.rating import Rating


class LocalTestDeckAdapter:
    """FlashcardService implementation with embedded test cards.

    All cards are always "due" - no SRS simulation.
    Ratings are accepted but not persisted between restarts.

    This adapter is useful for:
    - Development without Anki running
    - E2E testing without Anki dependency
    - Demo environments
    """

    def __init__(self) -> None:
        self._cards: list[Card] = self._load_cards()
        self._reviews: dict[int, Rating] = {}  # In-memory only

    def _load_cards(self) -> list[Card]:
        """Load cards from embedded JSON data.

        Uses importlib.resources for reliable package data access.
        Falls back to file path if running outside package context.
        """
        try:
            # Try importlib.resources first (works in installed packages)
            data_files = resources.files("src.adapters.data")
            data_path = data_files.joinpath("test_deck.json")
            with data_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (ModuleNotFoundError, FileNotFoundError, TypeError):
            # Fallback: read from file path (development mode)
            file_path = Path(__file__).parent / "data" / "test_deck.json"
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)

        cards = []
        for deck in data["decks"]:
            for card_data in deck["cards"]:
                cards.append(
                    Card(
                        id=card_data["id"],
                        deck_name=deck["name"],
                        front=card_data["front"],
                        back=card_data["back"],
                        image_filename=None,
                        card_type="basic",
                        queue=2,  # All cards are "review" queue (always due)
                        due=0,
                    )
                )
        return cards

    async def get_decks(self) -> list[str]:
        """Get list of deck names from test data."""
        return sorted({c.deck_name for c in self._cards})

    async def get_due_cards(self, deck_name: str) -> list[Card]:
        """Get due cards (same as reviewable for test deck)."""
        return await self.get_reviewable_cards(deck_name)

    async def get_reviewable_cards(self, deck_name: str) -> list[Card]:
        """Get all reviewable cards for deck.

        Args:
            deck_name: Deck to query. Use "All" or "" for all cards.

        Returns:
            List of cards available for study.
        """
        if deck_name == "All" or deck_name == "":
            return self._cards.copy()
        return [c for c in self._cards if c.deck_name == deck_name]

    async def get_next_card(self, deck_name: str) -> Card | None:
        """Get next card from deck."""
        cards = await self.get_reviewable_cards(deck_name)
        return cards[0] if cards else None

    async def submit_review(self, card_id: int, rating: Rating) -> None:
        """Accept rating (stored in memory only, not persisted)."""
        self._reviews[card_id] = rating

    async def get_card_image(self, filename: str) -> bytes | None:
        """No images in test deck."""
        return None

    async def sync(self) -> bool:
        """No-op sync (always succeeds)."""
        return True

    async def get_decks_with_due_count(self) -> list[tuple[str, int]]:
        """Get decks sorted by due count."""
        deck_counts: dict[str, int] = {}
        for card in self._cards:
            deck_counts[card.deck_name] = deck_counts.get(card.deck_name, 0) + 1
        return sorted(deck_counts.items(), key=lambda x: -x[1])

    async def get_decks_with_card_counts(self) -> list[DeckStats]:
        """Get decks with card counts."""
        deck_counts: dict[str, int] = {}
        for card in self._cards:
            deck_counts[card.deck_name] = deck_counts.get(card.deck_name, 0) + 1
        return [
            DeckStats(name=name, new_count=0, learn_count=0, due_count=count)
            for name, count in sorted(deck_counts.items(), key=lambda x: -x[1])
        ]

    async def wait_for_connection(self, timeout: float = 30.0) -> bool:
        """Always connected (no external dependency)."""
        return True

    async def close(self) -> None:
        """No-op cleanup."""
        pass
