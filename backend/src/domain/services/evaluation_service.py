"""
Evaluation Service.

Domain service that orchestrates answer evaluation.
Uses an LLM adapter for the actual evaluation but owns the business logic
for handling edge cases, logging, and result transformation.
"""

import logging
import time
from dataclasses import dataclass

from src.domain.constants import SKIP_PHRASES, FeedbackMessages
from src.domain.value_objects.evaluation_result import EvaluationResult
from src.ports.llm_service import (
    EvaluationRequest,
    EvaluationResponse,
    LLMPort,
    LLMServiceError,
)

logger = logging.getLogger(__name__)


@dataclass
class EvaluationInput:
    """Input for evaluation service."""

    question: str
    expected_answer: str
    transcript: str
    response_time_seconds: float
    hints_used: int = 0
    socratic_context: list[str] | None = None
    is_timeout: bool = False


class EvaluationService:
    """Domain service for evaluating user answers.

    Responsibilities:
    - Handle edge cases (timeout, explicit skip, empty transcript)
    - Log evaluation metrics for observability
    - Transform LLM responses to domain value objects
    - Apply business rules (hint caps rating, etc.)

    The actual LLM call is delegated to the LLMPort implementation.
    """

    def __init__(self, llm_adapter: LLMPort) -> None:
        """Initialize evaluation service.

        Args:
            llm_adapter: LLM port implementation (e.g., GeminiAdapter)
        """
        self._llm = llm_adapter

    async def evaluate(self, input: EvaluationInput) -> EvaluationResult:
        """Evaluate a user's spoken answer.

        Args:
            input: Evaluation input with question, answer, transcript, etc.

        Returns:
            EvaluationResult with rating and feedback
        """
        start_time = time.time()

        # Handle edge cases before calling LLM
        if input.is_timeout:
            logger.info(
                "Evaluation: timeout",
                extra={"question": input.question[:50]},
            )
            return EvaluationResult.timeout_result(
                answer_summary="Take a moment to review this answer. You'll see it again soon."
            )

        if self._is_explicit_skip(input.transcript):
            logger.info(
                "Evaluation: explicit skip",
                extra={"transcript": input.transcript[:50]},
            )
            return EvaluationResult.skip_result(
                answer_summary="Take a moment to review the answer above. Understanding the 'why' behind concepts helps retention."
            )

        if not input.transcript.strip():
            logger.info("Evaluation: empty transcript")
            return EvaluationResult.timeout_result(
                answer_summary="Take a moment to review this answer. You'll see it again soon."
            )

        # Call LLM for evaluation
        try:
            request = EvaluationRequest(
                question=input.question,
                expected_answer=input.expected_answer,
                transcript=input.transcript,
                response_time_seconds=input.response_time_seconds,
                hints_used=input.hints_used,
                socratic_context=input.socratic_context,
            )

            response = await self._llm.evaluate_answer(request)
            result = self._transform_response(response, input)

            # Log evaluation metrics
            evaluation_time_ms = (time.time() - start_time) * 1000
            self._log_evaluation(input, result, evaluation_time_ms)

            return result

        except LLMServiceError as e:
            logger.error(f"LLM evaluation failed: {e}")
            # Graceful degradation: return Hard rating with generic feedback
            return EvaluationResult(
                reasoning=f"LLM evaluation failed: {e}",
                corrected_transcript=None,
                is_semantically_correct=True,  # Give benefit of doubt
                fluency_score=2,
                rating=2,  # Hard - will see again soon
                feedback=FeedbackMessages.LLM_ERROR,
                enter_socratic_mode=False,
                answer_summary="",
                socratic_prompt=None,
            )

    def _is_explicit_skip(self, transcript: str) -> bool:
        """Check if transcript contains explicit skip/don't know phrases."""
        text_lower = transcript.lower().strip()
        return any(phrase in text_lower for phrase in SKIP_PHRASES)

    def _transform_response(
        self,
        response: EvaluationResponse,
        input: EvaluationInput,
    ) -> EvaluationResult:
        """Transform LLM response to domain value object.

        Applies business rules:
        - Hints used caps rating at 2 (Hard)
        - Validate socratic mode consistency
        - Correct answers should NOT enter socratic mode
        """
        rating = response.rating

        # Business rule: hints cap rating at Hard
        if input.hints_used > 0 and rating > 2:
            rating = 2

        # Ensure socratic mode consistency
        socratic_prompt = response.socratic_prompt
        enter_socratic = response.enter_socratic_mode

        # Business rule: if answer is semantically correct, don't enter socratic mode
        # Socratic mode is for partial/incomplete answers, not correct ones
        if response.is_semantically_correct:
            if enter_socratic:
                logger.info(
                    "Overriding socratic mode - answer is already correct",
                    extra={
                        "original_rating": response.rating,
                        "fluency": response.fluency_score,
                    },
                )
            enter_socratic = False
            socratic_prompt = None
            # Also ensure rating reflects the correct answer (at least 3 if fluent)
            if rating < 3 and response.fluency_score >= 3:
                rating = 3

        if enter_socratic and not socratic_prompt:
            # LLM forgot to provide prompt - use fallback
            socratic_prompt = FeedbackMessages.SOCRATIC_FALLBACK

        if not enter_socratic:
            socratic_prompt = None

        return EvaluationResult(
            reasoning=response.reasoning,
            corrected_transcript=response.corrected_transcript,
            is_semantically_correct=response.is_semantically_correct,
            fluency_score=response.fluency_score,  # type: ignore[arg-type]
            rating=rating,  # type: ignore[arg-type]
            feedback=response.feedback,
            enter_socratic_mode=enter_socratic,
            answer_summary=response.answer_summary,
            socratic_prompt=socratic_prompt,
        )

    def _log_evaluation(
        self,
        input: EvaluationInput,
        result: EvaluationResult,
        evaluation_time_ms: float,
    ) -> None:
        """Log structured evaluation data for observability."""
        logger.info(
            "evaluation_complete",
            extra={
                "event": "evaluation_complete",
                "original_transcript": input.transcript[:100],
                "corrected_transcript": result.corrected_transcript,
                "is_correct": result.is_semantically_correct,
                "fluency_score": result.fluency_score,
                "rating": result.rating,
                "response_time_ms": int(input.response_time_seconds * 1000),
                "evaluation_time_ms": int(evaluation_time_ms),
                "socratic_triggered": result.enter_socratic_mode,
                "hints_used": input.hints_used,
            },
        )
