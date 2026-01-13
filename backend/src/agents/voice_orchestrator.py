"""
Voice Session Orchestrator.

LangGraph-based state machine for voice review sessions.
Manages the flow: present card → listen → evaluate → feedback → next card.

Features:
- SQLite checkpointing for crash recovery
- Socratic mode for partial answers
- Voice command handling
- Timeout management
"""

import logging
import time
from datetime import datetime
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

from src.agents.state import CardDict, VoiceState, VoiceStateManager
from src.domain.constants import (
    FILLER_WORDS,
    MAX_SOCRATIC_CONTEXT_ENTRIES,
    MAX_SOCRATIC_TURNS,
    SILENCE_EXTENDED_TIMEOUT_MS,
    SILENCE_TIMEOUT_MS,
    FeedbackMessages,
)
from src.domain.services.command_parser import CommandType
from src.domain.services.evaluation_service import EvaluationInput, EvaluationService
from src.domain.value_objects.evaluation_result import EvaluationResult
from src.ports.llm_service import LLMServiceError

logger = logging.getLogger(__name__)

# Max consecutive timeouts before ending session gracefully
MAX_CONSECUTIVE_TIMEOUTS = 3


# =============================================================================
# Node Functions
# =============================================================================


async def present_card_node(state: VoiceState) -> dict[str, Any]:
    """Present the current card question.

    Returns state updates for presenting the card.
    The actual TTS is handled by the LiveKit agent.
    """
    current_card = state.get("current_card")

    if current_card is None:
        return {
            "current_state": "ended",
            "should_end": True,
        }

    logger.info(
        "Presenting card",
        extra={
            "card_id": current_card["id"],
            "deck": current_card["deck_name"],
        },
    )

    return {
        "current_state": "listening",
        "last_activity": datetime.now().timestamp(),
        "socratic_turn_count": 0,
        "socratic_context": [],
    }


async def evaluate_node(
    state: VoiceState,
    evaluation_service: EvaluationService,
) -> dict[str, Any]:
    """Evaluate the user's answer.

    Calls EvaluationService and returns result.
    Includes performance logging and resets consecutive timeout counter.
    """
    start_time = time.perf_counter()

    current_card = state.get("current_card")
    transcript = state.get("last_transcript", "")

    if current_card is None:
        return {"current_state": "ended", "should_end": True}

    # Calculate response time
    last_activity = state.get("last_activity", datetime.now().timestamp())
    response_time = datetime.now().timestamp() - last_activity

    # Build evaluation input
    evaluation_input = EvaluationInput(
        question=current_card["front"],
        expected_answer=current_card["back"],
        transcript=transcript,
        response_time_seconds=response_time,
        hints_used=0,  # Track this in state if needed
        socratic_context=state.get("socratic_context"),
        is_timeout=False,
    )

    result = await evaluation_service.evaluate(evaluation_input)

    # Convert to dict for state
    evaluation_dict = result.to_dict()

    # Calculate elapsed time for performance monitoring
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    logger.info(
        "evaluate_node_complete",
        extra={
            "node": "evaluate",
            "card_id": current_card["id"],
            "rating": result.rating,
            "is_correct": result.is_semantically_correct,
            "socratic": result.enter_socratic_mode,
            "elapsed_ms": round(elapsed_ms, 2),
            "within_budget": elapsed_ms < 500,  # LLM budget is <500ms
        },
    )

    return {
        "last_evaluation": evaluation_dict,
        "current_state": "socratic" if result.enter_socratic_mode else "feedback",
        "last_activity": datetime.now().timestamp(),
        # Reset consecutive timeouts on successful evaluation
        "consecutive_timeouts": 0,
        "last_error": None,
    }


async def socratic_node(state: VoiceState) -> dict[str, Any]:
    """Handle socratic mode - ask guiding question.

    Manages the socratic exchange count (max MAX_SOCRATIC_TURNS turns).
    """
    evaluation = state.get("last_evaluation")
    turn_count = state.get("socratic_turn_count", 0)

    # After max socratic turns, move to feedback with final rating
    if turn_count >= MAX_SOCRATIC_TURNS:
        return {
            "current_state": "feedback",
        }

    # Get socratic prompt from evaluation
    prompt = evaluation.get("socratic_prompt") if evaluation else None
    if not prompt:
        prompt = FeedbackMessages.SOCRATIC_FALLBACK

    # Add to context
    context = state.get("socratic_context", [])
    context.append(f"AI: {prompt}")

    return {
        "current_state": "listening",
        "socratic_context": context[-MAX_SOCRATIC_CONTEXT_ENTRIES:],
        "socratic_turn_count": turn_count + 1,
        "last_activity": datetime.now().timestamp(),
    }


async def feedback_node(state: VoiceState) -> dict[str, Any]:
    """Deliver feedback and record rating.

    Returns the feedback text for TTS and advances to next card.
    """
    evaluation = state.get("last_evaluation")
    current_card = state.get("current_card")

    if not evaluation or not current_card:
        return {"current_state": "ended", "should_end": True}

    rating = evaluation.get("rating", 2)

    # Record rating in history
    history = state.get("rating_history", [])
    history.append(rating)

    logger.info(
        "Feedback delivered",
        extra={
            "card_id": current_card["id"],
            "rating": rating,
            "feedback": evaluation.get("feedback", "")[:50],
        },
    )

    return {
        "rating_history": history,
        "previous_card": current_card,
        "previous_evaluation": evaluation,
        "last_activity": datetime.now().timestamp(),
    }


async def advance_card_node(state: VoiceState) -> dict[str, Any]:
    """Advance to the next card in the queue."""
    queue = state.get("card_queue", [])
    cards_reviewed = state.get("cards_reviewed", 0) + 1

    if queue:
        next_card = queue[0]
        return {
            "current_card": next_card,
            "card_queue": queue[1:],
            "cards_reviewed": cards_reviewed,
            "current_state": "presenting",
            "last_evaluation": None,
        }
    else:
        return {
            "current_card": None,
            "cards_reviewed": cards_reviewed,
            "current_state": "ended",
            "should_end": True,
        }


async def handle_command_node(
    state: VoiceState,
    command: CommandType,
) -> dict[str, Any]:
    """Handle a voice command."""
    if command == CommandType.SKIP:
        # Skip = Rating 1, advance to next
        # Use VoiceStateManager for consistent queue advancement
        manager = VoiceStateManager(state)
        manager.record_rating(1)
        next_card = manager.advance_to_next_card()

        if next_card:
            return {
                **manager.state,
                "current_state": "presenting",
            }
        else:
            return {
                **manager.state,
                "current_state": "ended",
                "should_end": True,
            }

    elif command == CommandType.REPEAT:
        # Repeat question - stay in presenting
        return {"current_state": "presenting"}

    elif command == CommandType.HINT:
        # Hint = enter socratic mode, cap rating at 2
        return {
            "current_state": "socratic",
            "socratic_turn_count": 1,  # Count hint as a turn
        }

    elif command == CommandType.UNDO:
        # Undo - restore previous card
        prev_card = state.get("previous_card")
        prev_eval = state.get("previous_evaluation")
        current_card = state.get("current_card")

        if prev_card:
            queue = state.get("card_queue", [])
            if current_card:
                queue = [current_card, *queue]

            history = state.get("rating_history", [])
            if history:
                history = history[:-1]

            return {
                "current_card": prev_card,
                "card_queue": queue,
                "previous_card": None,
                "last_evaluation": prev_eval,
                "previous_evaluation": None,
                "rating_history": history,
                "cards_reviewed": max(0, state.get("cards_reviewed", 0) - 1),
                "current_state": "presenting",
            }
        return {}

    elif command == CommandType.STOP:
        return {
            "current_state": "ended",
            "should_end": True,
        }

    return {}


async def timeout_node(state: VoiceState) -> dict[str, Any]:
    """Handle timeout - treat as 'don't know'.

    Tracks consecutive timeouts and ends session gracefully after
    MAX_CONSECUTIVE_TIMEOUTS to prevent infinite timeout loops.
    """
    current_card = state.get("current_card")
    consecutive = state.get("consecutive_timeouts", 0) + 1

    logger.info(
        "Timeout - treating as don't know",
        extra={
            "card_id": current_card["id"] if current_card else None,
            "consecutive_timeouts": consecutive,
        },
    )

    # End session after too many consecutive timeouts
    if consecutive >= MAX_CONSECUTIVE_TIMEOUTS:
        logger.warning(
            "Max consecutive timeouts reached, ending session",
            extra={"consecutive_timeouts": consecutive},
        )
        return {
            "current_state": "ended",
            "should_end": True,
            "consecutive_timeouts": consecutive,
            "last_error": f"Session ended after {consecutive} consecutive timeouts",
        }

    timeout_result = EvaluationResult.timeout_result()

    return {
        "last_evaluation": timeout_result.to_dict(),
        "current_state": "feedback",
        "consecutive_timeouts": consecutive,
    }


# =============================================================================
# Routing Functions
# =============================================================================


def route_from_feedback(state: VoiceState) -> Literal["next_card", "end"]:
    """Route after feedback - next card or end."""
    queue = state.get("card_queue", [])

    if queue:
        return "next_card"
    return "end"


def route_from_evaluate(state: VoiceState) -> Literal["socratic", "feedback"]:
    """Route after evaluation - socratic mode or direct feedback."""
    evaluation = state.get("last_evaluation", {})
    if evaluation.get("enter_socratic_mode"):
        return "socratic"
    return "feedback"


def should_extend_timeout(state: VoiceState) -> bool:
    """Check if timeout should be extended (engagement signals)."""
    transcript = state.get("last_transcript", "")
    socratic_count = state.get("socratic_turn_count", 0)

    # Extend for socratic mode
    if socratic_count > 0:
        return True

    # Extend for filler words indicating thinking
    transcript_lower = transcript.lower()

    return any(filler in transcript_lower for filler in FILLER_WORDS)


def get_timeout_ms(state: VoiceState) -> int:
    """Get appropriate timeout based on state."""
    if should_extend_timeout(state):
        return SILENCE_EXTENDED_TIMEOUT_MS
    return SILENCE_TIMEOUT_MS


# =============================================================================
# Graph Builder
# =============================================================================


class VoiceOrchestrator:
    """Voice session orchestrator using LangGraph.

    Manages the state machine for voice review sessions.
    Integrates with EvaluationService for answer grading.

    The graph is compiled once at init for efficiency and to enable
    checkpointing for crash recovery.
    """

    def __init__(
        self,
        evaluation_service: EvaluationService,
        checkpointer=None,
    ) -> None:
        """Initialize orchestrator.

        Args:
            evaluation_service: Service for answer evaluation
            checkpointer: Optional LangGraph checkpointer for persistence
        """
        self._evaluation_service = evaluation_service
        self._graph = self._build_graph()
        self._compiled_graph = self._graph.compile(checkpointer=checkpointer)
        self._state_manager: VoiceStateManager | None = None
        self._checkpointer = checkpointer

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state graph."""
        graph = StateGraph(VoiceState)

        # Create a closure for evaluate_node to capture evaluation_service
        async def evaluate_with_service(state: VoiceState) -> dict[str, Any]:
            return await evaluate_node(state, self._evaluation_service)

        # Add nodes with retry policy for evaluate (calls external LLM)
        graph.add_node("present_card", present_card_node)
        graph.add_node(
            "evaluate",
            evaluate_with_service,
            retry=RetryPolicy(max_attempts=2, retry_on=LLMServiceError),
        )
        graph.add_node("socratic", socratic_node)
        graph.add_node("feedback", feedback_node)
        graph.add_node("advance_card", advance_card_node)
        graph.add_node("timeout", timeout_node)

        # Entry point
        graph.set_entry_point("present_card")

        # Edges from present_card (goes to listening externally)
        # The agent handles the actual listening

        # Edges from feedback
        graph.add_conditional_edges(
            "feedback",
            route_from_feedback,
            {
                "next_card": "advance_card",
                "end": END,
            },
        )

        # Edges from advance_card
        graph.add_edge("advance_card", "present_card")

        # Edges from evaluate
        graph.add_conditional_edges(
            "evaluate",
            route_from_evaluate,
            {
                "socratic": "socratic",
                "feedback": "feedback",
            },
        )

        # Edges from socratic
        graph.add_edge("socratic", "present_card")  # Re-present with socratic prompt

        # Edges from timeout
        graph.add_edge("timeout", "feedback")

        return graph

    def create_session(
        self,
        session_id: str,
        deck_name: str,
        cards: list[CardDict],
    ) -> VoiceStateManager:
        """Create a new session with initial state.

        Args:
            session_id: Unique session ID
            deck_name: Name of deck
            cards: List of cards to review

        Returns:
            State manager for the session
        """
        self._state_manager = VoiceStateManager.create_initial_state(
            session_id=session_id,
            deck_name=deck_name,
            cards=cards,
        )
        return self._state_manager

    def get_state_manager(self) -> VoiceStateManager | None:
        """Get current state manager."""
        return self._state_manager

    async def process_transcript(
        self,
        transcript: str,
        is_timeout: bool = False,
    ) -> dict[str, Any]:
        """Process a user transcript.

        Args:
            transcript: User's spoken text
            is_timeout: Whether this is a timeout event

        Returns:
            Updated state dict
        """
        if self._state_manager is None:
            raise ValueError("No active session")

        state = self._state_manager.state

        # Update transcript
        state["last_transcript"] = transcript
        state["last_activity"] = datetime.now().timestamp()

        # Track user attempt for hint/question context
        self._state_manager.add_user_attempt(transcript)

        # Add user response to socratic context if in socratic mode
        # This ensures the LLM sees the full conversation history
        socratic_context = state.get("socratic_context", [])
        if socratic_context and transcript.strip():
            # Add user's response to context
            socratic_context.append(f"User: {transcript}")
            # Keep only last MAX_SOCRATIC_CONTEXT_ENTRIES entries
            state["socratic_context"] = socratic_context[-MAX_SOCRATIC_CONTEXT_ENTRIES:]
            logger.debug(
                "Added user response to socratic context",
                extra={
                    "transcript": transcript[:50],
                    "context_size": len(state["socratic_context"]),
                },
            )

        if is_timeout:
            state["current_state"] = "timeout"

        return state

    async def handle_command(self, command: CommandType) -> dict[str, Any]:
        """Handle a voice command.

        Args:
            command: Parsed command type

        Returns:
            Updated state dict
        """
        if self._state_manager is None:
            raise ValueError("No active session")

        logger.info(
            f"handle_command BEFORE: rating_history={self._state_manager.state.get('rating_history')}"
        )

        # Apply state changes back to the manager
        updated_state = await handle_command_node(self._state_manager.state, command)
        self._state_manager.state.update(updated_state)

        logger.info(
            f"handle_command AFTER: rating_history={self._state_manager.state.get('rating_history')}"
        )
        return updated_state

    def get_current_card(self) -> CardDict | None:
        """Get current card from state."""
        if self._state_manager is None:
            return None
        return self._state_manager.get_current_card()

    def get_previous_card(self) -> CardDict | None:
        """Get previously reviewed card (for recording skipped card ratings)."""
        if self._state_manager is None:
            return None
        return self._state_manager.state.get("previous_card")

    def get_stats(self) -> dict:
        """Get session statistics."""
        if self._state_manager is None:
            return {}
        stats = self._state_manager.get_stats()
        logger.info(
            f"get_stats: rating_history={self._state_manager.state.get('rating_history')}, stats={stats}"
        )
        return stats

    def record_rating_in_state(self, rating: int) -> None:
        """Record rating in state history for stats tracking."""
        if self._state_manager:
            self._state_manager.record_rating(rating)

    def advance_card(self) -> CardDict | None:
        """Advance to next card, return it or None if session ended."""
        if self._state_manager is None:
            return None
        return self._state_manager.advance_to_next_card()

    def increment_hints(self) -> int:
        """Increment hints used for current card, return new count."""
        if self._state_manager is None:
            return 0
        return self._state_manager.increment_hints()

    def get_hint_level(self) -> int:
        """Get current hint level (0-indexed for progressive hints)."""
        if self._state_manager is None:
            return 0
        return self._state_manager.state.get("hints_used", 1) - 1

    def can_undo(self) -> bool:
        """Check if undo is possible."""
        if self._state_manager is None:
            return False
        return self._state_manager.can_undo()

    def undo_card(self) -> CardDict | None:
        """Undo last rating, return previous card or None."""
        if self._state_manager is None:
            return None
        return self._state_manager.undo_last_rating()

    def has_active_session(self) -> bool:
        """Check if there's an active session."""
        return self._state_manager is not None

    def add_question_exchange(self, question: str, answer: str) -> None:
        """Add a question/answer exchange to history for context."""
        if self._state_manager:
            self._state_manager.add_question_exchange(question, answer)

    def get_question_history(self) -> list[dict[str, str]]:
        """Get the question/answer history for this card."""
        if self._state_manager is None:
            return []
        return self._state_manager.get_question_history()

    def get_previous_hints(self) -> list[str]:
        """Get the list of previous hints for this card."""
        if self._state_manager is None:
            return []
        return self._state_manager.get_previous_hints()

    def get_user_attempts(self) -> list[str]:
        """Get user's answer attempts for this card."""
        if self._state_manager is None:
            return []
        return self._state_manager.get_user_attempts()

    def get_last_evaluation_gap(self) -> str:
        """Get reasoning from last evaluation (what user got wrong)."""
        if self._state_manager is None:
            return ""
        eval_result = self._state_manager.state.get("last_evaluation", {})
        if eval_result and not eval_result.get("is_semantically_correct", True):
            return eval_result.get("reasoning", "")[:200]  # Truncate for prompt
        return ""

    async def evaluate(self, state: VoiceState) -> dict[str, Any]:
        """Evaluate user answer (public method for UnconsAgent).

        Args:
            state: Current voice state

        Returns:
            Evaluation result dict
        """
        # Check if max socratic turns reached BEFORE evaluation
        turn_count = state.get("socratic_turn_count", 0)
        if turn_count >= MAX_SOCRATIC_TURNS:
            logger.info(
                "Max socratic turns reached, forcing exit from socratic mode",
                extra={"turn_count": turn_count, "max_turns": MAX_SOCRATIC_TURNS},
            )
            # Force evaluation to not enter socratic mode anymore
            result = await evaluate_node(state, self._evaluation_service)
            evaluation = result.get("last_evaluation", {})
            if evaluation.get("enter_socratic_mode"):
                # Override - give final rating and feedback instead
                evaluation["enter_socratic_mode"] = False
                evaluation["socratic_prompt"] = None
                result["last_evaluation"] = evaluation
                result["current_state"] = "feedback"
            return result

        result = await evaluate_node(state, self._evaluation_service)

        # If entering socratic mode, add AI prompt to context and increment turn count
        evaluation = result.get("last_evaluation", {})
        if evaluation.get("enter_socratic_mode"):
            socratic_prompt = evaluation.get("socratic_prompt", "")
            if socratic_prompt and self._state_manager:
                # Add AI prompt to context
                socratic_context = self._state_manager.state.get("socratic_context", [])
                socratic_context.append(f"AI: {socratic_prompt}")
                self._state_manager.state["socratic_context"] = socratic_context[
                    -MAX_SOCRATIC_CONTEXT_ENTRIES:
                ]
                # Increment turn count
                self._state_manager.state["socratic_turn_count"] = turn_count + 1
                logger.debug(
                    "Entered socratic mode",
                    extra={
                        "turn_count": turn_count + 1,
                        "context_size": len(self._state_manager.state["socratic_context"]),
                        "prompt": socratic_prompt[:50],
                    },
                )

        return result
