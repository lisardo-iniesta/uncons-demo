"""Gemini LLM Adapter.

Implements LLMPort using Gemini Flash models via OpenAI-compatible API.
This approach provides:
- Consistent interface with other LLM providers
- JSON schema enforcement via response_format
- Lower latency than native Gemini SDK

Default model: gemini-3-flash-preview (configurable via GEMINI_MODEL env var)
"""

import json
import logging
import os
from typing import Any

from openai import APITimeoutError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.infrastructure.usage_tracker import log_usage
from src.ports.llm_service import (
    EvaluationRequest,
    EvaluationResponse,
    ExplainRequest,
    ExplainResponse,
    HintRequest,
    HintResponse,
    LLMPort,
    LLMRateLimitError,
    LLMServiceError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)


# JSON Schema for structured output
EVALUATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "Step-by-step evaluation reasoning. Generate FIRST for accuracy.",
        },
        "corrected_transcript": {
            "type": ["string", "null"],
            "description": "ASR-corrected transcript if phonetic errors detected.",
        },
        "is_semantically_correct": {
            "type": "boolean",
            "description": "Does the answer convey the correct concept?",
        },
        "fluency_score": {
            "type": "integer",
            "enum": [1, 2, 3, 4],
            "description": "Delivery quality: 1=poor, 2=hesitant, 3=good, 4=excellent",
        },
        "rating": {
            "type": "integer",
            "enum": [1, 2, 3, 4],
            "description": "Anki rating: 1=Again, 2=Hard, 3=Good, 4=Easy",
        },
        "feedback": {
            "type": "string",
            "description": "Brief encouraging feedback for TTS (1-2 sentences, under 25 words)",
            "maxLength": 150,  # Enforce brevity for voice output
        },
        "enter_socratic_mode": {
            "type": "boolean",
            "description": "True if partial knowledge detected and should guide with questions",
        },
        "socratic_prompt": {
            "type": ["string", "null"],
            "description": "Guiding question if socratic mode. Required if enter_socratic_mode is true.",
        },
        "answer_summary": {
            "type": "string",
            "description": "1-2 sentence summary of WHY the answer matters and how it connects to broader concepts. Do NOT repeat the card back verbatim.",
            "maxLength": 200,
        },
    },
    "required": [
        "reasoning",
        "is_semantically_correct",
        "fluency_score",
        "rating",
        "feedback",
        "enter_socratic_mode",
        "answer_summary",
    ],
    "additionalProperties": False,
}

# JSON Schema for hint generation
HINT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hint": {
            "type": "string",
            "description": "A question or prompt that triggers recall (1-2 sentences, under 30 words)",
            "maxLength": 200,
        },
        "hint_type": {
            "type": "string",
            "enum": ["contextual", "deeper", "reveal"],
            "description": "Type of hint: contextual (connections), deeper (key insight), or reveal",
        },
    },
    "required": ["hint", "hint_type"],
    "additionalProperties": False,
}

# JSON Schema for answer explanation (when user gives up)
EXPLAIN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "1-2 sentence explanation of WHY the answer matters (under 40 words)",
            "maxLength": 250,
        },
    },
    "required": ["summary"],
    "additionalProperties": False,
}


class GeminiAdapter(LLMPort):
    """Gemini LLM adapter using OpenAI-compatible API.

    Uses Gemini Flash models for low-latency evaluation with
    structured JSON output. Model configurable via GEMINI_MODEL env var.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/",
        timeout: float = 8.0,  # 8s max - Gemini 3 Flash needs more time
    ) -> None:
        """Initialize Gemini adapter.

        Args:
            api_key: Google API key. Defaults to GOOGLE_API_KEY env var.
            model: Model name. Defaults to GEMINI_MODEL env var or gemini-2.0-flash.
            base_url: OpenAI-compatible API endpoint.
            timeout: Request timeout in seconds.
        """
        self._api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
        if not self._api_key:
            raise ValueError("GOOGLE_API_KEY or GOOGLE_AI_API_KEY environment variable required")

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self._model = model or os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
        logger.info(f"GeminiAdapter initialized with model: {self._model}")

    @retry(
        retry=retry_if_exception_type((RateLimitError, APITimeoutError)),
        stop=stop_after_attempt(1),  # No retries on latency-critical path
        wait=wait_exponential(multiplier=1, min=1, max=2),
    )
    async def evaluate_answer(
        self,
        request: EvaluationRequest,
    ) -> EvaluationResponse:
        """Evaluate answer using Gemini 2.0 Flash.

        Args:
            request: Evaluation request with question, answer, transcript

        Returns:
            Structured evaluation response

        Raises:
            LLMServiceError: If evaluation fails
            LLMRateLimitError: If rate limited
            LLMTimeoutError: If request times out
        """
        try:
            user_prompt = self._build_user_prompt(request)

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": EVALUATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "evaluation_response",
                        "strict": True,
                        "schema": EVALUATION_RESPONSE_SCHEMA,
                    },
                },
                temperature=0.3,  # Low temp for consistent grading
            )

            content = response.choices[0].message.content
            if not content:
                raise LLMServiceError("Empty response from Gemini")

            # Log token usage for cost tracking
            if response.usage:
                log_usage(
                    model=self._model,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                )
                logger.debug(
                    "llm_usage",
                    extra={
                        "model": self._model,
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                )

            data = json.loads(content)
            return EvaluationResponse(**data)

        except RateLimitError as e:
            logger.warning(f"Gemini rate limited: {e}")
            raise LLMRateLimitError(str(e)) from e
        except APITimeoutError as e:
            logger.warning(f"Gemini timeout: {e}")
            raise LLMTimeoutError(str(e)) from e
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from Gemini: {e}")
            raise LLMServiceError(f"Invalid JSON response: {e}") from e
        except Exception as e:
            logger.error(f"Gemini evaluation failed: {e}")
            raise LLMServiceError(str(e)) from e

    def _build_user_prompt(self, request: EvaluationRequest) -> str:
        """Build user prompt for evaluation using XML tags."""
        prompt_parts = [
            "<flashcard>",
            f"Question: {request.question}",
            f"Expected: {request.expected_answer}",
            "</flashcard>",
            "",
            "<student_response>",
            f"Transcript: {request.transcript}",
            f"Response time: {request.response_time_seconds:.1f}s",
            f"Hints used: {request.hints_used}",
            "</student_response>",
        ]

        if request.socratic_context:
            prompt_parts.extend(
                [
                    "",
                    "<socratic_context>",
                    *[f"- {turn}" for turn in request.socratic_context[-3:]],
                    "</socratic_context>",
                ]
            )

        prompt_parts.extend(["", "<task>Evaluate the student's answer.</task>"])
        return "\n".join(prompt_parts)

    async def generate_hint(
        self,
        request: HintRequest,
    ) -> HintResponse:
        """Generate a pedagogical hint using Gemini.

        Args:
            request: Hint request with question, answer, and hint level

        Returns:
            Hint response with guiding hint text

        Raises:
            LLMServiceError: If hint generation fails
        """
        try:
            user_prompt = self._build_hint_prompt(request)

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": HINT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "hint_response",
                        "strict": True,
                        "schema": HINT_RESPONSE_SCHEMA,
                    },
                },
                temperature=0.7,  # Higher temp for more creative hints
            )

            content = response.choices[0].message.content
            if not content:
                raise LLMServiceError("Empty response from Gemini")

            # Log token usage for cost tracking
            if response.usage:
                log_usage(
                    model=self._model,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                )
                logger.debug(
                    "hint_generation_usage",
                    extra={
                        "model": self._model,
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "hint_level": request.hint_level,
                    },
                )

            data = json.loads(content)
            return HintResponse(**data)

        except RateLimitError as e:
            logger.warning(f"Gemini rate limited during hint generation: {e}")
            raise LLMRateLimitError(str(e)) from e
        except APITimeoutError as e:
            logger.warning(f"Gemini timeout during hint generation: {e}")
            raise LLMTimeoutError(str(e)) from e
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from Gemini hint: {e}")
            raise LLMServiceError(f"Invalid JSON response: {e}") from e
        except Exception as e:
            logger.error(f"Gemini hint generation failed: {e}")
            raise LLMServiceError(str(e)) from e

    def _build_hint_prompt(self, request: HintRequest) -> str:
        """Build user prompt for hint generation with full conversation context."""
        hint_type_map = {0: "contextual", 1: "deeper"}
        hint_type = hint_type_map.get(request.hint_level, "reveal")

        prompt_parts = [
            "<flashcard>",
            f"Question: {request.question}",
            f"Answer: {request.expected_answer}",
            "</flashcard>",
            "",
            f"<hint_level>{request.hint_level} ({hint_type})</hint_level>",
        ]

        # Add user's attempts (what they said)
        if request.user_attempts:
            prompt_parts.extend(
                [
                    "",
                    "<user_attempts>",
                    *[f'- "{attempt}"' for attempt in request.user_attempts[-3:]],
                    "</user_attempts>",
                ]
            )

        # Add evaluation gap (why they were wrong)
        if request.evaluation_gap:
            prompt_parts.extend(
                [
                    "",
                    f"<evaluation_gap>{request.evaluation_gap}</evaluation_gap>",
                ]
            )

        # Add socratic context
        if request.socratic_context:
            prompt_parts.extend(
                [
                    "",
                    "<socratic_exchanges>",
                    *[f"- {turn}" for turn in request.socratic_context[-4:]],
                    "</socratic_exchanges>",
                ]
            )

        if request.previous_hints:
            prompt_parts.extend(
                [
                    "",
                    "<previous_hints>",
                    *[f"- {hint}" for hint in request.previous_hints],
                    "</previous_hints>",
                ]
            )

        prompt_parts.extend(["", "<task>Generate a hint for the student.</task>"])
        return "\n".join(prompt_parts)

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
        try:
            user_prompt = f"""<flashcard>
Question: {request.question}
Answer: {request.answer}
</flashcard>

<task>Generate a brief explanation of why this answer matters.</task>"""

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "explain_response",
                        "strict": True,
                        "schema": EXPLAIN_RESPONSE_SCHEMA,
                    },
                },
                temperature=0.5,
            )

            content = response.choices[0].message.content
            if not content:
                raise LLMServiceError("Empty response from Gemini")

            # Log token usage for cost tracking
            if response.usage:
                log_usage(
                    model=self._model,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                )
                logger.debug(
                    "explain_answer_usage",
                    extra={
                        "model": self._model,
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                    },
                )

            data = json.loads(content)
            return ExplainResponse(**data)

        except RateLimitError as e:
            logger.warning(f"Gemini rate limited during explain: {e}")
            raise LLMRateLimitError(str(e)) from e
        except APITimeoutError as e:
            logger.warning(f"Gemini timeout during explain: {e}")
            raise LLMTimeoutError(str(e)) from e
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from Gemini explain: {e}")
            raise LLMServiceError(f"Invalid JSON response: {e}") from e
        except Exception as e:
            logger.error(f"Gemini explain failed: {e}")
            raise LLMServiceError(str(e)) from e


# System prompt for evaluation - uses XML tags for better Gemini parsing
EVALUATION_SYSTEM_PROMPT = """You are an Anki voice tutor evaluating spoken answers to flashcard questions.

<evaluation_philosophy>
Be encouraging but accurate. Separate WHAT the student knows from HOW they said it.
Assume transcription may contain ASR errors.
</evaluation_philosophy>

<asr_error_handling>
If a word seems contextually wrong, check for phonetic alternatives:
- Homophones: "two/to/too", "their/there/they're"
- Near-sounds: "ski/see/sea", "want/won't", "pears/Paris"
Choose the interpretation that makes the response most coherent.
If you correct a word, include it in corrected_transcript.
</asr_error_handling>

<grading_rubric>
Rating 4 (Easy): Semantically correct, response time < 2s, confident delivery (no fillers), no hints
Rating 3 (Good): Semantically correct, response time < 5s, reasonably fluent (minor hesitation OK), no hints
Rating 2 (Hard): Correct but hesitant (>3 fillers), took >5s, needed hints, or partial answer
Rating 1 (Again): Incorrect, "don't know", timeout, or unintelligible

Tie-breaker: If between two scores, choose the LOWER score.
</grading_rubric>

<synonym_handling>
If answer uses different terminology but same meaning:
- Accept as correct
- In feedback: "That's correct! The standard term is [X]."
- Rate normally (don't penalize for terminology)
</synonym_handling>

<socratic_mode>
Trigger when answer shows partial knowledge (some correct elements but incomplete):
- Set enter_socratic_mode: true
- Identify the SPECIFIC gap in their answer
- Craft a guiding question about that gap
- Do NOT reveal any part of the answer
- Do NOT reference visible card content
- Example: If they said "TCP is a protocol" but missed reliability:
  "You mentioned it's a protocol. What makes it different from UDP in terms of delivery guarantees?"
</socratic_mode>

<fluency_scores>
4: Immediate, confident, complete sentence, no fillers
3: Minor hesitation, complete thought, <2 fillers
2: Notable hesitation, >3 fillers, self-corrections, trailing off
1: Unable to form coherent response
</fluency_scores>

<output_instructions>
1. Generate reasoning FIRST (chain-of-thought)
2. Check for ASR errors and correct if needed
3. Evaluate semantic correctness
4. Assess fluency
5. Calculate final rating
6. Write brief, encouraging feedback (1-2 sentences max)
7. Decide if socratic mode is appropriate
8. Generate answer_summary (see below)

Keep feedback under 25 words - it will be spoken via TTS.
</output_instructions>

<answer_summary_instructions>
Generate a concise 1-2 sentence summary that:
- Explains WHY this answer matters (not WHAT it is)
- Connects to broader concepts or real-world relevance
- Does NOT repeat the card back verbatim
- Helps the learner see the bigger picture

Example: If card asks "What is TCP?" and answer is "Transmission Control Protocol",
summary might be: "TCP ensures reliable data delivery by establishing connections and confirming receipt - it's why web pages load completely instead of partially."

Generate for ALL answers (Rating 1-4) - even wrong answers benefit from understanding why the concept matters.
</answer_summary_instructions>"""

# System prompt for hint generation
HINT_SYSTEM_PROMPT = """You are a Socratic tutor helping students recall flashcard answers.

Your job is to generate HINTS that TRIGGER MEMORY RECALL through associations and connections - NOT by describing or revealing the answer.

<hint_philosophy>
Good hints help students REMEMBER by connecting to what they already know.
Bad hints REVEAL by describing the answer's content or structure.

NEVER say things like:
- "The answer has X parts..." (reveals structure)
- "It's a list of..." (reveals format)
- "The definition includes..." (reveals content)

INSTEAD, trigger recall through:
- Related concepts they should already know
- The key insight or "aha moment"
- Analogies or comparisons
- The problem this concept solves
- Common mistakes or misconceptions
</hint_philosophy>

<user_attempt_handling>
If user_attempts are provided:
- Acknowledge what they got RIGHT (if anything)
- Build on their existing knowledge
- Target the SPECIFIC gap identified in evaluation_gap
- Don't repeat information from previous_hints or socratic_exchanges
Example: "You mentioned sharing - that's the right direction. What SUBSET of the model do they share?"
</user_attempt_handling>

<hint_levels>
Level 0 (Contextual): Connect to real-world situations or related knowledge.
  Good examples:
  - "What happens when two teams need to share code but want independence?"
  - "Think about the trade-off between coupling and duplication."
  - "Remember what makes microservices communication hard?"

Level 1 (Deeper): Highlight the key insight, comparison, or mental model.
  Good examples:
  - "Compare this to the opposite approach - what would be the downside?"
  - "What's the core tension this pattern is trying to resolve?"
  - "Think about WHY you'd choose this over alternatives."

Level 2+ (Reveal Summary): The answer is now VISIBLE to the user. Give a brief key insight.
  Good examples:
  - "The key here is that [core principle]. This matters because [consequence]."
  - "Think of it as [analogy]. That's why [insight]."
  DO NOT read or summarize the answer - they can see it. Add ONE new insight.
</hint_levels>

<rules>
- NEVER read, quote, or summarize the answer text (user can already see it)
- For Level 2+: Give the "aha moment" - the insight that makes it stick
- Keep hints under 2 sentences (30 words max) for voice output
- If previous hints are provided, go DEEPER (don't repeat approaches)
- Be specific to THIS concept, not generic study advice
</rules>

<output>
Return JSON with:
- hint: For Level 0-1: A question that triggers recall. For Level 2+: A brief key insight.
- hint_type: "contextual", "deeper", or "reveal"
</output>"""

# System prompt for answer explanation (when user gives up)
EXPLAIN_SYSTEM_PROMPT = """You are an educational tutor helping a student understand a flashcard answer they couldn't recall.

<goal>
Generate a 1-2 sentence summary that explains WHY this answer matters and connects it to broader concepts.
This will be spoken via TTS, so keep it concise and conversational.
</goal>

<rules>
- Do NOT repeat the answer verbatim - the student can already see it
- Do NOT say "The answer is..." - they know what the answer is
- Focus on the INSIGHT: why does this matter? How does it connect to other concepts?
- Keep it under 40 words (will be spoken aloud)
- Be encouraging - help them see the bigger picture
</rules>

<examples>
Question: "What is TCP?"
Answer: "Transmission Control Protocol - ensures reliable, ordered data delivery"
Good summary: "TCP is the backbone of reliable internet communication. Understanding it helps you debug network issues and choose the right protocol for your application's needs."

Question: "What are HTTP methods?"
Answer: "GET, POST, PUT, PATCH, DELETE - each maps to CRUD operations"
Good summary: "These methods are the verbs of web APIs. Knowing idempotency - that PUT and DELETE can be safely retried - is crucial for building reliable systems."

Question: "What is dependency injection?"
Answer: "Design pattern where dependencies are provided externally rather than created internally"
Good summary: "This is the foundation of testable, modular code. When you can swap dependencies, you can test components in isolation and change implementations without rewriting code."
</examples>

<output>
Return JSON with:
- summary: Your 1-2 sentence explanation of WHY this matters (under 40 words)
</output>"""
