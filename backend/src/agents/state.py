"""
Voice Orchestrator State Schema.

Defines the state structure for the LangGraph voice session orchestrator.
All state changes are tracked and checkpointed to SQLite for crash recovery.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal, TypedDict

from src.domain.entities.card import CardDict


class VoiceSessionState(str, Enum):
    """Voice session state machine states."""

    IDLE = "idle"
    PRESENTING = "presenting"  # Reading question
    LISTENING = "listening"  # Waiting for answer
    EVALUATING = "evaluating"  # LLM processing
    FEEDBACK = "feedback"  # Delivering feedback
    SOCRATIC = "socratic"  # Guided questioning
    ENDED = "ended"  # Session complete


class EvaluationDict(TypedDict, total=False):
    """Evaluation result in state."""

    reasoning: str
    corrected_transcript: str | None
    is_semantically_correct: bool
    fluency_score: int
    rating: int
    feedback: str
    enter_socratic_mode: bool
    socratic_prompt: str | None


class VoiceState(TypedDict, total=False):
    """LangGraph state schema for voice session.

    This state is checkpointed to SQLite after each transition
    for crash recovery and observability.

    Attributes:
        session_id: Unique session identifier
        deck_name: Name of deck being reviewed
        current_card: Current card being presented
        previous_card: Previous card (for undo)
        card_queue: Remaining cards to review
        messages: LLM conversation history
        last_transcript: Most recent user transcript
        last_evaluation: Most recent evaluation result
        socratic_turn_count: Turns in current socratic exchange
        socratic_context: Last 3 turns for context (sliding window)
        current_state: Current state machine state
        should_end: Whether session should end
        cards_reviewed: Count of cards reviewed
        start_time: Session start timestamp
        last_activity: Last activity timestamp
        rating_history: History of ratings for stats
        last_error: Description of last error (if any)
        retry_count: Number of retries in current node
        consecutive_timeouts: Count of consecutive timeout events
    """

    # Session info
    session_id: str
    deck_name: str

    # Card state
    current_card: CardDict | None
    previous_card: CardDict | None
    card_queue: list[CardDict]

    # Conversation
    messages: list[dict[str, Any]]
    last_transcript: str | None

    # Evaluation
    last_evaluation: EvaluationDict | None
    previous_evaluation: EvaluationDict | None  # For undo

    # Socratic mode
    socratic_turn_count: int
    socratic_context: list[str]  # Sliding window of last 3 turns

    # Flow control
    current_state: Literal[
        "idle", "presenting", "listening", "evaluating", "feedback", "socratic", "ended"
    ]
    should_end: bool

    # Metrics
    cards_reviewed: int
    start_time: float
    last_activity: float
    rating_history: list[int]

    # Hints
    hints_used: int
    previous_hints: list[str]  # Track hints given for LLM context

    # Question mode conversation history (for context in follow-up questions)
    question_history: list[dict[str, str]]  # [{"q": "...", "a": "..."}]

    # User answer attempts (for context in hints and follow-up questions)
    user_attempts: list[str]  # User's answer transcripts for this card (last 3)

    # Error tracking (for resilience)
    last_error: str | None
    retry_count: int
    consecutive_timeouts: int


@dataclass
class VoiceStateManager:
    """Helper for managing voice state transitions.

    Provides convenience methods for common state operations
    and ensures consistency.
    """

    state: VoiceState

    @classmethod
    def create_initial_state(
        cls,
        session_id: str,
        deck_name: str,
        cards: list[CardDict],
    ) -> "VoiceStateManager":
        """Create initial state for a new session."""
        now = datetime.now().timestamp()
        state: VoiceState = {
            "session_id": session_id,
            "deck_name": deck_name,
            "current_card": cards[0] if cards else None,
            "previous_card": None,
            "card_queue": cards[1:] if len(cards) > 1 else [],
            "messages": [],
            "last_transcript": None,
            "last_evaluation": None,
            "previous_evaluation": None,
            "socratic_turn_count": 0,
            "socratic_context": [],
            "current_state": "presenting" if cards else "ended",
            "should_end": not cards,
            "cards_reviewed": 0,
            "start_time": now,
            "last_activity": now,
            "rating_history": [],
            # Hints
            "hints_used": 0,
            "previous_hints": [],
            # Question mode
            "question_history": [],
            # User answer attempts
            "user_attempts": [],
            # Error tracking
            "last_error": None,
            "retry_count": 0,
            "consecutive_timeouts": 0,
        }
        return cls(state=state)

    def get_current_card(self) -> CardDict | None:
        """Get current card or None."""
        return self.state.get("current_card")

    def advance_to_next_card(self) -> CardDict | None:
        """Move to next card, returning it or None if done."""
        import logging

        logger = logging.getLogger(__name__)

        queue = self.state.get("card_queue", [])
        current = self.state.get("current_card")

        logger.info(
            f"advance_to_next_card: current={current.get('front', 'None')[:30] if current else 'None'}, queue_size={len(queue)}"
        )
        if queue:
            logger.info(
                f"advance_to_next_card: next_in_queue={queue[0].get('front', 'None')[:30] if queue else 'None'}"
            )

        # Save current as previous for undo
        self.state["previous_card"] = current
        self.state["previous_evaluation"] = self.state.get("last_evaluation")

        if queue:
            next_card = queue[0]
            self.state["current_card"] = next_card
            self.state["card_queue"] = queue[1:]
            self.state["cards_reviewed"] = self.state.get("cards_reviewed", 0) + 1
            # Reset state for new card
            self.state["hints_used"] = 0
            self.state["previous_hints"] = []
            self.state["question_history"] = []
            self.state["user_attempts"] = []
            self.state["last_evaluation"] = None
            self.state["socratic_turn_count"] = 0
            self.state["socratic_context"] = []
            logger.info(
                f"advance_to_next_card: returning next_card={next_card.get('front', 'None')[:30]}"
            )
            return next_card
        else:
            self.state["current_card"] = None
            self.state["should_end"] = True
            logger.info("advance_to_next_card: queue empty, returning None")
            return None

    def record_rating(self, rating: int) -> None:
        """Record a rating in history."""
        history = self.state.get("rating_history", [])
        history.append(rating)
        self.state["rating_history"] = history

    def enter_socratic_mode(self, prompt: str) -> None:
        """Enter socratic mode with initial prompt."""
        self.state["current_state"] = "socratic"
        self.state["socratic_turn_count"] = 0
        context = self.state.get("socratic_context", [])
        context.append(f"AI: {prompt}")
        # Keep only last 3
        self.state["socratic_context"] = context[-3:]

    def add_socratic_turn(self, user_response: str, ai_response: str) -> None:
        """Add a socratic exchange to context."""
        context = self.state.get("socratic_context", [])
        context.append(f"User: {user_response}")
        context.append(f"AI: {ai_response}")
        # Keep only last 3 exchanges (6 entries)
        self.state["socratic_context"] = context[-6:]
        self.state["socratic_turn_count"] = self.state.get("socratic_turn_count", 0) + 1

    def can_undo(self) -> bool:
        """Check if undo is possible."""
        return self.state.get("previous_card") is not None

    def undo_last_rating(self) -> CardDict | None:
        """Undo last rating, returning to previous card."""
        prev_card = self.state.get("previous_card")
        if prev_card is None:
            return None

        # Restore previous card
        current = self.state.get("current_card")
        queue = self.state.get("card_queue", [])

        # Put current card back at front of queue
        if current:
            queue = [current, *queue]

        self.state["current_card"] = prev_card
        self.state["card_queue"] = queue
        self.state["previous_card"] = None

        # Restore evaluation
        self.state["last_evaluation"] = self.state.get("previous_evaluation")
        self.state["previous_evaluation"] = None

        # Remove last rating from history
        history = self.state.get("rating_history", [])
        if history:
            history.pop()
            self.state["rating_history"] = history

        # Decrement cards reviewed
        reviewed = self.state.get("cards_reviewed", 0)
        if reviewed > 0:
            self.state["cards_reviewed"] = reviewed - 1

        return prev_card

    def increment_hints(self) -> int:
        """Increment hints used for current card, return new count."""
        hints = self.state.get("hints_used", 0) + 1
        self.state["hints_used"] = hints
        return hints

    def add_hint(self, hint: str) -> None:
        """Add a hint to the previous hints list for context."""
        previous_hints = self.state.get("previous_hints", [])
        previous_hints.append(hint)
        self.state["previous_hints"] = previous_hints

    def get_previous_hints(self) -> list[str]:
        """Get the list of previous hints for this card."""
        return self.state.get("previous_hints", [])

    def add_question_exchange(self, question: str, answer: str) -> None:
        """Add a question/answer exchange to history for context."""
        history = self.state.get("question_history", [])
        history.append({"q": question, "a": answer})
        # Keep last 5 exchanges to avoid prompt bloat
        self.state["question_history"] = history[-5:]

    def get_question_history(self) -> list[dict[str, str]]:
        """Get the question/answer history for this card."""
        return self.state.get("question_history", [])

    def add_user_attempt(self, transcript: str) -> None:
        """Track user's answer attempt for context."""
        attempts = self.state.get("user_attempts", [])
        if transcript and transcript not in attempts:
            attempts.append(transcript)
            # Keep last 3 attempts
            self.state["user_attempts"] = attempts[-3:]

    def get_user_attempts(self) -> list[str]:
        """Get user's answer attempts for this card."""
        return self.state.get("user_attempts", [])

    def get_stats(self) -> dict:
        """Get session statistics."""
        history = self.state.get("rating_history", [])
        queue_remaining = len(self.state.get("card_queue", []))
        # Include current card in remaining count if present
        current_card = self.state.get("current_card")
        cards_remaining = queue_remaining + (1 if current_card else 0)
        return {
            # Use len(rating_history) as source of truth - cards_reviewed state
            # variable can get out of sync when session ends on last card
            "cards_reviewed": len(history),
            "cards_remaining": cards_remaining,
            "rating_distribution": {
                "again": history.count(1),
                "hard": history.count(2),
                "good": history.count(3),
                "easy": history.count(4),
            },
            "session_duration_seconds": (
                datetime.now().timestamp() - self.state.get("start_time", 0)
            ),
        }
