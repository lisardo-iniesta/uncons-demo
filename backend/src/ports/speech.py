"""Port interfaces for speech services (STT/TTS)."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from src.domain.value_objects.transcript import Transcript


@runtime_checkable
class STTPort(Protocol):
    """Speech-to-Text port interface."""

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[Transcript]:
        """
        Transcribe streaming audio to text.

        Yields interim transcripts followed by a final transcript.
        Final transcript has is_final=True.
        """
        ...


@runtime_checkable
class TTSPort(Protocol):
    """Text-to-Speech port interface."""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Convert text to streaming audio.

        Yields audio chunks as they're generated for low-latency playback.
        """
        ...

    def cancel(self) -> None:
        """Cancel ongoing synthesis (for barge-in support)."""
        ...


class VADPort(ABC):
    """Voice Activity Detection port interface."""

    @abstractmethod
    def is_speech(self, audio_chunk: bytes) -> bool:
        """Check if audio chunk contains speech."""
        ...

    @abstractmethod
    def get_speech_probability(self, audio_chunk: bytes) -> float:
        """Get probability that audio chunk contains speech (0.0-1.0)."""
        ...
