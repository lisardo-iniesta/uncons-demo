"""
Barge-In Handler.

Manages interruption logic when user speaks while TTS is playing.
Per spec 002: Immediate stop (<100ms) when user speaks.
"""

from dataclasses import dataclass
from enum import Enum

from src.domain.constants import MIN_BARGE_IN_DURATION_MS, SHORT_INTERRUPTION_MS

from .command_parser import CommandContext, CommandParser, CommandType, ParsedCommand


class BargeInAction(str, Enum):
    """Actions to take after barge-in."""

    LISTEN = "listen"  # User wants to speak, switch to listening
    EXECUTE_COMMAND = "execute_command"  # User issued a command
    ACKNOWLEDGE_WAIT = "acknowledge_wait"  # Short interruption, acknowledge and wait
    RESUME = "resume"  # False positive, resume playback


@dataclass(frozen=True)
class BargeInResult:
    """Result of barge-in analysis (immutable value object)."""

    action: BargeInAction
    should_stop_tts: bool
    acknowledgment: str | None
    command: ParsedCommand | None


class BargeInHandler:
    """
    Handles barge-in (user interruption during TTS playback).

    Decision flow:
    1. User speaks while TTS playing → Stop TTS immediately
    2. Analyze interruption type:
       - Short (<500ms, e.g., "wait"): Acknowledge and wait
       - Command detected: Execute command
       - Extended speech: Switch to listening mode
    """

    def __init__(self, command_parser: CommandParser | None = None) -> None:
        self.command_parser = command_parser or CommandParser()

    def handle_interruption(
        self,
        speech_duration_ms: int,
        transcript: str | None,
        transcript_confidence: float,
        current_context: CommandContext,
    ) -> BargeInResult:
        """
        Process a user interruption during TTS playback.

        Args:
            speech_duration_ms: Duration of user's speech so far
            transcript: Current transcript (may be partial)
            transcript_confidence: STT confidence
            current_context: What was happening when interrupted

        Returns:
            BargeInResult with recommended action
        """
        # Too short to be intentional - might be noise
        if speech_duration_ms < MIN_BARGE_IN_DURATION_MS:
            return BargeInResult(
                action=BargeInAction.RESUME,
                should_stop_tts=False,
                acknowledgment=None,
                command=None,
            )

        # Always stop TTS for any real speech
        should_stop = True

        # No transcript yet - stop and wait
        if not transcript:
            return BargeInResult(
                action=BargeInAction.LISTEN,
                should_stop_tts=should_stop,
                acknowledgment=None,
                command=None,
            )

        # Check for commands
        command = self.command_parser.parse(
            transcript,
            context=current_context,
            transcript_confidence=transcript_confidence,
        )

        # Command detected with high confidence
        if command.command_type != CommandType.ANSWER and command.confidence >= 0.7:
            return BargeInResult(
                action=BargeInAction.EXECUTE_COMMAND,
                should_stop_tts=should_stop,
                acknowledgment=self._get_command_acknowledgment(command),
                command=command,
            )

        # Short interruption - acknowledge and wait for more
        if speech_duration_ms < SHORT_INTERRUPTION_MS:
            return BargeInResult(
                action=BargeInAction.ACKNOWLEDGE_WAIT,
                should_stop_tts=should_stop,
                acknowledgment="Yes?",
                command=None,
            )

        # Extended speech - switch to listening mode
        return BargeInResult(
            action=BargeInAction.LISTEN,
            should_stop_tts=should_stop,
            acknowledgment=None,
            command=None,
        )

    def _get_command_acknowledgment(self, command: ParsedCommand) -> str:
        """Get acknowledgment message for a command."""
        acknowledgments = {
            CommandType.SKIP: "Skipping.",
            CommandType.REPEAT: "Sure, I'll repeat.",
            CommandType.HINT: "Here's a hint.",
            CommandType.STOP: "Ending session.",
            CommandType.UNDO: "Going back.",
            CommandType.EXPLAIN: "Let me explain.",
            CommandType.SLOWER: "I'll speak slower.",
            CommandType.FASTER: "I'll speak faster.",
        }
        return acknowledgments.get(command.command_type, "Got it.")
