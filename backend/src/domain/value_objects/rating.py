"""Rating value object for Anki card reviews."""

from enum import IntEnum


class Rating(IntEnum):
    """Anki rating values (SM-2 ease buttons).

    Maps to Anki's 4-button rating system:
    - AGAIN (1): Failed recall, card goes to relearning
    - HARD (2): Recalled with difficulty, interval reduced
    - GOOD (3): Normal recall, standard interval increase
    - EASY (4): Perfect recall, interval bonus applied
    """

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4

    @classmethod
    def from_evaluation(cls, confidence: float, partial: bool = False) -> "Rating":
        """Map LLM evaluation to Anki rating.

        Args:
            confidence: Evaluation confidence 0.0-1.0
            partial: Whether answer was partially correct

        Returns:
            Appropriate Rating based on evaluation
        """
        if confidence < 0.3:
            return cls.AGAIN
        elif confidence < 0.6 or partial:
            return cls.HARD
        elif confidence < 0.9:
            return cls.GOOD
        else:
            return cls.EASY

    def __str__(self) -> str:
        return self.name.lower()
