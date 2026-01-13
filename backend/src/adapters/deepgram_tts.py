"""Deepgram Text-to-Speech adapter."""

import asyncio
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class DeepgramTTSAdapter:
    """
    Deepgram TTS adapter implementing TTSPort.

    Supports streaming synthesis with cancellation for barge-in handling.

    Note: In LiveKit Agents context, we primarily use livekit.plugins.deepgram.TTS.
    This adapter provides a standalone implementation for testing and flexibility.
    """

    def __init__(
        self,
        model: str = "aura-asteria-en",
        sample_rate: int = 16000,
    ) -> None:
        self.model = model
        self.sample_rate = sample_rate
        self._cancelled = False
        self._client = None  # Lazy-initialized, reused across calls

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Convert text to streaming audio.

        Yields audio chunks as they're generated for low-latency playback.
        Can be cancelled mid-stream via cancel() for barge-in support.
        """
        self._cancelled = False

        # Import here to avoid loading SDK when not used
        from deepgram import DeepgramClient, SpeakOptions

        # Reuse client across calls (saves 20-50ms per call)
        if self._client is None:
            self._client = DeepgramClient()
        client = self._client

        options = SpeakOptions(
            model=self.model,
            encoding="linear16",
            sample_rate=self.sample_rate,
        )

        try:
            response = await client.speak.asyncrest.v("1").stream_raw(
                {"text": text},
                options,
            )

            async for chunk in response.aiter_bytes():
                if self._cancelled:
                    logger.info("TTS cancelled (barge-in)")
                    break
                yield chunk

        except asyncio.CancelledError:
            logger.info("TTS task cancelled")
            raise

    def cancel(self) -> None:
        """Cancel ongoing synthesis (for barge-in support)."""
        self._cancelled = True
