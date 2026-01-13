"""
Voice Commands Parser.

Parses user utterances for voice commands that control the session.
Commands can be context-specific or always available.

Commands from spec 002:
- Always: skip, repeat, hint, stop, undo, explain, "how am I doing?"
- Context-specific: read again, slower, I disagree, mark as [rating]
"""

import re
from dataclasses import dataclass
from enum import Enum

from src.domain.constants import COMMAND_CONFIDENCE_THRESHOLD

# Max length for command matching (ReDoS protection)
# Commands are short phrases; long text is definitely an answer
MAX_COMMAND_LENGTH = 100


class CommandType(str, Enum):
    """Types of voice commands."""

    # Always available
    SKIP = "skip"
    GIVE_UP = "give_up"  # Show answer before skipping ("I don't know")
    REPEAT = "repeat"
    HINT = "hint"
    STOP = "stop"
    UNDO = "undo"
    EXPLAIN = "explain"
    STATUS = "status"

    # Context: during question
    READ_AGAIN = "read_again"
    SLOWER = "slower"
    FASTER = "faster"
    WHAT_DECK = "what_deck"

    # Context: during evaluation
    DISAGREE = "disagree"
    REANSWER = "reanswer"
    WHY = "why"

    # Context: during feedback
    MARK_EASY = "mark_easy"
    MARK_GOOD = "mark_good"
    MARK_HARD = "mark_hard"
    MARK_AGAIN = "mark_again"
    NEXT = "next"  # Advance to next card after viewing result
    QUESTION = "question"  # Ask educational question about the card

    # Special
    UNKNOWN = "unknown"
    ANSWER = "answer"  # Not a command - treat as answer


class CommandContext(str, Enum):
    """Context in which commands are valid."""

    ANY = "any"
    QUESTION = "question"  # During question presentation
    LISTENING = "listening"  # While listening for answer
    EVALUATION = "evaluation"  # During evaluation
    FEEDBACK = "feedback"  # During feedback


@dataclass(frozen=True)
class ParsedCommand:
    """Result of command parsing (immutable value object)."""

    command_type: CommandType
    confidence: float  # 0.0-1.0
    raw_text: str
    parameters: tuple[tuple[str, str], ...]  # Immutable key-value pairs
    needs_confirmation: bool  # True if confidence < 0.8


# Command patterns: (regex pattern, command type, valid contexts)
COMMAND_PATTERNS: list[tuple[str, CommandType, list[CommandContext]]] = [
    # Always available
    (r"\b(skip|pass)\b", CommandType.SKIP, [CommandContext.ANY]),
    # Next card - only after rating (FEEDBACK context)
    (r"\b(next|continue|next card)\b", CommandType.NEXT, [CommandContext.FEEDBACK]),
    (
        r"\b(repeat|say that again|again please)\b",
        CommandType.REPEAT,
        [CommandContext.ANY],
    ),
    (r"\b(hint|give me a hint|help me)\b", CommandType.HINT, [CommandContext.ANY]),
    (r"\b(stop|end session|quit|exit)\b", CommandType.STOP, [CommandContext.ANY]),
    (r"\b(undo|go back|previous)\b", CommandType.UNDO, [CommandContext.ANY]),
    (
        r"\b(explain|tell me more|elaborate)\b",
        CommandType.EXPLAIN,
        [CommandContext.ANY],
    ),
    (r"\bhow am i doing\b", CommandType.STATUS, [CommandContext.ANY]),
    # "I don't know" variants - show answer before skipping (GIVE_UP)
    (
        r"\b(i don'?t know|no idea|can'?t remember|i forget)\b",
        CommandType.GIVE_UP,
        [CommandContext.ANY],
    ),
    (
        r"\b(show me|what is it|tell me the answer|give up)\b",
        CommandType.GIVE_UP,
        [CommandContext.ANY],
    ),
    # During question
    (r"\bread (it )?again\b", CommandType.READ_AGAIN, [CommandContext.QUESTION]),
    (
        r"\bslower\b",
        CommandType.SLOWER,
        [CommandContext.QUESTION, CommandContext.FEEDBACK],
    ),
    (
        r"\bfaster\b",
        CommandType.FASTER,
        [CommandContext.QUESTION, CommandContext.FEEDBACK],
    ),
    (r"\bwhat deck\b", CommandType.WHAT_DECK, [CommandContext.QUESTION]),
    # During evaluation
    (
        r"\bi disagree\b",
        CommandType.DISAGREE,
        [CommandContext.EVALUATION, CommandContext.FEEDBACK],
    ),
    (
        r"\bthat'?s not what i meant\b",
        CommandType.REANSWER,
        [CommandContext.EVALUATION],
    ),
    (r"\bwhy\b", CommandType.WHY, [CommandContext.EVALUATION, CommandContext.FEEDBACK]),
    (
        r"\bcan you explain why\b",
        CommandType.WHY,
        [CommandContext.EVALUATION, CommandContext.FEEDBACK],
    ),
    # Rating overrides during feedback
    (r"\bmark (as |it )?easy\b", CommandType.MARK_EASY, [CommandContext.FEEDBACK]),
    (r"\bmark (as |it )?good\b", CommandType.MARK_GOOD, [CommandContext.FEEDBACK]),
    (r"\bmark (as |it )?hard\b", CommandType.MARK_HARD, [CommandContext.FEEDBACK]),
    (r"\bmark (as |it )?again\b", CommandType.MARK_AGAIN, [CommandContext.FEEDBACK]),
    (r"\bi actually knew that\b", CommandType.MARK_GOOD, [CommandContext.FEEDBACK]),
    (
        r"\bthat was (harder|more difficult)\b",
        CommandType.MARK_HARD,
        [CommandContext.FEEDBACK],
    ),
    (r"\bthat was easy\b", CommandType.MARK_EASY, [CommandContext.FEEDBACK]),
]


class CommandParser:
    """
    Parses voice utterances for commands.

    Uses pattern matching and context to determine if an utterance
    is a command or an answer to a question.
    """

    def __init__(self, confidence_threshold: float = COMMAND_CONFIDENCE_THRESHOLD) -> None:
        self.confidence_threshold = confidence_threshold
        # Compile patterns for efficiency
        self._compiled_patterns = [
            (re.compile(pattern, re.IGNORECASE), cmd_type, contexts)
            for pattern, cmd_type, contexts in COMMAND_PATTERNS
        ]

    def parse(
        self,
        text: str,
        context: CommandContext = CommandContext.LISTENING,
        transcript_confidence: float = 1.0,
    ) -> ParsedCommand:
        """
        Parse text for commands.

        Args:
            text: The user's utterance
            context: Current session context (affects which commands are valid)
            transcript_confidence: STT confidence score

        Returns:
            ParsedCommand with detected command or ANSWER if no command found
        """
        text_lower = text.lower().strip()

        if not text_lower:
            return ParsedCommand(
                command_type=CommandType.UNKNOWN,
                confidence=0.0,
                raw_text=text,
                parameters=(),
                needs_confirmation=True,
            )

        # Early exit for long text (ReDoS protection)
        # Commands are short phrases; long text is definitely an answer
        if len(text_lower) > MAX_COMMAND_LENGTH:
            return ParsedCommand(
                command_type=CommandType.ANSWER,
                confidence=transcript_confidence,
                raw_text=text,
                parameters=(),
                needs_confirmation=False,
            )

        # Try each pattern
        for pattern, cmd_type, valid_contexts in self._compiled_patterns:
            if CommandContext.ANY in valid_contexts or context in valid_contexts:
                match = pattern.search(text_lower)
                if match:
                    # Calculate confidence based on:
                    # 1. How much of the text the command occupies
                    # 2. STT confidence
                    match_ratio = len(match.group()) / len(text_lower)
                    confidence = min(match_ratio, transcript_confidence)

                    return ParsedCommand(
                        command_type=cmd_type,
                        confidence=confidence,
                        raw_text=text,
                        parameters=(("match", match.group()),),
                        needs_confirmation=confidence < self.confidence_threshold,
                    )

        # No command found - treat as answer
        return ParsedCommand(
            command_type=CommandType.ANSWER,
            confidence=transcript_confidence,
            raw_text=text,
            parameters=(),
            needs_confirmation=False,
        )

    def get_rating_from_command(self, command: ParsedCommand) -> int | None:
        """Get Anki rating (1-4) from a rating override command."""
        rating_map = {
            CommandType.MARK_AGAIN: 1,
            CommandType.MARK_HARD: 2,
            CommandType.MARK_GOOD: 3,
            CommandType.MARK_EASY: 4,
        }
        return rating_map.get(command.command_type)
