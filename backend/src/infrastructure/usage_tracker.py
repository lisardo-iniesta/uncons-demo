"""Unified usage tracking for all billable services.

Logs usage to a JSONL file for cost monitoring and analysis.
Services tracked: Gemini LLM, Deepgram STT/TTS, Cartesia TTS, LiveKit sessions.
"""

import json
import logging
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ServiceType(str, Enum):
    """Billable service types."""

    GEMINI = "gemini"
    DEEPGRAM_STT = "deepgram_stt"
    DEEPGRAM_TTS = "deepgram_tts"
    CARTESIA_TTS = "cartesia_tts"
    LIVEKIT_SESSION = "livekit_session"


# Gemini pricing per 1M tokens (USD) - updated 2026-01
GEMINI_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
}

# Deepgram pricing
DEEPGRAM_STT_PRICE_PER_MINUTE = 0.0043  # Nova-2: $0.0043/minute
DEEPGRAM_TTS_PRICE_PER_1K_CHARS = 0.015  # Aura: $0.015 per 1,000 characters

# Cartesia pricing (Pro plan estimate)
CARTESIA_TTS_PRICE_PER_1K_CHARS = 0.038  # ~$0.038 per 1,000 characters

# LiveKit pricing (estimated participant-minutes)
LIVEKIT_PRICE_PER_PARTICIPANT_MINUTE = 0.0035

# Default log path (relative to backend/)
DEFAULT_USAGE_LOG = Path(__file__).parent.parent.parent / "logs" / "usage.jsonl"

# Backward compatibility alias
MODEL_PRICING = GEMINI_PRICING


# =============================================================================
# Cost Calculation Functions
# =============================================================================


def calculate_gemini_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate Gemini LLM cost in USD."""
    pricing = GEMINI_PRICING.get(model, GEMINI_PRICING["gemini-2.0-flash"])
    input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    output_cost = (completion_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


def calculate_deepgram_stt_cost(audio_duration_seconds: float) -> float:
    """Calculate Deepgram STT cost in USD."""
    minutes = audio_duration_seconds / 60
    return minutes * DEEPGRAM_STT_PRICE_PER_MINUTE


def calculate_deepgram_tts_cost(characters_count: int) -> float:
    """Calculate Deepgram TTS cost in USD."""
    return (characters_count / 1000) * DEEPGRAM_TTS_PRICE_PER_1K_CHARS


def calculate_cartesia_tts_cost(characters_count: int) -> float:
    """Calculate Cartesia TTS cost in USD."""
    return (characters_count / 1000) * CARTESIA_TTS_PRICE_PER_1K_CHARS


def calculate_livekit_session_cost(duration_seconds: float, participant_count: int) -> float:
    """Calculate LiveKit session cost (participant-minutes) in USD."""
    minutes = duration_seconds / 60
    return minutes * participant_count * LIVEKIT_PRICE_PER_PARTICIPANT_MINUTE


# Backward compatibility alias
calculate_cost = calculate_gemini_cost


# =============================================================================
# Logging Functions
# =============================================================================


def _log_entry(entry: dict[str, Any], log_path: Path | None = None) -> None:
    """Internal: write a log entry to JSONL file.

    Non-blocking: failures are logged but don't raise.
    """
    log_file = log_path or DEFAULT_USAGE_LOG
    log_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.warning(f"Failed to log usage: {e}")


def log_gemini_usage(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    log_path: Path | None = None,
) -> None:
    """Log Gemini LLM usage."""
    cost = calculate_gemini_cost(model, prompt_tokens, completion_tokens)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": ServiceType.GEMINI.value,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": round(cost, 6),
    }
    _log_entry(entry, log_path)


def log_deepgram_stt_usage(
    audio_duration_seconds: float,
    model: str = "nova-2",
    log_path: Path | None = None,
) -> None:
    """Log Deepgram STT usage."""
    cost = calculate_deepgram_stt_cost(audio_duration_seconds)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": ServiceType.DEEPGRAM_STT.value,
        "model": model,
        "audio_duration_seconds": round(audio_duration_seconds, 3),
        "cost_usd": round(cost, 6),
    }
    _log_entry(entry, log_path)


def log_deepgram_tts_usage(
    characters_count: int,
    audio_duration_seconds: float | None = None,
    model: str = "aura-asteria-en",
    log_path: Path | None = None,
) -> None:
    """Log Deepgram TTS usage."""
    cost = calculate_deepgram_tts_cost(characters_count)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": ServiceType.DEEPGRAM_TTS.value,
        "model": model,
        "characters_count": characters_count,
        "cost_usd": round(cost, 6),
    }
    if audio_duration_seconds is not None:
        entry["audio_duration_seconds"] = round(audio_duration_seconds, 3)
    _log_entry(entry, log_path)


def log_cartesia_tts_usage(
    characters_count: int,
    model: str = "sonic-2",
    log_path: Path | None = None,
) -> None:
    """Log Cartesia TTS usage (for future use)."""
    cost = calculate_cartesia_tts_cost(characters_count)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": ServiceType.CARTESIA_TTS.value,
        "model": model,
        "characters_count": characters_count,
        "cost_usd": round(cost, 6),
    }
    _log_entry(entry, log_path)


def log_livekit_session_usage(
    session_id: str,
    room_name: str,
    duration_seconds: float,
    participant_count: int = 2,
    log_path: Path | None = None,
) -> None:
    """Log LiveKit session usage."""
    cost = calculate_livekit_session_cost(duration_seconds, participant_count)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": ServiceType.LIVEKIT_SESSION.value,
        "session_id": session_id,
        "room_name": room_name,
        "duration_seconds": round(duration_seconds, 2),
        "participant_count": participant_count,
        "cost_usd": round(cost, 6),
    }
    _log_entry(entry, log_path)


# Backward compatibility alias
log_usage = log_gemini_usage


# =============================================================================
# Summary Functions
# =============================================================================


def get_usage_summary(log_path: Path | None = None) -> dict[str, Any]:
    """Get summary of usage from log file, aggregated by service.

    Returns:
        Summary dict with per-service breakdowns and totals.
    """
    log_file = log_path or DEFAULT_USAGE_LOG

    if not log_file.exists():
        return {
            "total_cost_usd": 0.0,
            "total_requests": 0,
            "by_service": {},
        }

    by_service: dict[str, dict[str, Any]] = {}
    total_cost = 0.0
    total_requests = 0

    with log_file.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                # Handle legacy entries without 'service' field
                service = entry.get("service", "gemini")
                cost = entry.get("cost_usd", 0)
                total_cost += cost
                total_requests += 1

                if service not in by_service:
                    by_service[service] = {
                        "count": 0,
                        "cost_usd": 0.0,
                    }

                by_service[service]["count"] += 1
                by_service[service]["cost_usd"] += cost

                # Service-specific aggregations
                if service == "gemini":
                    by_service[service].setdefault("total_tokens", 0)
                    by_service[service]["total_tokens"] += entry.get("total_tokens", 0)
                elif service == "deepgram_stt":
                    by_service[service].setdefault("total_audio_seconds", 0)
                    by_service[service]["total_audio_seconds"] += entry.get(
                        "audio_duration_seconds", 0
                    )
                elif service in ("deepgram_tts", "cartesia_tts"):
                    by_service[service].setdefault("total_characters", 0)
                    by_service[service]["total_characters"] += entry.get("characters_count", 0)
                elif service == "livekit_session":
                    by_service[service].setdefault("total_duration_seconds", 0)
                    by_service[service]["total_duration_seconds"] += entry.get(
                        "duration_seconds", 0
                    )

            except json.JSONDecodeError:
                continue

    # Round costs
    for service_data in by_service.values():
        service_data["cost_usd"] = round(service_data["cost_usd"], 4)

    return {
        "total_cost_usd": round(total_cost, 4),
        "total_requests": total_requests,
        "by_service": by_service,
    }
