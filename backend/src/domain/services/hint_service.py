"""
Hint Service.

Domain service for generating pedagogical hints.
Uses LLM to create guiding hints that help users recall answers
without directly revealing the content.
"""

import logging
import time

from src.domain.services.card_sanitizer import generate_fallback_hint
from src.ports.llm_service import (
    ExplainRequest,
    HintRequest,
    LLMPort,
    LLMServiceError,
)

logger = logging.getLogger(__name__)


class HintService:
    """Domain service for generating pedagogical hints.

    Responsibilities:
    - Generate guiding hints via LLM (contextual, structural)
    - Fall back to static hints on LLM failure
    - Handle the reveal case (level 2+)
    - Track hint metrics for observability
    """

    def __init__(self, llm_adapter: LLMPort) -> None:
        """Initialize hint service.

        Args:
            llm_adapter: LLM port implementation (e.g., GeminiAdapter)
        """
        self._llm = llm_adapter

    async def generate_hint(
        self,
        question: str,
        answer: str,
        hint_level: int,
        previous_hints: list[str] | None = None,
        user_attempts: list[str] | None = None,
        socratic_context: list[str] | None = None,
        evaluation_gap: str = "",
    ) -> str:
        """Generate a pedagogical hint for a flashcard.

        Args:
            question: The flashcard question
            answer: The expected answer
            hint_level: 0=contextual, 1=structural, 2+=reveal
            previous_hints: Previous hints given for this card
            user_attempts: User's answer transcripts for context
            socratic_context: Recent socratic exchanges
            evaluation_gap: Why user's answer was wrong

        Returns:
            Hint text for TTS/display
        """
        start_time = time.perf_counter()

        # Level 2+: Card is revealed - give short summary (not full answer)
        # The user can already SEE the answer, so speak a brief key insight instead
        if hint_level >= 2:
            try:
                # Use LLM to generate a concise summary
                request = HintRequest(
                    question=question,
                    expected_answer=answer,
                    hint_level=hint_level,
                    previous_hints=previous_hints or [],
                    user_attempts=user_attempts or [],
                    socratic_context=socratic_context or [],
                    evaluation_gap=evaluation_gap,
                )
                response = await self._llm.generate_hint(request)
                # Prepend reveal notification and append next card prompt
                hint = f"Here's the answer. {response.hint} Click Next Card when you're ready."
                self._log_hint(
                    hint_level, "reveal_summary", time.perf_counter() - start_time, False
                )
                return hint
            except LLMServiceError as e:
                # Fallback: just acknowledge the reveal, don't read the whole answer
                logger.warning(f"Reveal summary failed, using brief fallback: {e}")
                hint = "Here's the answer. Take a moment to review it. Click Next Card when you're ready."
                self._log_hint(
                    hint_level, "reveal_fallback", time.perf_counter() - start_time, True
                )
                return hint

        # Try LLM-generated hint
        try:
            request = HintRequest(
                question=question,
                expected_answer=answer,
                hint_level=hint_level,
                previous_hints=previous_hints or [],
                user_attempts=user_attempts or [],
                socratic_context=socratic_context or [],
                evaluation_gap=evaluation_gap,
            )
            logger.debug(
                "Generating hint",
                extra={
                    "hint_level": hint_level,
                    "previous_hints_count": len(previous_hints or []),
                    "previous_hints": previous_hints[:2] if previous_hints else [],
                    "user_attempts_count": len(user_attempts or []),
                    "has_evaluation_gap": bool(evaluation_gap),
                },
            )
            response = await self._llm.generate_hint(request)

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._log_hint(hint_level, response.hint_type, elapsed_ms / 1000, False)

            return response.hint

        except LLMServiceError as e:
            logger.warning(f"Hint generation failed, using fallback: {e}")
            hint = generate_fallback_hint(answer, hint_level)
            self._log_hint(hint_level, "fallback", time.perf_counter() - start_time, True)
            return hint

    def _log_hint(
        self,
        hint_level: int,
        hint_type: str,
        elapsed_seconds: float,
        used_fallback: bool,
    ) -> None:
        """Log hint generation metrics."""
        logger.info(
            "hint_generated",
            extra={
                "hint_level": hint_level,
                "hint_type": hint_type,
                "elapsed_ms": round(elapsed_seconds * 1000, 2),
                "used_fallback": used_fallback,
            },
        )

    async def explain_answer(self, question: str, answer: str) -> str:
        """Generate brief explanation of why an answer matters.

        Used when user gives up and needs to understand the answer.
        Returns a 1-2 sentence insight rather than reading the full answer.

        Args:
            question: The flashcard question
            answer: The expected answer

        Returns:
            Brief explanation for TTS (why this matters, not what it is)
        """
        start_time = time.perf_counter()

        try:
            request = ExplainRequest(question=question, answer=answer)
            response = await self._llm.explain_answer(request)

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                "explain_answer_generated",
                extra={
                    "elapsed_ms": round(elapsed_ms, 2),
                    "summary_length": len(response.summary),
                },
            )

            return response.summary

        except LLMServiceError as e:
            logger.warning(f"Explain answer failed, using fallback: {e}")
            # Fallback: generic message about reviewing the answer
            return "Take a moment to review the answer. Understanding the 'why' helps it stick."
