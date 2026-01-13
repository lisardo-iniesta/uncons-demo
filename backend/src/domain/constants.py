"""
Shared Domain Constants.

Central location for all threshold constants used across domain services.
All timing values are in milliseconds for consistency.
"""

# =============================================================================
# Silence Thresholds (milliseconds)
# =============================================================================
# Per Phase 3 interview decisions:
# - 300ms silence → utterance complete
# - 2.0s silence → thinking time (just wait)
# - 15.0s → prompt "Take your time"
# - 30.0s base timeout → treat as "don't know"
# - 60.0s extended timeout → after filler words or partial answer

SILENCE_UTTERANCE_END_MS = 300  # Mark utterance complete
SILENCE_THINKING_MS = 2000  # User may be thinking (wait)
SILENCE_TIMEOUT_MS = 30000  # Base timeout - treat as "don't know"
SILENCE_EXTENDED_TIMEOUT_MS = 60000  # Extended after engagement signals


# =============================================================================
# Confidence Thresholds
# =============================================================================
# Low confidence (<0.7) → ask "Could you please repeat?"

CONFIDENCE_CLARIFY_THRESHOLD = 0.7  # Below this, ask to repeat
COMMAND_CONFIDENCE_THRESHOLD = 0.8  # Below this, command needs confirmation


# =============================================================================
# Barge-in Thresholds (milliseconds)
# =============================================================================

MIN_BARGE_IN_DURATION_MS = 100  # Minimum speech to trigger barge-in
SHORT_INTERRUPTION_MS = 500  # Under this = short interruption


# =============================================================================
# Socratic Mode Settings
# =============================================================================

MAX_SOCRATIC_TURNS = 2  # Max guided questioning exchanges before giving answer
MAX_SOCRATIC_CONTEXT_ENTRIES = 6  # 3 exchanges * 2 (AI + User) for sliding window


# =============================================================================
# Linguistic Patterns
# =============================================================================

# Filler words that indicate thinking (wait for more)
FILLER_WORDS = frozenset(
    [
        "um",
        "uh",
        "hmm",
        "let me think",
        "wait",
        "hold on",
        "so",
        "well",
        "like",
        "you know",
        "i mean",
    ]
)

# Continuation phrases that indicate more is coming
CONTINUATION_PHRASES = frozenset(
    [
        "and also",
        "another thing",
        "plus",
        "additionally",
        "furthermore",
        "moreover",
        "not only that",
    ]
)

# Explicit done markers (user finished speaking)
DONE_MARKERS = frozenset(
    [
        "that's it",
        "that's all",
        "done",
        "finished",
        "i think that's everything",
        "that's my answer",
    ]
)

# Skip/don't know phrases
SKIP_PHRASES = frozenset(
    [
        "i don't know",
        "i dont know",
        "i'm not sure",
        "im not sure",
        "not sure",
        "no idea",
        "can't remember",
        "cant remember",
        "i forget",
        "pass",
        "skip",
        "show me",
        "what is it",
        "tell me the answer",
    ]
)

# Hesitation markers (for grading fluency)
HESITATION_MARKERS = frozenset(
    [
        "um",
        "uh",
        "hmm",
        "er",
        "ah",
        "like",
        "you know",
    ]
)

# Low-confidence markers (for grading certainty)
CONFIDENCE_MARKERS = frozenset(
    [
        "i think",
        "maybe",
        "probably",
        "i guess",
        "not sure",
        "perhaps",
    ]
)

# =============================================================================
# Feedback Messages (Single Source of Truth)
# =============================================================================


class FeedbackMessages:
    """Centralized feedback messages for TTS output.

    All feedback messages used across the application should be defined here
    to ensure consistency and ease of maintenance.
    """

    # Timeout and skip scenarios
    TIMEOUT = "No worries. Let me show you the answer."
    SKIP = "No problem! Here's the answer."
    LLM_ERROR = "I had trouble evaluating that. Let's mark it as hard and review again."

    # Rating-based feedback (correct answers)
    RATING_EASY = "Perfect! You've got this one down."
    RATING_GOOD = "Well done! Moving on."
    RATING_HARD = "Good effort! You'll see this again soon."
    RATING_AGAIN = "No worries. Let's review this one."

    # Wrong answer
    WRONG_ANSWER = "Not quite. Let me explain."

    # Partial answer
    PARTIAL_ANSWER = "You're on the right track! Let me fill in the rest."

    # Socratic mode fallback
    SOCRATIC_FALLBACK = "What else can you tell me about this?"

    @classmethod
    def for_rating(cls, rating: int) -> str:
        """Get feedback message for a rating (1-4)."""
        return {
            1: cls.RATING_AGAIN,
            2: cls.RATING_HARD,
            3: cls.RATING_GOOD,
            4: cls.RATING_EASY,
        }.get(rating, cls.RATING_HARD)
