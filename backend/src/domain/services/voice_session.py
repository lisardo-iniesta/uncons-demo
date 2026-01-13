"""
Voice Session State Machine using LangGraph.

States per spec 002 (with user modifications):
- IDLE → PRESENTING_CARD → LISTENING → EVALUATING → FEEDBACK
- Special: SPEAKING, INTERRUPTED, CLARIFYING

Modifications from spec:
- No encouragement prompts (4.0s threshold removed)
- 20s timeout (increased from 10s)
- Low confidence (<0.7) → ask "Could you please repeat?"
"""

import operator
from enum import Enum
from typing import Annotated

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.domain.constants import SILENCE_TIMEOUT_MS
from src.domain.value_objects.transcript import Transcript


class SessionStatus(str, Enum):
    """Voice session states."""

    IDLE = "idle"
    PRESENTING_CARD = "presenting_card"
    LISTENING = "listening"
    EVALUATING = "evaluating"
    FEEDBACK = "feedback"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CLARIFYING = "clarifying"  # Asking user to repeat


class VoiceSessionState(TypedDict):
    """State for the voice session graph."""

    # Current session status
    status: SessionStatus

    # Current card being reviewed
    card_id: str | None
    card_question: str | None
    card_answer: str | None

    # User's response
    transcript: Transcript | None
    silence_duration_ms: int

    # Evaluation results
    rating: int | None  # 1-4 Anki rating
    feedback_text: str | None

    # Session metadata
    session_id: str
    hints_used: int
    clarification_count: int

    # Messages for the user (accumulated)
    messages: Annotated[list[str], operator.add]

    # Internal routing
    next_action: str | None


def idle_node(state: VoiceSessionState) -> dict:
    """Initial state - waiting for session to start."""
    return {
        "status": SessionStatus.IDLE,
        "next_action": None,
    }


def present_card_node(state: VoiceSessionState) -> dict:
    """Present the current card's question to the user."""
    if not state.get("card_question"):
        return {
            "status": SessionStatus.IDLE,
            "messages": ["No card loaded. Session ending."],
            "next_action": "end",
        }

    return {
        "status": SessionStatus.PRESENTING_CARD,
        "messages": [state["card_question"]],
        "next_action": "speak",
    }


def listening_node(state: VoiceSessionState) -> dict:
    """Actively listening for user's answer."""
    return {
        "status": SessionStatus.LISTENING,
        "silence_duration_ms": 0,
        "transcript": None,
        "next_action": None,
    }


def check_transcript_node(state: VoiceSessionState) -> dict:
    """Check transcript quality and decide next step."""
    transcript = state.get("transcript")

    if not transcript:
        # No transcript yet - check for timeout
        silence_ms = state.get("silence_duration_ms", 0)
        if silence_ms >= SILENCE_TIMEOUT_MS:
            return {
                "rating": 1,  # Again
                "messages": ["No worries. Let me show you the answer."],
                "next_action": "timeout",
            }
        return {"next_action": "continue_listening"}

    # Check confidence
    if transcript.needs_clarification:
        clarify_count = state.get("clarification_count", 0)
        if clarify_count >= 2:
            # Too many clarifications - proceed anyway
            return {"next_action": "evaluate"}

        return {
            "status": SessionStatus.CLARIFYING,
            "clarification_count": clarify_count + 1,
            "messages": ["Could you please repeat?"],
            "next_action": "clarify",
        }

    return {"next_action": "evaluate"}


def evaluate_node(state: VoiceSessionState) -> dict:
    """Evaluate the user's answer."""
    return {
        "status": SessionStatus.EVALUATING,
        "next_action": "evaluated",
    }


def feedback_node(state: VoiceSessionState) -> dict:
    """Deliver feedback to the user."""
    rating = state.get("rating", 3)
    feedback = state.get("feedback_text", "")

    rating_messages = {
        1: "Let's review this one again soon.",
        2: "Good effort! You'll see this again shortly.",
        3: "Well done! Moving on.",
        4: "Perfect! You've mastered this one.",
    }

    message = feedback or rating_messages.get(rating, "")

    return {
        "status": SessionStatus.FEEDBACK,
        "messages": [message] if message else [],
        "next_action": "speak",
    }


def speaking_node(state: VoiceSessionState) -> dict:
    """TTS is playing - can be interrupted."""
    return {
        "status": SessionStatus.SPEAKING,
        "next_action": None,
    }


def interrupted_node(state: VoiceSessionState) -> dict:
    """User interrupted - stop TTS and listen."""
    return {
        "status": SessionStatus.INTERRUPTED,
        "next_action": "listen",
    }


def clarifying_node(state: VoiceSessionState) -> dict:
    """Asking user to repeat due to low confidence."""
    return {
        "status": SessionStatus.CLARIFYING,
        "next_action": "speak",
    }


# Routing functions
def route_after_present(state: VoiceSessionState) -> str:
    """Route after presenting card."""
    return "speaking"


def route_after_check(state: VoiceSessionState) -> str:
    """Route based on transcript check results."""
    action = state.get("next_action")
    if action == "timeout":
        return "feedback"
    elif action == "clarify":
        return "clarifying"
    elif action == "evaluate":
        return "evaluate"
    else:
        return "listening"  # continue listening


def route_after_speaking(state: VoiceSessionState) -> str:
    """Route after TTS finishes."""
    status = state.get("status")
    if status == SessionStatus.PRESENTING_CARD or status == SessionStatus.CLARIFYING:
        return "listening"
    elif status == SessionStatus.FEEDBACK:
        return END
    return "listening"


def route_after_feedback(state: VoiceSessionState) -> str:
    """Route after feedback - next card or end."""
    return END  # For now, end after feedback


def create_voice_session_graph() -> StateGraph:
    """
    Create the voice session state machine.

    Returns a compiled LangGraph that handles the voice interaction flow.
    """
    builder = StateGraph(VoiceSessionState)

    # Add nodes
    builder.add_node("idle", idle_node)
    builder.add_node("present_card", present_card_node)
    builder.add_node("listening", listening_node)
    builder.add_node("check_transcript", check_transcript_node)
    builder.add_node("evaluate", evaluate_node)
    builder.add_node("feedback", feedback_node)
    builder.add_node("speaking", speaking_node)
    builder.add_node("interrupted", interrupted_node)
    builder.add_node("clarifying", clarifying_node)

    # Define edges
    builder.add_edge(START, "idle")
    builder.add_edge("idle", "present_card")
    builder.add_edge("present_card", "speaking")
    builder.add_edge("speaking", "listening")
    builder.add_edge("listening", "check_transcript")

    # Conditional edges
    builder.add_conditional_edges(
        "check_transcript",
        route_after_check,
        {
            "listening": "listening",
            "clarifying": "clarifying",
            "evaluate": "evaluate",
            "feedback": "feedback",
        },
    )

    builder.add_edge("clarifying", "speaking")
    builder.add_edge("evaluate", "feedback")
    builder.add_edge("feedback", END)
    builder.add_edge("interrupted", "listening")

    return builder.compile()


# Export the compiled graph
voice_session_graph = create_voice_session_graph()
