"""
Evaluation Result Value Object.

Represents the result of evaluating a user's spoken answer.
Immutable data structure used between EvaluationService and consumers.
"""

from dataclasses import dataclass
from typing import Literal

from src.domain.constants import FeedbackMessages


@dataclass(frozen=True)
class EvaluationResult:
    """Result of LLM-based answer evaluation.

    Attributes:
        reasoning: Chain-of-thought reasoning (generated first for accuracy)
        corrected_transcript: ASR-corrected transcript if phonetic errors detected
        is_semantically_correct: Whether the answer conveys the correct concept
        fluency_score: Delivery quality (1=poor, 4=excellent)
        rating: Final Anki rating (1=Again, 2=Hard, 3=Good, 4=Easy)
        feedback: Brief encouraging feedback for TTS (1-2 sentences)
        enter_socratic_mode: Whether to enter guided questioning mode
        socratic_prompt: Guiding question if socratic mode (None otherwise)
        answer_summary: 1-2 sentence summary of WHY the answer matters
    """

    reasoning: str
    corrected_transcript: str | None
    is_semantically_correct: bool
    fluency_score: Literal[1, 2, 3, 4]
    rating: Literal[1, 2, 3, 4]
    feedback: str
    enter_socratic_mode: bool
    answer_summary: str
    socratic_prompt: str | None = None

    def __post_init__(self) -> None:
        """Validate consistency of fields."""
        if self.enter_socratic_mode and not self.socratic_prompt:
            raise ValueError("socratic_prompt required when enter_socratic_mode is True")
        if not self.enter_socratic_mode and self.socratic_prompt:
            raise ValueError("socratic_prompt should be None when enter_socratic_mode is False")
        if self.rating < 1 or self.rating > 4:
            raise ValueError(f"rating must be 1-4, got {self.rating}")
        if self.fluency_score < 1 or self.fluency_score > 4:
            raise ValueError(f"fluency_score must be 1-4, got {self.fluency_score}")

    @property
    def is_correct(self) -> bool:
        """Alias for is_semantically_correct for convenience."""
        return self.is_semantically_correct

    @property
    def needs_explanation(self) -> bool:
        """Whether the answer requires explanation (rating <= 2)."""
        return self.rating <= 2

    @property
    def is_timeout(self) -> bool:
        """Check if this result represents a timeout (no response)."""
        return "timeout" in self.reasoning.lower()

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "reasoning": self.reasoning,
            "corrected_transcript": self.corrected_transcript,
            "is_semantically_correct": self.is_semantically_correct,
            "fluency_score": self.fluency_score,
            "rating": self.rating,
            "feedback": self.feedback,
            "enter_socratic_mode": self.enter_socratic_mode,
            "socratic_prompt": self.socratic_prompt,
            "answer_summary": self.answer_summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationResult":
        """Create from dictionary."""
        return cls(
            reasoning=data["reasoning"],
            corrected_transcript=data.get("corrected_transcript"),
            is_semantically_correct=data["is_semantically_correct"],
            fluency_score=data["fluency_score"],
            rating=data["rating"],
            feedback=data["feedback"],
            enter_socratic_mode=data["enter_socratic_mode"],
            answer_summary=data.get("answer_summary", ""),
            socratic_prompt=data.get("socratic_prompt"),
        )

    @classmethod
    def timeout_result(cls, answer_summary: str = "") -> "EvaluationResult":
        """Factory method for timeout result (Rating 1)."""
        return cls(
            reasoning="User did not respond within timeout period",
            corrected_transcript=None,
            is_semantically_correct=False,
            fluency_score=1,
            rating=1,
            feedback=FeedbackMessages.TIMEOUT,
            enter_socratic_mode=False,
            answer_summary=answer_summary,
            socratic_prompt=None,
        )

    @classmethod
    def skip_result(cls, answer_summary: str = "") -> "EvaluationResult":
        """Factory method for explicit skip/don't know (Rating 1)."""
        return cls(
            reasoning="User indicated they don't know the answer",
            corrected_transcript=None,
            is_semantically_correct=False,
            fluency_score=1,
            rating=1,
            feedback=FeedbackMessages.SKIP,
            enter_socratic_mode=False,
            answer_summary=answer_summary,
            socratic_prompt=None,
        )
