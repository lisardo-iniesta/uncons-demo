"""
Turn Detection Service.

Determines when the user has finished speaking and is ready for evaluation.
Handles silence thresholds, filler word detection, and multi-part answers.

Thresholds (modified from spec 002):
- 300ms silence → utterance complete
- 2.0s silence → thinking time (just wait)
- NO 4.0s encouragement (removed per user request)
- 30.0s timeout → treat as "don't know" (60s if engagement detected)

Low confidence (<0.7) → ask "Could you please repeat?"
"""

from dataclasses import dataclass
from enum import Enum

from src.domain.constants import (
    CONFIDENCE_CLARIFY_THRESHOLD,
    CONTINUATION_PHRASES,
    DONE_MARKERS,
    FILLER_WORDS,
    SILENCE_THINKING_MS,
    SILENCE_TIMEOUT_MS,
    SILENCE_UTTERANCE_END_MS,
)
from src.domain.value_objects.transcript import Transcript


class TurnStatus(str, Enum):
    """Status of the current turn."""

    LISTENING = "listening"  # Still waiting for input
    UTTERANCE_COMPLETE = "utterance_complete"  # User finished a phrase
    THINKING = "thinking"  # User is thinking (wait)
    TIMEOUT = "timeout"  # User didn't respond
    NEEDS_CLARIFICATION = "needs_clarification"  # Ask to repeat


@dataclass(frozen=True)
class TurnDetectionResult:
    """Result of turn detection analysis (immutable value object)."""

    status: TurnStatus
    should_process: bool
    needs_clarification: bool
    clarification_reason: str | None
    detected_filler: bool
    detected_continuation: bool
    detected_done_marker: bool


class TurnDetector:
    """
    Detects when a user's turn is complete.

    Analyzes silence duration, transcript confidence, and linguistic cues
    to determine the appropriate action.
    """

    def __init__(
        self,
        silence_utterance_end_ms: int = SILENCE_UTTERANCE_END_MS,
        silence_thinking_ms: int = SILENCE_THINKING_MS,
        silence_timeout_ms: int = SILENCE_TIMEOUT_MS,
        confidence_threshold: float = CONFIDENCE_CLARIFY_THRESHOLD,
    ) -> None:
        self.silence_utterance_end_ms = silence_utterance_end_ms
        self.silence_thinking_ms = silence_thinking_ms
        self.silence_timeout_ms = silence_timeout_ms
        self.confidence_threshold = confidence_threshold

    def analyze(
        self,
        transcript: Transcript | None,
        silence_duration_ms: int,
        clarification_count: int = 0,
    ) -> TurnDetectionResult:
        """
        Analyze the current state and determine turn status.

        Args:
            transcript: Current transcript (may be None if no speech yet)
            silence_duration_ms: Duration of silence since last speech
            clarification_count: How many times we've asked for clarification

        Returns:
            TurnDetectionResult with status and action recommendations
        """
        # Check for timeout (30 seconds default, 60 with engagement)
        if silence_duration_ms >= self.silence_timeout_ms:
            return TurnDetectionResult(
                status=TurnStatus.TIMEOUT,
                should_process=True,
                needs_clarification=False,
                clarification_reason=None,
                detected_filler=False,
                detected_continuation=False,
                detected_done_marker=False,
            )

        # No transcript yet - determine based on silence
        if transcript is None:
            status = (
                TurnStatus.THINKING
                if silence_duration_ms >= self.silence_thinking_ms
                else TurnStatus.LISTENING
            )
            return TurnDetectionResult(
                status=status,
                should_process=False,
                needs_clarification=False,
                clarification_reason=None,
                detected_filler=False,
                detected_continuation=False,
                detected_done_marker=False,
            )

        # Analyze transcript content
        text_lower = transcript.text.lower().strip()

        # Detect linguistic cues (computed once, used throughout)
        detected_done_marker = any(marker in text_lower for marker in DONE_MARKERS)
        detected_filler = any(filler in text_lower for filler in FILLER_WORDS)
        detected_continuation = any(phrase in text_lower for phrase in CONTINUATION_PHRASES)

        # Check confidence
        if transcript.confidence < self.confidence_threshold:
            # Low confidence - but don't ask more than twice
            if clarification_count < 2:
                return TurnDetectionResult(
                    status=TurnStatus.NEEDS_CLARIFICATION,
                    should_process=False,
                    needs_clarification=True,
                    clarification_reason="Could you please repeat?",
                    detected_filler=detected_filler,
                    detected_continuation=detected_continuation,
                    detected_done_marker=detected_done_marker,
                )

        # Determine if utterance is complete based on silence and cues
        if silence_duration_ms >= self.silence_utterance_end_ms:
            # User has stopped speaking

            if detected_done_marker:
                # Explicit done signal
                return TurnDetectionResult(
                    status=TurnStatus.UTTERANCE_COMPLETE,
                    should_process=True,
                    needs_clarification=False,
                    clarification_reason=None,
                    detected_filler=detected_filler,
                    detected_continuation=detected_continuation,
                    detected_done_marker=True,
                )

            if detected_filler:
                # Filler word detected - extend patience
                if silence_duration_ms < self.silence_thinking_ms:
                    return TurnDetectionResult(
                        status=TurnStatus.THINKING,
                        should_process=False,
                        needs_clarification=False,
                        clarification_reason=None,
                        detected_filler=True,
                        detected_continuation=detected_continuation,
                        detected_done_marker=False,
                    )

            if detected_continuation:
                # Continuation phrase - wait for more
                if silence_duration_ms < self.silence_thinking_ms:
                    return TurnDetectionResult(
                        status=TurnStatus.THINKING,
                        should_process=False,
                        needs_clarification=False,
                        clarification_reason=None,
                        detected_filler=detected_filler,
                        detected_continuation=True,
                        detected_done_marker=False,
                    )

            # Normal case - utterance complete after silence threshold
            if silence_duration_ms >= self.silence_thinking_ms:
                # Extended silence - user is done
                return TurnDetectionResult(
                    status=TurnStatus.UTTERANCE_COMPLETE,
                    should_process=True,
                    needs_clarification=False,
                    clarification_reason=None,
                    detected_filler=detected_filler,
                    detected_continuation=detected_continuation,
                    detected_done_marker=detected_done_marker,
                )

            # Brief silence - utterance may be complete
            if transcript.is_final and transcript.confidence >= self.confidence_threshold:
                return TurnDetectionResult(
                    status=TurnStatus.UTTERANCE_COMPLETE,
                    should_process=True,
                    needs_clarification=False,
                    clarification_reason=None,
                    detected_filler=detected_filler,
                    detected_continuation=detected_continuation,
                    detected_done_marker=detected_done_marker,
                )

        # Default: still listening
        return TurnDetectionResult(
            status=TurnStatus.LISTENING,
            should_process=False,
            needs_clarification=False,
            clarification_reason=None,
            detected_filler=detected_filler,
            detected_continuation=detected_continuation,
            detected_done_marker=detected_done_marker,
        )
