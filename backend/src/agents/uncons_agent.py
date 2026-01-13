"""
UNCONS Voice Agent.

LiveKit Agent implementation for voice-first Anki tutoring.
Integrates with VoiceOrchestrator for state management and
EvaluationService for answer grading.

Run: poetry run python src/agents/worker.py dev
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine

from livekit import rtc
from livekit.agents import Agent, RunContext, function_tool

from src.agents.voice_orchestrator import VoiceOrchestrator
from src.domain.constants import FeedbackMessages
from src.domain.entities.card import CardDict
from src.domain.services.card_sanitizer import (
    generate_fallback_hint,
    sanitize_question_for_tts,
)
from src.domain.services.command_parser import CommandParser, CommandType
from src.domain.services.hint_service import HintService
from src.domain.services.session_manager import SessionManager
from src.ports.llm_service import LLMServiceError

logger = logging.getLogger(__name__)


def _fire_and_forget(coro: Coroutine, name: str = "background task") -> None:
    """Create a fire-and-forget task with error logging.

    Args:
        coro: The coroutine to run in the background
        name: Name for logging purposes
    """

    async def wrapper():
        try:
            await coro
        except Exception as e:
            logger.error(f"Fire-and-forget {name} failed: {e}")

    asyncio.create_task(wrapper())


class UnconsAgent(Agent):
    """UNCONS voice tutor agent.

    Manages a voice review session with:
    - Card presentation via TTS
    - Answer evaluation via Gemini
    - Socratic mode for partial answers
    - Voice command handling
    """

    def __init__(
        self,
        orchestrator: VoiceOrchestrator,
        session_manager: SessionManager,
        room: rtc.Room | None = None,
        command_parser: CommandParser | None = None,
        hint_service: HintService | None = None,
        on_message_published: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize UNCONS agent.

        Args:
            orchestrator: Voice session orchestrator
            session_manager: Session manager for Anki sync
            room: LiveKit room for data channel communication
            command_parser: Optional command parser (default created)
            hint_service: Optional hint service for LLM-generated hints
            on_message_published: Callback when agent publishes a message (for deduplication)
        """
        super().__init__(
            instructions="""You are UNCONS, a voice tutor for Anki flashcard review sessions.

IMPORTANT: The review session and deck are ALREADY selected by the user before you connect.
Do NOT ask which deck to practice. Do NOT greet the user. The first card will be presented automatically.

Your job:
- Wait for the user to answer the current flashcard question
- Use evaluate_answer tool when user gives an answer
- The tool returns the feedback - speak that EXACTLY

CRITICAL RULE - TOOL RESPONSES ONLY:
When you call a tool like evaluate_answer:
- DO NOT generate your own feedback text
- DO NOT say anything like "Excellent!" or "Good job!" on your own
- ONLY speak the exact text returned by the tool
- The tool's return value IS your response - nothing more, nothing less

CRITICAL: After evaluating an answer:
- The tool returns feedback - that's what you say
- DO NOT call next_card automatically
- The user will click a "Next" button in the UI to advance
- Wait for explicit "next" command before calling next_card

When user speaks:
- If it sounds like an answer to the flashcard → use evaluate_answer
- If they say "next" or "continue" → use next_card
- If it's a command (skip, hint, repeat, stop) → handle it
- If they ask a FOLLOW-UP QUESTION (e.g., "Could you explain more?", "What does X mean?",
  "Can you give me an example?") → respond helpfully WITHOUT calling evaluate_answer.
  Questions are NOT answers - answer them conversationally using your knowledge.

Voice guidelines:
- When calling tools, ONLY speak the tool's return value
- Never add your own commentary or feedback
- Never ask "which deck would you like to practice"
- Never give a greeting or introduction
- Never auto-advance to next card""",
        )
        self._orchestrator = orchestrator
        self._session_manager = session_manager
        self._room = room
        self._command_parser = command_parser or CommandParser()
        self._hint_service = hint_service
        self._on_message_published = on_message_published
        self._session_id: str | None = None
        # Track input source to suppress echo in text mode
        self._last_input_from_text: bool = False

    def set_text_input_mode(self, from_text: bool) -> None:
        """Mark that the next input came from text mode (suppresses echo).

        Args:
            from_text: True if input is from text mode, False for voice
        """
        self._last_input_from_text = from_text

    def get_current_card(self) -> CardDict | None:
        """Get the current card from the orchestrator.

        Returns:
            Current card or None if no session/card
        """
        if not self._orchestrator:
            return None
        return self._orchestrator.get_current_card()

    def add_question_exchange(self, question: str, answer: str) -> None:
        """Add a question/answer exchange to history for context."""
        if self._orchestrator:
            self._orchestrator.add_question_exchange(question, answer)

    def get_question_history(self) -> list[dict[str, str]]:
        """Get the question/answer history for this card."""
        if not self._orchestrator:
            return []
        return self._orchestrator.get_question_history()

    def get_previous_hints(self) -> list[str]:
        """Get the list of previous hints for this card."""
        if not self._orchestrator:
            return []
        return self._orchestrator.get_previous_hints()

    def get_user_attempts(self) -> list[str]:
        """Get user's answer attempts for this card."""
        if not self._orchestrator:
            return []
        return self._orchestrator.get_user_attempts()

    def get_socratic_context(self) -> list[str]:
        """Get socratic exchanges for this card."""
        if not self._orchestrator:
            return []
        state_manager = self._orchestrator.get_state_manager()
        if not state_manager:
            return []
        return state_manager.state.get("socratic_context", [])

    async def evaluate_answer_direct(self, user_answer: str) -> str:
        """Evaluate user answer directly (bypasses LLM streaming).

        Use this method from worker.py for text input to avoid TTS/text mismatch.
        The @function_tool evaluate_answer delegates to this method.

        Args:
            user_answer: The user's answer text

        Returns:
            Feedback string to be spoken and displayed
        """
        return await self._evaluate_answer_impl(user_answer, ctx=None)

    @function_tool
    async def evaluate_answer(
        self,
        ctx: RunContext,
        user_answer: str,
    ) -> str:
        """Evaluate the user's spoken answer.

        Args:
            user_answer: The transcribed user answer

        Returns:
            Feedback and next action
        """
        return await self._evaluate_answer_impl(user_answer, ctx=ctx)

    async def _evaluate_answer_impl(self, user_answer: str, ctx: RunContext | None = None) -> str:
        """Internal implementation of answer evaluation.

        Args:
            user_answer: The user's answer text

        Returns:
            Feedback string
        """
        import re

        start_time = time.perf_counter()  # For end-to-end latency tracking

        # Reject empty or punctuation-only answers (LLM hallucination protection)
        # This prevents the LLM from evaluating "." or "," as valid answers
        # Note: Single characters like "2", "A" are valid (answers to "1+1?" or multiple choice)
        cleaned_answer = re.sub(r"[^\w\s]", "", user_answer).strip()
        if not cleaned_answer:
            logger.warning(f"Rejecting invalid answer (punctuation-only): '{user_answer}'")
            return "I didn't catch that. Could you please answer the question?"

        # Check if session is initialized
        if not self._orchestrator.has_active_session():
            logger.warning("evaluate_answer called but no active session")
            return "I'm still loading the flashcards. Please wait a moment and try again."

        # Send the user's transcript to frontend for voice mode display
        # Skip echo in text mode - user already sees what they typed
        if self._room and self._room.local_participant and not self._last_input_from_text:
            import json

            data = json.dumps(
                {
                    "type": "user_transcript",
                    "text": user_answer,
                    "source": "voice",
                }
            ).encode("utf-8")
            await self._room.local_participant.publish_data(
                data, reliable=True, topic="agent-response"
            )
            logger.debug(f"Sent final user transcript to frontend: {user_answer[:50]}...")
        elif self._last_input_from_text:
            logger.debug("Skipping transcript echo for text mode input")

        # Reset the flag after processing
        self._last_input_from_text = False

        # Check for commands first
        parsed = self._command_parser.parse(user_answer)

        if parsed.command_type != CommandType.ANSWER:
            return await self._handle_command(ctx, parsed.command_type)

        # Capture current card BEFORE any state changes (for rating recording)
        current_card = self._orchestrator.get_current_card()

        # Process as answer
        state = await self._orchestrator.process_transcript(user_answer)

        # Get evaluation from orchestrator (using public method) with fallback
        try:
            eval_result = await self._orchestrator.evaluate(state)
            evaluation = eval_result.get("last_evaluation", {})
        except LLMServiceError as e:
            logger.warning(f"Evaluation failed, using fallback: {e}")
            # Fallback: treat as "hard" (rating 2) and continue
            evaluation = {
                "rating": 2,
                "feedback": FeedbackMessages.LLM_ERROR,
                "enter_socratic_mode": False,
                "socratic_prompt": None,
            }

        # Extract evaluation fields
        rating = evaluation.get("rating", 2)
        feedback = evaluation.get("feedback", "Let's move on.")
        enter_socratic = evaluation.get("enter_socratic_mode", False)
        socratic_prompt = evaluation.get("socratic_prompt")
        answer_summary = evaluation.get("answer_summary", "")

        # Helper to log latency before returning
        def log_latency(result: str) -> str:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                "evaluate_answer_complete",
                extra={
                    "elapsed_ms": round(elapsed_ms, 2),
                    "within_budget": elapsed_ms < 1000,
                    "card_id": current_card["id"] if current_card else None,
                    "rating": rating,
                },
            )
            if elapsed_ms > 1200:
                logger.warning(
                    "Latency budget exceeded",
                    extra={"elapsed_ms": round(elapsed_ms, 2), "red_flag_threshold": 1200},
                )
            return result

        # If socratic mode, return the prompt
        if enter_socratic and socratic_prompt:
            # Publish socratic prompt to text panel
            await self.publish_agent_message(socratic_prompt)
            return log_latency(socratic_prompt)

        # Record rating to session manager (using captured card to avoid race condition)
        if self._session_id and current_card:
            from src.domain.value_objects.rating import Rating

            # Fire-and-forget: don't block feedback for database write
            _fire_and_forget(
                self._session_manager.record_rating(
                    session_id=self._session_id,
                    card_id=current_card["id"],
                    rating=Rating(rating),
                ),
                "record_rating",
            )

            # Record rating in state for stats tracking (but DON'T advance yet)
            if self._orchestrator.has_active_session():
                self._orchestrator.record_rating_in_state(rating)

                # Build enhanced feedback with answer summary
                # Summary adds WHY the answer matters (generated by LLM)
                enhanced_feedback = feedback
                if answer_summary:
                    enhanced_feedback = f"{feedback} {answer_summary}"

                # Send rating result to frontend SYNCHRONOUSLY - ensures text appears before TTS
                # User must click "Next" to advance
                await self.publish_rating_result(
                    rating, current_card, enhanced_feedback, answer_summary
                )

                # Also publish as agent_message for text panel display
                await self.publish_agent_message(enhanced_feedback)

        # Return enhanced feedback for TTS
        if answer_summary:
            return log_latency(f"{feedback} {answer_summary}")
        return log_latency(feedback)

    @function_tool
    async def next_card(self, ctx: RunContext, last_rating: int | None = None) -> str:
        """Move to the next flashcard.

        Args:
            ctx: Run context
            last_rating: Rating from previous card (1-4), if any

        Returns:
            Next question or completion message
        """
        if not self._orchestrator.has_active_session():
            return "No active session."

        next_card = self._orchestrator.advance_card()

        if next_card is None:
            # Record last rating in state for stats tracking (if provided)
            if last_rating is not None:
                self._orchestrator.record_rating_in_state(last_rating)

            # End session and notify frontend
            stats = self._orchestrator.get_stats()
            if self._session_id:
                # Fire-and-forget to not block response
                _fire_and_forget(
                    self._session_manager.end_session(self._session_id),
                    "end_session_next_card_complete",
                )

            # Publish session_complete to trigger frontend navigation
            await self.publish_session_complete(stats)

            return self._build_completion_message(stats)

        # Notify frontend of new card WITH rating
        await self.publish_card_update(next_card, last_rating=last_rating)

        next_card_text = sanitize_question_for_tts(next_card["front"])
        return f"Next question: {next_card_text}"

    @function_tool
    async def get_session_stats(self, ctx: RunContext) -> str:
        """Get current session statistics.

        Returns:
            Statistics summary
        """
        return await self._get_session_stats_impl()

    async def _get_session_stats_impl(self) -> str:
        """Internal implementation of get_session_stats."""
        stats = self._orchestrator.get_stats()
        if not stats:
            return "No active session."

        reviewed = stats.get("cards_reviewed", 0)
        remaining = stats.get("cards_remaining", 0)
        dist = stats.get("rating_distribution", {})

        return (
            f"You've reviewed {reviewed} cards with {remaining} remaining. "
            f"Ratings: {dist.get('easy', 0)} easy, {dist.get('good', 0)} good, "
            f"{dist.get('hard', 0)} hard, {dist.get('again', 0)} again."
        )

    @function_tool
    async def end_session(self, ctx: RunContext) -> str:
        """End the current review session.

        Returns:
            Goodbye message with final stats
        """
        return await self._end_session_impl()

    async def _end_session_impl(self) -> str:
        """Internal implementation of end_session."""
        if self._session_id:
            try:
                result = await self._session_manager.end_session(self._session_id)
                stats = result.stats
                return self._build_completion_message(stats)
            except Exception as e:
                logger.error(f"Error ending session: {e}")

        return "Session ended. See you next time!"

    async def _handle_command(self, ctx: RunContext | None, command: CommandType) -> str:
        """Handle a voice command."""
        if command == CommandType.SKIP:
            # Get the current card BEFORE handle_command advances it
            card_to_skip = self._orchestrator.get_current_card()

            # Now advance the card
            await self._orchestrator.handle_command(command)

            # Record skip as Rating 1 (Again) to database (fire-and-forget for latency)
            if self._session_id and card_to_skip:
                from src.domain.value_objects.rating import Rating

                _fire_and_forget(
                    self._session_manager.record_rating(
                        session_id=self._session_id,
                        card_id=card_to_skip["id"],
                        rating=Rating.AGAIN,
                    ),
                    "skip_record_rating",
                )

            # Get the new current card (handle_command already advanced)
            next_card = self._orchestrator.get_current_card()
            if next_card:
                # Notify frontend of new card with rating
                await self.publish_card_update(next_card, last_rating=1)
                next_card_text = sanitize_question_for_tts(next_card["front"])
                return f"Okay, next question: {next_card_text}"
            else:
                # Session complete
                stats = self._orchestrator.get_stats()
                if self._session_id:
                    _fire_and_forget(
                        self._session_manager.end_session(self._session_id),
                        "end_session_skip_complete",
                    )
                await self.publish_session_complete(stats)
                return self._build_completion_message(stats)

        elif command == CommandType.GIVE_UP:
            # User doesn't know the answer - show it and record as AGAIN
            card = self._orchestrator.get_current_card()
            if card:
                from src.domain.value_objects.rating import Rating

                # Record as AGAIN rating (worst)
                if self._session_id:
                    _fire_and_forget(
                        self._session_manager.record_rating(
                            session_id=self._session_id,
                            card_id=card["id"],
                            rating=Rating.AGAIN,
                        ),
                        "give_up_record_rating",
                    )

                # Record rating in state for stats tracking
                if self._orchestrator.has_active_session():
                    self._orchestrator.record_rating_in_state(1)  # AGAIN = 1

                # Generate brief explanation via LLM (not full answer verbatim)
                explanation = "Take a moment to review the answer."
                if self._hint_service:
                    try:
                        explanation = await self._hint_service.explain_answer(
                            question=card["front"],
                            answer=card["back"],
                        )
                    except Exception as e:
                        logger.warning(f"Failed to generate explanation: {e}")

                # Publish rating result to frontend (shows full answer for reading)
                await self.publish_rating_result(
                    rating=1,
                    card=card,
                    feedback=explanation,
                )

                # Return brief explanation for TTS (NOT full answer)
                return explanation
            return "No current question."

        elif command == CommandType.REPEAT:
            await self._orchestrator.handle_command(command)
            card = self._orchestrator.get_current_card()
            if card:
                card_text = sanitize_question_for_tts(card["front"])
                return f"The question is: {card_text}"
            return "No current question."

        elif command == CommandType.HINT:
            await self._orchestrator.handle_command(command)
            card = self._orchestrator.get_current_card()
            if card:
                # Increment hint count and get hint level
                self._orchestrator.increment_hints()
                hint_level = self._orchestrator.get_hint_level()

                # Get full conversation context for personalized hints
                state_manager = self._orchestrator.get_state_manager()
                previous_hints = state_manager.get_previous_hints() if state_manager else []
                user_attempts = self._orchestrator.get_user_attempts()
                socratic_context = (
                    state_manager.state.get("socratic_context", []) if state_manager else []
                )
                evaluation_gap = self._orchestrator.get_last_evaluation_gap()

                # Generate intelligent hint via LLM (or fallback)
                if self._hint_service:
                    hint = await self._hint_service.generate_hint(
                        question=card["front"],
                        answer=card["back"],
                        hint_level=hint_level,
                        previous_hints=previous_hints,
                        user_attempts=user_attempts,
                        socratic_context=socratic_context,
                        evaluation_gap=evaluation_gap,
                    )
                else:
                    # Fallback to static hints if no hint service
                    hint = generate_fallback_hint(card["back"], hint_level)

                # Track hint for future context
                if state_manager:
                    state_manager.add_hint(hint)

                # If reveal hint (level 2+), flip the card to show back
                if hint_level >= 2:
                    await self.publish_reveal_answer(card)

                return hint
            return "No current question."

        elif command == CommandType.UNDO:
            # Check if undo is possible BEFORE calling handle_command
            if self._orchestrator.can_undo():
                await self._orchestrator.handle_command(command)
                card = self._orchestrator.get_current_card()
                if card:
                    # Publish card update to frontend
                    await self.publish_card_update(card, last_rating=None)
                    card_text = sanitize_question_for_tts(card["front"])
                    return f"Let's go back. {card_text}"
            return "Nothing to undo."

        elif command == CommandType.STOP:
            return await self._end_session_impl()

        elif command == CommandType.STATUS:
            return await self._get_session_stats_impl()

        elif command == CommandType.NEXT:
            # Advance to next card after user has seen the result
            next_card = self._orchestrator.advance_card()
            if next_card:
                # Notify frontend of new card
                await self.publish_card_update(next_card, last_rating=None)
                next_card_text = sanitize_question_for_tts(next_card["front"])
                return f"Next question: {next_card_text}"
            else:
                # Session complete
                stats = self._orchestrator.get_stats()
                if self._session_id:
                    _fire_and_forget(
                        self._session_manager.end_session(self._session_id),
                        "end_session_next_complete",
                    )
                await self.publish_session_complete(stats)
                return self._build_completion_message(stats)

        return "I didn't catch that command."

    def _build_completion_message(self, stats: dict) -> str:
        """Build session completion message."""
        reviewed = stats.get("cards_reviewed", 0)
        dist = stats.get("rating_distribution", {})

        easy = dist.get("easy", 0)
        good = dist.get("good", 0)
        hard = dist.get("hard", 0)
        again = dist.get("again", 0)

        message = f"Session complete! You reviewed {reviewed} cards. "

        if easy + good > hard + again:
            message += "Great work! You're doing well with this material."
        elif hard + again > 0:
            message += "Keep practicing! You'll master these soon."
        else:
            message += "Well done!"

        return message

    async def publish_session_complete(self, stats: dict) -> None:
        """Publish session complete message to frontend via data channel.

        Args:
            stats: Session statistics to send
        """
        if not self._room:
            logger.warning("No room available to publish session complete")
            return

        cards_reviewed = stats.get("cards_reviewed", 0)
        duration_seconds = stats.get("session_duration_seconds", 0)

        try:
            await self._room.local_participant.publish_data(
                json.dumps(
                    {
                        "type": "session_complete",
                        "stats": {
                            "cards_reviewed": cards_reviewed,
                            "ratings": stats.get("rating_distribution", {}),
                            "duration_minutes": duration_seconds / 60,
                            "synced_count": cards_reviewed,  # Assume all synced
                            "failed_count": 0,
                        },
                    }
                ).encode("utf-8"),
                reliable=True,
                topic="agent-response",
            )
            logger.info(f"Published session complete: {cards_reviewed} cards reviewed")
        except Exception as e:
            logger.error(f"Failed to publish session complete: {e}")

    async def publish_card_update(self, card: CardDict, last_rating: int | None = None) -> None:
        """Publish card update to frontend via data channel.

        Sends a 'card' type message that the frontend uses to update
        the displayed flashcard in the UI, including progress info.

        Args:
            card: The card data to send to the frontend
            last_rating: Rating from the previous card (1-4), if any
        """
        if not self._room:
            logger.warning("No room available to publish card update")
            return

        # Get progress stats via orchestrator
        stats = self._orchestrator.get_stats()

        try:
            await self._room.local_participant.publish_data(
                json.dumps(
                    {
                        "type": "card",
                        "card": {
                            "id": card["id"],
                            "question_html": card["front"],
                            "answer_html": card["back"],
                            "deck_name": card.get("deck_name"),
                            "image_url": card.get("image_filename"),
                        },
                        "progress": {
                            "cards_reviewed": stats.get("cards_reviewed", 0),
                            "cards_remaining": stats.get("cards_remaining", 0),
                        },
                        "last_rating": last_rating,
                    }
                ).encode("utf-8"),
                reliable=True,
                topic="agent-response",
            )
            logger.info(f"Published card update: {card['front'][:30]}...")
        except Exception as e:
            logger.error(f"Failed to publish card update: {e}")

    async def publish_rating_result(
        self,
        rating: int,
        card: CardDict,
        feedback: str,
        answer_summary: str = "",
    ) -> None:
        """Publish rating result to frontend without advancing to next card.

        Args:
            rating: The rating (1-4)
            card: The current card (for showing back content)
            feedback: The feedback message
            answer_summary: 1-2 sentence summary of WHY the answer matters
        """
        if not self._room:
            logger.warning("No room available to publish rating result")
            return

        # Get progress stats via orchestrator
        stats = self._orchestrator.get_stats()

        try:
            await self._room.local_participant.publish_data(
                json.dumps(
                    {
                        "type": "rating_result",
                        "rating": rating,
                        "feedback": feedback,
                        "card_back": card["back"],
                        "answer_summary": answer_summary,
                        "progress": {
                            "cards_reviewed": stats.get("cards_reviewed", 0),
                            "cards_remaining": stats.get("cards_remaining", 0),
                        },
                    }
                ).encode("utf-8"),
                reliable=True,
                topic="agent-response",
            )
            logger.info(f"Published rating result: rating={rating}")
        except Exception as e:
            logger.error(f"Failed to publish rating result: {e}")

    async def publish_reveal_answer(self, card: CardDict) -> None:
        """Publish reveal answer to frontend to flip the card.

        Called when hint level 2+ is reached (full reveal).
        Triggers showingResult: true in frontend without a rating.

        Args:
            card: The current card (for showing back content)
        """
        if not self._room:
            logger.warning("No room available to publish reveal answer")
            return

        # Get progress stats via orchestrator
        stats = self._orchestrator.get_stats()

        try:
            await self._room.local_participant.publish_data(
                json.dumps(
                    {
                        "type": "reveal_answer",
                        "card_back": card["back"],
                        "progress": {
                            "cards_reviewed": stats.get("cards_reviewed", 0),
                            "cards_remaining": stats.get("cards_remaining", 0),
                        },
                    }
                ).encode("utf-8"),
                reliable=True,
                topic="agent-response",
            )
            logger.info("Published reveal answer (hint level 2+)")
        except Exception as e:
            logger.error(f"Failed to publish reveal answer: {e}")

    async def publish_agent_message(self, text: str) -> None:
        """Publish agent message to frontend text panel.

        Args:
            text: The message text to display
        """
        if not self._room or not text:
            return

        import ulid

        try:
            message_id = str(ulid.ULID())
            await self._room.local_participant.publish_data(
                json.dumps(
                    {
                        "type": "agent_message",
                        "text": text,
                        "id": message_id,
                    }
                ).encode("utf-8"),
                reliable=True,
                topic="agent-response",
            )
            logger.info(f"Published agent_message [{message_id[:8]}...]: {text[:30]}...")

            # Signal that we published this text (for deduplication in conversation_item_added)
            if self._on_message_published:
                self._on_message_published(text)

        except Exception as e:
            logger.error(f"Failed to publish agent_message: {e}")
