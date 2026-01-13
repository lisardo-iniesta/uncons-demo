"""Voice transcription value objects."""

from dataclasses import dataclass
from enum import Enum


class TranscriptConfidence(Enum):
    """Confidence levels for transcript quality."""

    HIGH = "high"  # >= 0.9 - proceed with evaluation
    MEDIUM = "medium"  # 0.7-0.9 - proceed but may clarify
    LOW = "low"  # < 0.7 - ask user to repeat

    @classmethod
    def from_score(cls, score: float) -> "TranscriptConfidence":
        """Convert numeric confidence score to level."""
        if score >= 0.9:
            return cls.HIGH
        elif score >= 0.7:
            return cls.MEDIUM
        else:
            return cls.LOW


@dataclass(frozen=True)
class SpeechSegment:
    """A segment of transcribed speech with timing."""

    text: str
    start_time_ms: int
    end_time_ms: int
    confidence: float

    @property
    def duration_ms(self) -> int:
        return self.end_time_ms - self.start_time_ms


@dataclass(frozen=True)
class Transcript:
    """Complete transcript of a user utterance."""

    text: str
    confidence: float
    is_final: bool
    segments: tuple[SpeechSegment, ...] = ()

    @property
    def confidence_level(self) -> TranscriptConfidence:
        return TranscriptConfidence.from_score(self.confidence)

    @property
    def needs_clarification(self) -> bool:
        """True if confidence is too low and we should ask user to repeat."""
        return self.confidence_level == TranscriptConfidence.LOW

    @property
    def duration_ms(self) -> int:
        """Total duration of all segments."""
        if not self.segments:
            return 0
        return self.segments[-1].end_time_ms - self.segments[0].start_time_ms
