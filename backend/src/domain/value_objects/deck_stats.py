"""Deck statistics value object."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeckStats:
    """Immutable value object representing card counts for a deck.

    Represents the three card categories that Anki tracks:
    - new: Cards never reviewed
    - learn: Cards in learning/relearning phase
    - due: Cards due for review
    """

    name: str
    new_count: int
    learn_count: int
    due_count: int

    @property
    def total_count(self) -> int:
        """Total cards available for study."""
        return self.new_count + self.learn_count + self.due_count

    @property
    def has_cards(self) -> bool:
        """Whether deck has any cards available."""
        return self.total_count > 0
