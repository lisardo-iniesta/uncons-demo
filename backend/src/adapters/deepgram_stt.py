"""Deepgram Speech-to-Text adapter."""

import logging
from collections.abc import AsyncIterator

from src.domain.value_objects.transcript import SpeechSegment, Transcript

logger = logging.getLogger(__name__)


class DeepgramSTTAdapter:
    """
    Deepgram STT adapter implementing STTPort.

    Note: In LiveKit Agents context, we primarily use livekit.plugins.deepgram.STT.
    This adapter provides a standalone implementation for testing and flexibility.
    """

    def __init__(
        self,
        model: str = "nova-2",
        language: str = "en",
        sample_rate: int = 16000,
    ) -> None:
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self._client = None

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[Transcript]:
        """
        Transcribe streaming audio using Deepgram's live transcription API.

        Yields interim transcripts as speech is detected, then a final transcript
        when the utterance is complete.
        """
        # Import here to avoid loading SDK when not used
        from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

        if self._client is None:
            self._client = DeepgramClient()

        options = LiveOptions(
            model=self.model,
            language=self.language,
            sample_rate=self.sample_rate,
            encoding="linear16",
            channels=1,
            interim_results=True,
            utterance_end_ms=300,
            vad_events=True,
        )

        connection = self._client.listen.live.v("1")

        # Buffer for collecting results
        results: list[Transcript] = []

        def on_transcript(_, result, **kwargs):
            if result.channel and result.channel.alternatives:
                alt = result.channel.alternatives[0]
                transcript = Transcript(
                    text=alt.transcript,
                    confidence=alt.confidence,
                    is_final=result.is_final,
                    segments=tuple(
                        SpeechSegment(
                            text=word.word,
                            start_time_ms=int(word.start * 1000),
                            end_time_ms=int(word.end * 1000),
                            confidence=word.confidence,
                        )
                        for word in (alt.words or [])
                    ),
                )
                results.append(transcript)

        connection.on(LiveTranscriptionEvents.Transcript, on_transcript)

        await connection.start(options)

        try:
            async for chunk in audio_stream:
                connection.send(chunk)

                # Yield any accumulated results
                while results:
                    yield results.pop(0)

            await connection.finish()

            # Yield remaining results
            while results:
                yield results.pop(0)

        finally:
            await connection.finish()

    @staticmethod
    def from_livekit_event(
        text: str,
        confidence: float,
        is_final: bool,
    ) -> Transcript:
        """
        Create Transcript from LiveKit STT event data.

        Use this when integrating with LiveKit Agents framework.
        """
        return Transcript(
            text=text,
            confidence=confidence,
            is_final=is_final,
        )
