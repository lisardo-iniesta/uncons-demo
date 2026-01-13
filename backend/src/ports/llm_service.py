"""Port interface for LLM services (answer evaluation)."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class EvaluationRequest(BaseModel):
    """Request for answer evaluation."""

    question: str
    expected_answer: str
    transcript: str
    response_time_seconds: float
    hints_used: int = 0
    socratic_context: list[str] | None = None


class EvaluationResponse(BaseModel):
    """Response from LLM evaluation.

    Field order optimized for streaming (reasoning first for CoT accuracy).
    """

    reasoning: str
    corrected_transcript: str | None = None
    is_semantically_correct: bool
    fluency_score: int  # 1-4
    rating: int  # 1-4
    feedback: str
    enter_socratic_mode: bool
    socratic_prompt: str | None = None
    answer_summary: str = ""  # 1-2 sentence summary of WHY the answer matters


class HintRequest(BaseModel):
    """Request for hint generation."""

    question: str
    expected_answer: str
    hint_level: int  # 0 = contextual, 1 = deeper, 2+ = reveal
    previous_hints: list[str] = []
    user_attempts: list[str] = []  # User's answer transcripts for context
    socratic_context: list[str] = []  # Recent socratic exchanges
    evaluation_gap: str = ""  # Why user's answer was wrong (from last evaluation)


class HintResponse(BaseModel):
    """Response from hint generation."""

    hint: str
    hint_type: str  # "contextual", "deeper", "reveal"


class ExplainRequest(BaseModel):
    """Request for answer explanation (when user gives up)."""

    question: str
    answer: str


class ExplainResponse(BaseModel):
    """Response with brief explanation of why an answer matters."""

    summary: str  # 1-2 sentence explanation of WHY the answer matters


@runtime_checkable
class LLMPort(Protocol):
    """LLM port interface for answer evaluation.

    Implementations should:
    - Use the provided system prompt for consistent evaluation
    - Return structured JSON matching EvaluationResponse schema
    - Handle API errors gracefully with retries
    """

    async def evaluate_answer(
        self,
        request: EvaluationRequest,
    ) -> EvaluationResponse:
        """Evaluate a user's spoken answer against the expected answer.

        Args:
            request: Evaluation request with question, answer, and transcript

        Returns:
            Structured evaluation response

        Raises:
            LLMServiceError: If evaluation fails after retries
        """
        ...

    async def generate_hint(
        self,
        request: HintRequest,
    ) -> HintResponse:
        """Generate a pedagogical hint for a flashcard.

        Args:
            request: Hint request with question, answer, and hint level

        Returns:
            Hint response with guiding hint text

        Raises:
            LLMServiceError: If hint generation fails
        """
        ...

    async def explain_answer(
        self,
        request: ExplainRequest,
    ) -> ExplainResponse:
        """Generate brief explanation of why an answer matters.

        Used when user gives up and needs to understand the answer.

        Args:
            request: Explain request with question and answer

        Returns:
            Brief summary explaining WHY the answer matters

        Raises:
            LLMServiceError: If explanation generation fails
        """
        ...


class LLMServiceError(Exception):
    """Base exception for LLM service errors."""

    pass


class LLMRateLimitError(LLMServiceError):
    """Raised when LLM API rate limit is exceeded."""

    pass


class LLMTimeoutError(LLMServiceError):
    """Raised when LLM request times out."""

    pass
