"""Card entity representing an Anki flashcard."""

from dataclasses import dataclass
from typing import TypedDict


class CardDict(TypedDict):
    """Card data structure for serialization."""

    id: int
    front: str
    back: str
    deck_name: str
    image_filename: str | None


@dataclass(frozen=True)
class Card:
    """Anki card entity.

    Represents a single flashcard from Anki with its content and scheduling state.

    Attributes:
        id: Unique card identifier from Anki
        deck_name: Name of the deck containing this card
        front: Question/prompt side of the card (HTML stripped)
        back: Answer side of the card (HTML stripped)
        image_filename: Optional media filename if card contains image
        card_type: Card type - 'basic' or 'cloze' (cloze deferred to future)
        queue: Anki queue - 0=new, 1=learning, 2=review
        due: Due value (interpretation depends on queue)
    """

    id: int
    deck_name: str
    front: str
    back: str
    image_filename: str | None = None
    card_type: str = "basic"
    queue: int = 0
    due: int = 0

    def has_image(self) -> bool:
        """Check if card contains an image."""
        return self.image_filename is not None

    def is_new(self) -> bool:
        """Check if card is new (never reviewed)."""
        return self.queue == 0

    def is_learning(self) -> bool:
        """Check if card is in learning phase."""
        return self.queue == 1

    def is_review(self) -> bool:
        """Check if card is in review phase."""
        return self.queue == 2

    def to_dict(self) -> CardDict:
        """Convert card to dictionary for state serialization."""
        return {
            "id": self.id,
            "front": self.front,
            "back": self.back,
            "deck_name": self.deck_name,
            "image_filename": self.image_filename,
        }
