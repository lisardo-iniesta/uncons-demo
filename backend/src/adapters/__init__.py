# Adapters layer - Concrete implementations (Gemini, Deepgram, AnkiConnect)

from .anki_connect import AnkiConnectAdapter, AnkiConnectError
from .deepgram_stt import DeepgramSTTAdapter
from .deepgram_tts import DeepgramTTSAdapter
from .gemini_adapter import GeminiAdapter

__all__ = [
    "AnkiConnectAdapter",
    "AnkiConnectError",
    "DeepgramSTTAdapter",
    "DeepgramTTSAdapter",
    "GeminiAdapter",
]
