"""
UNCONS Agent Worker.

LiveKit Agents worker entrypoint for the UNCONS voice tutor.

Run:
    poetry run python src/agents/worker.py dev        # Development mode
    poetry run python src/agents/worker.py start      # Production mode

Environment variables required:
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
    DEEPGRAM_API_KEY
    GOOGLE_API_KEY (for Gemini)
    CARTESIA_API_KEY
    ANKI_CONNECT_URL (default: http://localhost:8765)
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time as time_module
from pathlib import Path

import ulid
from dotenv import load_dotenv

# Load .env from project root (parent of backend/)
env_path = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(env_path)

from livekit import agents, rtc  # noqa: E402
from livekit.agents import AgentServer, AgentSession, RoomIO, RoomInputOptions  # noqa: E402
from livekit.plugins import deepgram, google, silero  # noqa: E402

from src.adapters.anki_connect import AnkiConnectAdapter  # noqa: E402
from src.agents.uncons_agent import UnconsAgent  # noqa: E402
from src.domain.services.card_sanitizer import sanitize_question_for_tts  # noqa: E402
from src.domain.services.session_manager import SessionManager  # noqa: E402
from src.infrastructure.recovery_store import RecoveryStore  # noqa: E402
from src.infrastructure.usage_tracker import (  # noqa: E402
    log_deepgram_stt_usage,
    log_deepgram_tts_usage,
    log_livekit_session_usage,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Track TTS billing errors to avoid spamming logs
_tts_billing_warned = False

# Track background tasks for cleanup on room disconnect
# This prevents "ghost" responses from old sessions appearing after refresh
_background_tasks: set[asyncio.Task] = set()


def create_tracked_task(coro, name: str = "unnamed") -> asyncio.Task:
    """Create a background task that gets cancelled on room disconnect.

    This replaces fire-and-forget asyncio.create_task() calls.
    All tracked tasks are cancelled when the room disconnects,
    preventing delayed responses from appearing in new sessions.

    Args:
        coro: The coroutine to run
        name: Name for logging (e.g., "handle_text_input")

    Returns:
        The created Task object
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def cleanup(t):
        _background_tasks.discard(t)
        if t.cancelled():
            logger.debug(f"Task {name} was cancelled")
        elif t.exception():
            # Don't log CancelledError as error
            exc = t.exception()
            if not isinstance(exc, asyncio.CancelledError):
                logger.error(f"Task {name} failed: {exc}")

    task.add_done_callback(cleanup)
    return task


def calculate_tts_timeout(text: str) -> float:
    """Calculate TTS timeout based on text length.

    Assumes ~150 words per minute speaking rate.
    Add buffer for TTS initialization and network latency.

    Args:
        text: The text to be spoken

    Returns:
        Timeout in seconds (minimum 15s, maximum 30s)
    """
    words = len(text.split())
    speaking_time = (words / 150) * 60  # seconds
    buffer = 5.0  # TTS init + network
    return max(15.0, min(30.0, speaking_time + buffer))


async def check_cartesia_credits() -> bool:
    """Check if Cartesia TTS has available credits.

    Makes a minimal TTS request to verify the API key works.
    Returns True if credits available, False if billing issue.
    """
    import httpx

    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        logger.error("=" * 60)
        logger.error("CARTESIA_API_KEY not set!")
        logger.error("TTS will not work. Set the API key in .env file.")
        logger.error("=" * 60)
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Use Cartesia's streaming endpoint with minimal text
            response = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers={
                    "X-API-Key": api_key,
                    "Cartesia-Version": "2024-06-10",
                    "Content-Type": "application/json",
                },
                json={
                    "model_id": "sonic-2",
                    "transcript": "test",
                    "voice": {"mode": "id", "id": "79a125e8-cd45-4c13-8a67-188112f4dd22"},
                    "output_format": {
                        "container": "raw",
                        "encoding": "pcm_s16le",
                        "sample_rate": 24000,
                    },
                },
            )

            if response.status_code == 402:
                logger.error("=" * 60)
                logger.error("CARTESIA TTS BILLING ERROR (402)")
                logger.error("Your Cartesia account has run out of credits!")
                logger.error("Please add credits at: https://play.cartesia.ai/")
                logger.error("=" * 60)
                return False
            elif response.status_code == 401:
                logger.error("=" * 60)
                logger.error("CARTESIA API KEY INVALID (401)")
                logger.error("Check your CARTESIA_API_KEY in .env file.")
                logger.error("=" * 60)
                return False
            elif response.status_code >= 400:
                logger.warning(f"Cartesia API check returned {response.status_code}")
                return True  # Assume it might work

            logger.info("Cartesia TTS credits verified - OK")
            return True

    except Exception as e:
        logger.warning(f"Could not verify Cartesia credits: {e}")
        return True  # Assume it might work, don't block startup


def log_tts_billing_error():
    """Log TTS billing error once per session."""
    global _tts_billing_warned
    if not _tts_billing_warned:
        _tts_billing_warned = True
        logger.error("=" * 60)
        logger.error("TTS FAILED - LIKELY BILLING ISSUE")
        logger.error("If you see 402 errors, add credits at:")
        logger.error("https://play.cartesia.ai/")
        logger.error("=" * 60)


class AgentMessagePublisher:
    """Single source of truth for publishing agent messages to frontend.

    This class eliminates duplicate messages by:
    1. Using a single publish method for all agent messages
    2. Generating unique message IDs for deduplication
    3. Allowing pre-send and speech_created to share the same ID for identical content
    """

    def __init__(self, room: rtc.Room, pub_logger: logging.Logger):
        self._room = room
        self._logger = pub_logger
        self._pending_message_ids: dict[str, str] = {}  # text_hash → message_id

    def get_or_create_message_id(self, text: str) -> str:
        """Get existing message ID for text or create new one.

        This allows pre-send and speech_created to share the same ID
        for the same text content, enabling frontend deduplication.
        """
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        if text_hash not in self._pending_message_ids:
            self._pending_message_ids[text_hash] = str(ulid.ULID())
        return self._pending_message_ids[text_hash]

    def clear_message_id(self, text: str) -> None:
        """Clear message ID after speech completes (prevent memory leak)."""
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        self._pending_message_ids.pop(text_hash, None)

    async def publish_agent_message(self, text: str, message_id: str | None = None) -> None:
        """Publish agent message with guaranteed ID and topic."""
        if not text:
            return

        msg_id = message_id or self.get_or_create_message_id(text)

        try:
            await self._room.local_participant.publish_data(
                json.dumps(
                    {
                        "type": "agent_message",
                        "text": text,
                        "id": msg_id,
                    }
                ).encode("utf-8"),
                reliable=True,
                topic="agent-response",
            )
            self._logger.info(f"Published agent_message [{msg_id[:8]}...]: {text[:30]}...")
        except Exception as e:
            self._logger.error(f"Failed to publish agent_message: {e}")


def prewarm(proc: agents.JobProcess) -> None:
    """Prewarm models and initialize shared resources.

    This runs once when the worker starts, avoiding per-session latency.
    """
    logger.info("Prewarming UNCONS agent...")

    # Note: TTS is now using Deepgram, so Cartesia credit check is disabled
    # The check_cartesia_credits() function is kept for future reference
    proc.userdata["tts_credits_ok"] = True

    # Load VAD model with error handling
    try:
        proc.userdata["vad"] = silero.VAD.load()
        logger.info("VAD model loaded successfully")
    except Exception as e:
        logger.warning(f"VAD prewarming failed: {e}, will load lazily on first session")
        proc.userdata["vad"] = None

    # Initialize recovery store (SQLite)
    # Use same default path as API (~/.uncons/recovery.db) so both processes share the same DB
    default_db_path = str(Path.home() / ".uncons" / "recovery.db")
    db_path = os.getenv("RECOVERY_DB_PATH", default_db_path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    proc.userdata["recovery_store"] = RecoveryStore(db_path=db_path)
    logger.info(f"Recovery store initialized: {Path(db_path).resolve()}")

    # Initialize flashcard adapter based on configuration
    adapter_type = os.getenv("FLASHCARD_ADAPTER", "anki").lower()

    if adapter_type == "local":
        from src.adapters.local_test_deck import LocalTestDeckAdapter

        logger.info("Using LOCAL test deck adapter (no Anki required)")
        flashcard_adapter = LocalTestDeckAdapter()
    elif adapter_type == "anki":
        anki_url = os.getenv("ANKI_CONNECT_URL", "http://localhost:8765")
        flashcard_adapter = AnkiConnectAdapter(url=anki_url)

        # Wait for Anki to be available (with retries)
        connected = asyncio.run(flashcard_adapter.wait_for_connection())
        if connected:
            logger.info(f"AnkiConnect ready: {anki_url}")
        else:
            logger.warning("AnkiConnect not available at startup - will retry on first request")
    else:
        raise ValueError(
            f"Invalid FLASHCARD_ADAPTER: '{adapter_type}'. " "Valid options: 'anki', 'local'"
        )

    proc.userdata["anki_adapter"] = flashcard_adapter

    logger.info("UNCONS agent prewarmed successfully")


# Get port from environment (Railway sets PORT, default to 8081 for local dev)
http_port = int(os.getenv("PORT", 8081))
server = AgentServer(port=http_port)
server.setup_fnc = prewarm


@server.rtc_session()
async def uncons_session(ctx: agents.JobContext):
    """Main entry point for UNCONS agent sessions.

    Each voice session gets its own instance of:
    - EvaluationService (with Gemini adapter)
    - VoiceOrchestrator (LangGraph state machine)
    - UnconsAgent (LiveKit agent)
    """
    logger.info("UNCONS session starting...")

    # Connect to the LiveKit room first (required for audio to work)
    await ctx.connect()

    # DIAGNOSTIC: Log room connection with participant details
    logger.info(
        "Agent connected to room",
        extra={
            "room_name": ctx.room.name,
            "agent_id": ctx.room.local_participant.identity,
            "participant_count": len(ctx.room.remote_participants),
            "participants": [p.identity for p in ctx.room.remote_participants.values()],
        },
    )

    # Check if another agent is already in room (defense in depth)
    from livekit import rtc

    agent_participants = [
        p
        for p in ctx.room.remote_participants.values()
        if p.identity.startswith("agent-") or p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
    ]
    if agent_participants:
        logger.warning(
            "Another agent already in room, disconnecting",
            extra={
                "room_name": ctx.room.name,
                "existing_agents": [p.identity for p in agent_participants],
            },
        )
        await ctx.room.disconnect()
        return  # Exit early - don't create duplicate agent

    # Log when new participants join
    @ctx.room.on("participant_connected")
    def on_participant_connected(participant):
        logger.warning(
            "New participant joined room",
            extra={
                "participant_id": participant.identity,
                "participant_name": participant.name,
                "room_name": ctx.room.name,
                "total_participants": len(ctx.room.remote_participants) + 1,
            },
        )

    # Cancel all background tasks when room disconnects
    # This prevents "ghost" responses from appearing after user refreshes
    @ctx.room.on("disconnected")
    def on_room_disconnected():
        global _background_tasks
        task_count = len(_background_tasks)
        if task_count > 0:
            logger.info(f"Room disconnected, cancelling {task_count} pending tasks")
            for task in list(_background_tasks):
                if not task.done():
                    task.cancel()
            _background_tasks.clear()
        else:
            logger.info("Room disconnected, no pending tasks to cancel")

    # Get prewarmed resources (with lazy loading fallback for VAD)
    vad = ctx.proc.userdata["vad"]
    if vad is None:
        logger.info("VAD not prewarmed, loading now...")
        try:
            vad = silero.VAD.load()
            ctx.proc.userdata["vad"] = vad
            logger.info("VAD model loaded on-demand")
        except Exception as e:
            logger.error(f"VAD lazy load failed: {e}, session cannot start")
            raise RuntimeError(f"VAD initialization failed: {e}") from e
    recovery_store = ctx.proc.userdata["recovery_store"]
    anki_adapter = ctx.proc.userdata["anki_adapter"]

    # Create session-scoped services via composition root
    from src.composition import create_hint_service, create_voice_orchestrator

    orchestrator = create_voice_orchestrator()
    hint_service = create_hint_service()

    # Use shorter timeout in dev for easier testing
    is_dev = os.getenv("ENVIRONMENT", "development") != "production"
    timeout_minutes = 5 if is_dev else 30

    session_manager = SessionManager(
        flashcard_service=anki_adapter,
        recovery_store=recovery_store,
        timeout_minutes=timeout_minutes,
    )

    # Track recently published texts for deduplication
    # When uncons_agent.publish_agent_message() runs, it marks text as published
    # Then conversation_item_added can skip duplicates
    _published_text_hashes: dict[str, float] = {}

    def mark_text_published(text: str) -> None:
        """Mark text as published to prevent duplicate via conversation_item_added."""
        # Hash only first 30 chars - SDK heavily truncates text in conversation_item_added
        prefix = text[:30]
        text_hash = hashlib.sha256(prefix.encode()).hexdigest()[:16]
        _published_text_hashes[text_hash] = time_module.time()
        logger.info(f"Marked text as published: hash={text_hash}, prefix='{prefix}'")

    # Create agent with room for data channel communication
    agent = UnconsAgent(
        orchestrator=orchestrator,
        session_manager=session_manager,
        room=ctx.room,
        hint_service=hint_service,
        on_message_published=mark_text_published,
    )

    # Always use PTT mode (VAD removed for simpler UX)
    ptt_mode = True
    logger.info("Using PTT mode (always enabled)")

    # Track accumulated transcript during PTT for question detection
    _ptt_transcript_buffer: list[str] = []
    _ptt_recording = False

    def is_question(text: str) -> bool:
        """Detect if text is a follow-up question (not an answer attempt)."""
        text_lower = text.lower().strip()
        # Question indicators - words that signal a question
        question_starters = ["what ", "how ", "why ", "can you", "could you", "when ", "where ", "who ", "which "]
        question_keywords = ["explain", "tell me", "give me an example", "more detail", "elaborate"]

        # Check for question mark
        if "?" in text:
            return True

        # Check for question starters
        for starter in question_starters:
            if text_lower.startswith(starter):
                return True

        # Check for question keywords
        for keyword in question_keywords:
            if keyword in text_lower:
                return True

        return False

    # Create LiveKit session with voice pipeline
    # Use manual turn detection for PTT mode, automatic VAD otherwise
    session = AgentSession(
        # Turn detection: manual for PTT, None for VAD (auto)
        turn_detection="manual" if ptt_mode else None,
        # VAD: Silero (prewarmed) - still needed even in PTT for end-of-speech detection
        vad=vad,
        # STT: Deepgram Nova-2
        stt=deepgram.STT(
            model="nova-2",
            language="en",
            smart_format=True,
            interim_results=True,
        ),
        # LLM: Gemini 2.0 Flash via native Google plugin
        llm=google.LLM(
            model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_AI_API_KEY"),
        ),
        tts=deepgram.TTS(),
    )

    # Start session
    # For PTT mode: Use explicit RoomIO setup and disable audio after start
    # For VAD mode: Use default room handling
    # IMPORTANT: close_on_disconnect=False keeps session alive during brief network hiccups
    input_options = RoomInputOptions(close_on_disconnect=False)

    if ptt_mode:
        # Create RoomIO explicitly for PTT mode (required for audio toggle to work)
        session_room_io = RoomIO(session, room=ctx.room, input_options=input_options)
        await session_room_io.start()
        await session.start(agent=agent)  # Don't pass room - RoomIO handles it
        # Disable audio input AFTER session is started (audio stream must exist first)
        session.input.set_audio_enabled(False)
        logger.info("PTT mode: Audio input disabled until user presses button")
    else:
        await session.start(
            room=ctx.room,
            agent=agent,
            room_input_options=input_options,
        )

    logger.info(f"UNCONS session ready (mode: {'PTT' if ptt_mode else 'VAD'})")

    # Track session start time for usage logging
    session_start_time = time_module.time()
    session_room_name = ctx.room.name

    # Track STT/TTS usage via LiveKit's metrics_collected event
    @session.on("metrics_collected")
    def on_metrics_collected(metrics):
        """Log STT and TTS usage metrics for cost tracking."""
        metrics_type = type(metrics).__name__

        if "STT" in metrics_type:
            # STTMetrics has: audio_duration, label (model name)
            audio_duration = getattr(metrics, "audio_duration", 0)
            model = getattr(metrics, "label", "nova-2")
            if audio_duration and audio_duration > 0:
                logger.debug(f"STT metrics: {audio_duration:.2f}s audio, model={model}")
                log_deepgram_stt_usage(
                    audio_duration_seconds=audio_duration,
                    model=model,
                )

        elif "TTS" in metrics_type:
            # TTSMetrics has: characters_count, audio_duration, label, cancelled
            characters = getattr(metrics, "characters_count", 0)
            audio_duration = getattr(metrics, "audio_duration", None)
            model = getattr(metrics, "label", "aura-asteria-en")
            cancelled = getattr(metrics, "cancelled", False)

            # Only log if not cancelled (barge-in)
            if characters and characters > 0 and not cancelled:
                logger.debug(f"TTS metrics: {characters} chars, model={model}")
                log_deepgram_tts_usage(
                    characters_count=characters,
                    audio_duration_seconds=audio_duration,
                    model=model,
                )

    # Log user voice input (STT transcription)
    # Note: Voice commands (hint, skip) are NOT parsed here - they go through
    # the LLM which handles them poorly. Instead, use UI buttons for commands.
    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event):
        nonlocal _ptt_transcript_buffer
        if event.is_final:
            logger.info(
                "User voice input",
                extra={
                    "room_name": ctx.room.name,
                    "transcript": event.transcript,
                    "language": event.language,
                },
            )
            # Accumulate transcripts during PTT for question detection
            if _ptt_recording:
                _ptt_transcript_buffer.append(event.transcript)

    # Initialize message publisher (single source of truth for agent messages)
    publisher = AgentMessagePublisher(ctx.room, logger)

    # Voice question handler - routes spoken questions to fast Gemini path
    # Mirrors handle_question() but for voice input (PTT)
    async def handle_voice_question(question: str):
        """Handle a question detected from PTT voice input.

        Routes to fast Gemini API path (~2s) instead of slow evaluation (~9s).
        """
        try:
            import google.generativeai as genai

            from src.domain.services.card_sanitizer import sanitize_for_tts

            logger.info(
                "Voice question (PTT detected)",
                extra={
                    "room_name": ctx.room.name,
                    "question": question,
                },
            )

            # Interrupt any ongoing agent speech
            session.interrupt()

            # Get current card context for the question
            card = agent.get_current_card()
            if not card:
                no_card_msg = "I don't have a card loaded to answer questions about."
                speech_handle = session.say(no_card_msg, add_to_chat_ctx=False)
                await publisher.publish_agent_message(no_card_msg)
                await speech_handle
                return

            # Build LLM-generated educational response
            card_front = sanitize_for_tts(card["front"])
            card_back = sanitize_for_tts(card["back"])
            question_lower = question.lower()

            # Get conversation context for follow-up questions
            previous_hints = agent.get_previous_hints()
            question_history = agent.get_question_history()
            user_attempts = agent.get_user_attempts()
            socratic_context = agent.get_socratic_context()

            # Build conversation context string
            conv_context = ""

            if user_attempts:
                conv_context += "\n<user_attempts>\n"
                for attempt in user_attempts[-2:]:
                    conv_context += f'- "{attempt}"\n'
                conv_context += "</user_attempts>\n"

            if socratic_context:
                conv_context += "\n<socratic_discussion>\n"
                for turn in socratic_context[-4:]:
                    conv_context += f"{turn}\n"
                conv_context += "</socratic_discussion>\n"

            if previous_hints:
                conv_context += "\n<previous_hints>\n"
                for h in previous_hints[-3:]:
                    conv_context += f"- {h}\n"
                conv_context += "</previous_hints>\n"

            if question_history:
                conv_context += "\n<conversation>\n"
                for qa in question_history[-3:]:
                    conv_context += f"User: {qa['q']}\nAssistant: {qa['a']}\n"
                conv_context += "</conversation>\n"

            # Build prompt based on question type
            if "explain" in question_lower or "more detail" in question_lower:
                prompt = f"""Context (user already sees this):
Q: {card_front}
A: {card_back}
{conv_context}
TASK: Give ONE insight they WON'T find in the answer above.

Rules:
- DO NOT summarize or rephrase the answer
- Share the "aha moment" or mental model behind this concept
- If there's conversation history, build on previous responses
- 2 sentences max
- Start directly with the insight"""

            elif "example" in question_lower:
                prompt = f"""Context (user already sees this):
Q: {card_front}
A: {card_back}
{conv_context}
TASK: One SPECIFIC real-world scenario (not mentioned in the answer).

Rules:
- Use concrete names/situations (e.g., "When Netflix migrated...")
- Show cause and effect
- If there's conversation history, build on or connect to previous examples
- 2-3 sentences max
- Start directly with the example"""

            elif "why" in question_lower or "important" in question_lower:
                prompt = f"""Context (user already sees this):
Q: {card_front}
A: {card_back}
{conv_context}
TASK: Why does this matter? What breaks without it?

Rules:
- Focus on consequences, not definitions
- Be specific (name a real problem it prevents)
- If there's conversation history, connect to previous discussion
- 2 sentences max
- Start directly"""

            else:
                prompt = f"""Context (user already sees this):
Q: {card_front}
A: {card_back}
{conv_context}
User asks: {question}

Rules:
- Answer their specific question directly
- CRITICAL: If user references something from conversation history, use that context
- Add value they can't get from the card
- 2-3 sentences max
- Start directly with your answer"""

            # Direct Gemini API call (bypasses LiveKit agent tools)
            genai.configure(
                api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
            )
            model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))

            response = await model.generate_content_async(prompt)
            response_text = response.text.strip()

            # Track Q&A for conversation context
            agent.add_question_exchange(question, response_text)

            # Use session.say() to speak the generated response
            # Publish BEFORE TTS starts for immediate text display
            await publisher.publish_agent_message(response_text)
            speech_handle = session.say(response_text, add_to_chat_ctx=False)
            await speech_handle

        except Exception as e:
            logger.error(f"Error handling voice question: {e}")
            # Graceful fallback
            try:
                fallback_msg = "I had trouble answering that. Could you try rephrasing?"
                await publisher.publish_agent_message(fallback_msg)
                speech_handle = session.say(fallback_msg, add_to_chat_ctx=False)
                await speech_handle
            except Exception as fallback_err:
                logger.error(f"Fallback also failed: {fallback_err}")

    # Graceful shutdown handler (registered early to catch all shutdown scenarios)
    async def cleanup_session():
        """Cleanup on worker shutdown (SIGTERM, SIGINT) or between tests.

        Extended timeout for reliable Anki sync completion.
        """
        logger.info("Shutdown hook triggered, cleaning up...")

        try:
            # Calculate session duration
            session_duration = time_module.time() - session_start_time

            # End active session (syncs ratings to Anki)
            if agent._session_id:
                await asyncio.wait_for(
                    session_manager.end_session(agent._session_id),
                    timeout=15.0,  # Extended from 8s for reliable sync
                )
                logger.info(f"Session {agent._session_id} ended cleanly")

                # Log LiveKit session usage (participant_count = 2: 1 user + 1 agent)
                log_livekit_session_usage(
                    session_id=agent._session_id,
                    room_name=session_room_name,
                    duration_seconds=session_duration,
                    participant_count=2,
                )
                logger.info(f"Logged session usage: {session_duration:.1f}s")

            # Clear cached state to prevent stale references
            agent._session_id = None
            agent._orchestrator = None

        except TimeoutError:
            logger.warning("Session end timed out, ratings may be lost")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        finally:
            # Close recovery store DB
            try:
                recovery_store.close()
                logger.info("Recovery store closed")
            except Exception as e:
                logger.error(f"Failed to close recovery store: {e}")

            # Force garbage collection to free memory
            import gc

            gc.collect()
            logger.info("Garbage collection complete")

    ctx.add_shutdown_callback(cleanup_session)
    logger.info("Shutdown hook registered")

    # Handle text input and PTT controls from frontend
    # Uses user_input parameter to add text to chat history (same as voice input)
    # This ensures coherent conversation regardless of input method
    @ctx.room.on("data_received")
    def on_data_received(data_packet: rtc.DataPacket):
        try:
            payload = json.loads(data_packet.data.decode("utf-8"))
            msg_type = payload.get("type")
            logger.info(
                f"Data received: type={msg_type}, topic={data_packet.topic}, participant={data_packet.participant.identity if data_packet.participant else 'unknown'}"
            )

            if msg_type == "init_session":
                # Initialize session from data channel (bypasses recovery store)
                # This is needed when API and worker are separate services (Railway)
                deck_name = payload.get("deck_name", "")
                init_session_id = payload.get("session_id", "")
                logger.info(
                    f"Received init_session: deck={deck_name}, session_id={init_session_id}"
                )

                async def handle_init_session():
                    try:
                        # Fetch cards from adapter
                        if deck_name == "All":
                            decks = await anki_adapter.get_decks()
                            semaphore = asyncio.Semaphore(10)

                            async def fetch_deck(deck: str):
                                async with semaphore:
                                    return await anki_adapter.get_reviewable_cards(deck)

                            results = await asyncio.gather(
                                *[fetch_deck(d) for d in decks],
                                return_exceptions=True,
                            )
                            all_cards = []
                            for i, result in enumerate(results):
                                if isinstance(result, Exception):
                                    logger.warning(f"Failed to fetch cards from deck {decks[i]}: {result}")
                                else:
                                    all_cards.extend(result)
                            fetched_cards = all_cards
                        else:
                            fetched_cards = await anki_adapter.get_reviewable_cards(deck_name)

                        logger.info(f"init_session: Fetched {len(fetched_cards)} cards")

                        if not fetched_cards:
                            logger.warning("No cards found for deck")
                            response = "No cards are due for review in this deck right now."
                            await publisher.publish_agent_message(response)
                            speech_handle = session.say(response, add_to_chat_ctx=False)
                            await speech_handle
                            return

                        # Initialize orchestrator with cards
                        cards = [card.to_dict() for card in fetched_cards]
                        orchestrator.create_session(
                            session_id=init_session_id,
                            deck_name=deck_name,
                            cards=cards,
                        )
                        agent._session_id = init_session_id

                        # Restore session in session_manager
                        session_manager.restore_session(
                            session_id=init_session_id,
                            deck_name=deck_name,
                            cards=fetched_cards,
                        )
                        logger.info(f"Session initialized: {init_session_id}")

                        # Present first card
                        first_card = orchestrator.get_current_card()
                        if first_card:
                            logger.info(f"Presenting first card: {first_card['front'][:50]}...")
                            card_text = sanitize_question_for_tts(first_card["front"])
                            await agent.publish_card_update(first_card)
                            await publisher.publish_agent_message(card_text)
                            speech_handle = session.say(card_text, add_to_chat_ctx=False)
                            try:
                                timeout = calculate_tts_timeout(card_text)
                                await asyncio.wait_for(speech_handle, timeout=timeout)
                            except TimeoutError:
                                logger.warning("TTS timed out presenting first card")
                    except Exception as e:
                        logger.error(f"Error in init_session: {e}")
                        error_msg = "There was an error loading your flashcards. Please try again."
                        await publisher.publish_agent_message(error_msg)
                        speech_handle = session.say(error_msg, add_to_chat_ctx=False)
                        await speech_handle

                create_tracked_task(handle_init_session(), "handle_init_session")

            elif msg_type == "user_text_input":
                text = payload.get("text", "")
                logger.info(
                    "User text input",
                    extra={
                        "room_name": ctx.room.name,
                        "text": text,
                    },
                )

                # Process text through the agent's command parser
                # This ensures commands (skip, hint, etc.) are handled properly
                # Commands use session.say() to bypass LLM entirely
                async def handle_text_input():
                    # Guard: Check room still connected before processing
                    if ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED:
                        logger.warning("Room disconnected, skipping text input")
                        return

                    try:
                        import re

                        from src.domain.services.command_parser import (
                            CommandParser,
                            CommandType,
                        )

                        parser = CommandParser()
                        # Check for "next" command explicitly (context-sensitive)
                        text_lower = text.lower().strip()
                        if text_lower in ("next", "continue", "next card"):
                            # Interrupt any ongoing agent speech first
                            session.interrupt()
                            # Handle as NEXT command - bypass LLM with session.say()
                            agent.set_text_input_mode(True)
                            response = await agent._handle_command(None, CommandType.NEXT)
                            # Publish to text mode FIRST (before TTS starts)
                            await publisher.publish_agent_message(response)
                            speech_handle = session.say(response, add_to_chat_ctx=False)
                            await speech_handle
                            # Reset text mode flag since we bypassed evaluate_answer
                            agent.set_text_input_mode(False)
                            return

                        # Reject empty or punctuation-only answers BEFORE sending to LLM
                        # This prevents the LLM from hallucinating valid answers from garbage input
                        # Note: Single characters like "2", "A" are valid (answers to "1+1?" or multiple choice)
                        cleaned_text = re.sub(r"[^\w\s]", "", text).strip()
                        if not cleaned_text:
                            logger.warning(f"Rejecting invalid input (punctuation-only): '{text}'")
                            agent.set_text_input_mode(True)
                            response = "I didn't catch that. Could you please answer the question?"
                            # Publish to text mode FIRST (before TTS starts)
                            await publisher.publish_agent_message(response)
                            speech_handle = session.say(response, add_to_chat_ctx=False)
                            await speech_handle
                            # Reset text mode flag since we bypassed evaluate_answer
                            agent.set_text_input_mode(False)
                            return

                        parsed = parser.parse(text)
                        logger.info(
                            f"Command parsed: text='{text}', command_type={parsed.command_type}, confidence={parsed.confidence}"
                        )

                        # Mark input as from text mode to suppress echo
                        agent.set_text_input_mode(True)

                        if parsed.command_type != CommandType.ANSWER:
                            # It's a command - interrupt any ongoing speech and bypass LLM
                            session.interrupt()
                            response = await agent._handle_command(None, parsed.command_type)
                            # Publish to text mode FIRST (before TTS starts)
                            await publisher.publish_agent_message(response)
                            speech_handle = session.say(response, add_to_chat_ctx=False)
                            await speech_handle
                            # Reset text mode flag since we bypassed evaluate_answer
                            agent.set_text_input_mode(False)
                        else:
                            # It's an answer - send to LLM for evaluation with timeout
                            speech_handle = session.generate_reply(user_input=text)
                            await asyncio.wait_for(speech_handle, timeout=15.0)

                    except TimeoutError:
                        logger.error(f"LLM response timed out for input: {text[:50]}...")
                        # Notify user of timeout (ignore errors if room is gone)
                        with contextlib.suppress(Exception):
                            await ctx.room.local_participant.publish_data(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": "Response took too long. Please try again.",
                                    }
                                ).encode("utf-8"),
                                reliable=True,
                                topic="agent-response",
                            )
                    except asyncio.CancelledError:
                        logger.info("Text input handling cancelled (room disconnect)")
                        raise  # Re-raise to allow proper cleanup
                    except Exception as e:
                        logger.error(f"Error processing text input: {e}")
                        # Fallback to LLM processing - still mark as text input
                        agent.set_text_input_mode(True)
                        try:
                            speech_handle = session.generate_reply(user_input=text)
                            await asyncio.wait_for(speech_handle, timeout=15.0)
                        except TimeoutError:
                            logger.error("Fallback LLM response also timed out")
                        except asyncio.CancelledError:
                            raise

                create_tracked_task(handle_text_input(), "handle_text_input")

            elif msg_type == "user_question":
                # Question mode: answer educational questions about the current card
                # Does NOT trigger evaluation or affect rating
                # NOTE: Bypasses LLM entirely to prevent tool calls
                question = payload.get("text", "")
                logger.info(
                    "User question (question mode)",
                    extra={
                        "room_name": ctx.room.name,
                        "question": question,
                    },
                )

                async def handle_question():
                    try:
                        import google.generativeai as genai

                        from src.domain.services.card_sanitizer import sanitize_for_tts

                        # Interrupt any ongoing agent speech first (like HINT/GIVE_UP handlers)
                        session.interrupt()

                        # Get current card context for the question
                        card = agent.get_current_card()
                        if not card:
                            no_card_msg = "I don't have a card loaded to answer questions about."
                            speech_handle = session.say(no_card_msg, add_to_chat_ctx=False)
                            await publisher.publish_agent_message(no_card_msg)
                            await speech_handle
                            return

                        # Mark as text input to suppress echo
                        agent.set_text_input_mode(True)

                        # Build LLM-generated educational response
                        # The user already sees the answer on screen - add NEW value, don't repeat it
                        card_front = sanitize_for_tts(card["front"])
                        card_back = sanitize_for_tts(card["back"])
                        question_lower = question.lower()

                        # Get conversation context for follow-up questions
                        previous_hints = agent.get_previous_hints()
                        question_history = agent.get_question_history()
                        user_attempts = agent.get_user_attempts()
                        socratic_context = agent.get_socratic_context()

                        # Build conversation context string
                        conv_context = ""

                        # Add user's answer attempts
                        if user_attempts:
                            conv_context += "\n<user_attempts>\n"
                            for attempt in user_attempts[-2:]:
                                conv_context += f'- "{attempt}"\n'
                            conv_context += "</user_attempts>\n"

                        # Add socratic exchanges
                        if socratic_context:
                            conv_context += "\n<socratic_discussion>\n"
                            for turn in socratic_context[-4:]:
                                conv_context += f"{turn}\n"
                            conv_context += "</socratic_discussion>\n"

                        if previous_hints:
                            conv_context += "\n<previous_hints>\n"
                            for h in previous_hints[-3:]:  # Last 3 hints
                                conv_context += f"- {h}\n"
                            conv_context += "</previous_hints>\n"
                        if question_history:
                            conv_context += "\n<conversation>\n"
                            for qa in question_history[-3:]:  # Last 3 Q&A
                                conv_context += f"User: {qa['q']}\nAssistant: {qa['a']}\n"
                            conv_context += "</conversation>\n"

                        if "explain" in question_lower or "more detail" in question_lower:
                            prompt = f"""Context (user already sees this):
Q: {card_front}
A: {card_back}
{conv_context}
TASK: Give ONE insight they WON'T find in the answer above.

Rules:
- DO NOT summarize or rephrase the answer
- Share the "aha moment" or mental model behind this concept
- If there's conversation history, build on previous responses
- 2 sentences max
- Start directly with the insight"""

                        elif "example" in question_lower:
                            prompt = f"""Context (user already sees this):
Q: {card_front}
A: {card_back}
{conv_context}
TASK: One SPECIFIC real-world scenario (not mentioned in the answer).

Rules:
- Use concrete names/situations (e.g., "When Netflix migrated...")
- Show cause and effect
- If there's conversation history, build on or connect to previous examples
- 2-3 sentences max
- Start directly with the example"""

                        elif "why" in question_lower or "important" in question_lower:
                            prompt = f"""Context (user already sees this):
Q: {card_front}
A: {card_back}
{conv_context}
TASK: Why does this matter? What breaks without it?

Rules:
- Focus on consequences, not definitions
- Be specific (name a real problem it prevents)
- If there's conversation history, connect to previous discussion
- 2 sentences max
- Start directly"""

                        else:
                            prompt = f"""Context (user already sees this):
Q: {card_front}
A: {card_back}
{conv_context}
User asks: {question}

Rules:
- Answer their specific question directly
- CRITICAL: If user references something from conversation history, use that context
- Add value they can't get from the card
- 2-3 sentences max
- Start directly with your answer"""

                        # Direct Gemini API call (bypasses LiveKit agent tools)
                        genai.configure(
                            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
                        )
                        model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))

                        response = await model.generate_content_async(prompt)
                        response_text = response.text.strip()

                        # Track Q&A for conversation context
                        agent.add_question_exchange(question, response_text)

                        # Use session.say() to speak the generated response
                        speech_handle = session.say(response_text, add_to_chat_ctx=False)
                        await publisher.publish_agent_message(response_text)
                        await speech_handle
                    except Exception as e:
                        logger.error(f"Error handling question: {e}")
                        # Graceful fallback to template-based response
                        try:
                            card = agent.get_current_card()
                            if card:
                                from src.domain.services.card_sanitizer import sanitize_for_tts

                                card_back = sanitize_for_tts(card["back"])
                                fallback_msg = f"Let me share the key point: {card_back[:100]}"
                            else:
                                fallback_msg = (
                                    "I had trouble answering that. Could you try rephrasing?"
                                )
                            speech_handle = session.say(fallback_msg, add_to_chat_ctx=False)
                            await publisher.publish_agent_message(fallback_msg)
                            await speech_handle
                        except Exception as fallback_err:
                            logger.error(f"Fallback also failed: {fallback_err}")
                            error_msg = "I had trouble answering that. Could you try rephrasing?"
                            speech_handle = session.say(error_msg, add_to_chat_ctx=False)
                            await publisher.publish_agent_message(error_msg)
                            await speech_handle

                create_tracked_task(handle_question(), "handle_question")

            # PTT (Push-to-Talk) handlers
            elif msg_type == "ptt_start":
                logger.info("PTT: Start recording")

                async def handle_ptt_start():
                    nonlocal _ptt_recording, _ptt_transcript_buffer
                    # Clear transcript buffer for new recording
                    _ptt_transcript_buffer = []
                    _ptt_recording = True
                    # Stop any current agent speech
                    session.interrupt()
                    # Clear any previous buffered audio
                    session.clear_user_turn()
                    # Start listening to user audio
                    session.input.set_audio_enabled(True)
                    # Acknowledge to frontend
                    await ctx.room.local_participant.publish_data(
                        json.dumps({"type": "ptt_state", "recording": True}).encode("utf-8"),
                        reliable=True,
                        topic="agent-response",
                    )

                create_tracked_task(handle_ptt_start(), "handle_ptt_start")

            elif msg_type == "ptt_end":
                logger.info("PTT: End recording, processing...")

                async def handle_ptt_end():
                    nonlocal _ptt_recording, _ptt_transcript_buffer
                    # Stop listening but keep _ptt_recording True to capture final transcripts
                    session.input.set_audio_enabled(False)

                    # Wait for STT to finalize (transcripts can arrive 400-500ms after audio stops)
                    # This allows us to capture the final transcript for question detection
                    await asyncio.sleep(0.5)  # 500ms wait for transcript

                    _ptt_recording = False

                    # Get accumulated transcript for question detection
                    accumulated_text = " ".join(_ptt_transcript_buffer).strip()
                    logger.info(f"PTT accumulated transcript: '{accumulated_text[:80]}...' (len={len(accumulated_text)})")

                    # Check if user asked a follow-up question
                    if accumulated_text and is_question(accumulated_text):
                        logger.info(f"PTT detected as question, routing to fast handler")
                        # Clear the audio buffer (don't send to evaluation)
                        session.clear_user_turn()
                        # Route to question handler (same as text question)
                        await handle_voice_question(accumulated_text)
                    else:
                        # Normal answer flow - process via evaluation
                        # Reduced timeouts: PTT has explicit end signal, so we don't need long waits
                        # 3s transcript timeout is generous (STT typically finalizes in ~200ms)
                        # 0.5s flush is minimal padding (PTT button release is clear endpoint)
                        session.commit_user_turn(
                            transcript_timeout=3.0,  # Reduced from 10.0 - faster response
                            stt_flush_duration=0.5,  # Reduced from 2.0 - PTT has clear end
                        )

                    # Clear buffer
                    _ptt_transcript_buffer = []

                    # Acknowledge to frontend
                    await ctx.room.local_participant.publish_data(
                        json.dumps({"type": "ptt_state", "recording": False}).encode("utf-8"),
                        reliable=True,
                        topic="agent-response",
                    )

                create_tracked_task(handle_ptt_end(), "handle_ptt_end")

            elif msg_type == "ptt_cancel":
                logger.info("PTT: Cancelled")

                async def handle_ptt_cancel():
                    # Stop listening
                    session.input.set_audio_enabled(False)
                    # Discard the recorded audio
                    session.clear_user_turn()
                    # Acknowledge to frontend
                    await ctx.room.local_participant.publish_data(
                        json.dumps({"type": "ptt_state", "recording": False}).encode("utf-8"),
                        reliable=True,
                        topic="agent-response",
                    )

                create_tracked_task(handle_ptt_cancel(), "handle_ptt_cancel")

            # Hint button handler - request hint without evaluation
            # Uses session.say() to bypass LLM and speak directly
            elif msg_type == "hint":
                logger.info("Hint button pressed")

                async def handle_hint():
                    try:
                        from src.domain.services.command_parser import CommandType

                        # Interrupt any ongoing agent speech first
                        session.interrupt()
                        # Mark as text mode to suppress echo
                        agent.set_text_input_mode(True)
                        # Handle hint command via orchestrator
                        response = await agent._handle_command(None, CommandType.HINT)
                        # Use session.say() to speak directly (bypasses LLM)
                        speech_handle = session.say(response, add_to_chat_ctx=False)
                        # Manually publish the hint message (add_to_chat_ctx=False skips auto-publish)
                        await publisher.publish_agent_message(response)
                        await speech_handle
                    except Exception as e:
                        logger.error(f"Error handling hint: {e}")
                        error_msg = "I couldn't provide a hint right now."
                        speech_handle = session.say(error_msg, add_to_chat_ctx=False)
                        await publisher.publish_agent_message(error_msg)
                        await speech_handle

                create_tracked_task(handle_hint(), "handle_hint")

            # Give up button handler - show answer and mark as Again
            # Uses session.say() to bypass LLM and speak directly
            elif msg_type == "give_up":
                logger.info("Give up button pressed")

                async def handle_give_up():
                    try:
                        from src.domain.services.command_parser import CommandType

                        # Interrupt any ongoing agent speech first
                        session.interrupt()
                        # Mark as text mode to suppress echo
                        agent.set_text_input_mode(True)
                        # Handle give_up command via orchestrator
                        response = await agent._handle_command(None, CommandType.GIVE_UP)
                        # Use session.say() to speak directly (bypasses LLM)
                        speech_handle = session.say(response, add_to_chat_ctx=False)
                        # Manually publish the response (add_to_chat_ctx=False skips auto-publish)
                        await publisher.publish_agent_message(response)
                        await speech_handle
                    except Exception as e:
                        logger.error(f"Error handling give up: {e}")
                        error_msg = "I couldn't process your request right now."
                        speech_handle = session.say(error_msg, add_to_chat_ctx=False)
                        await publisher.publish_agent_message(error_msg)
                        await speech_handle

                create_tracked_task(handle_give_up(), "handle_give_up")

            # Mnemonic button handler - generate memory aid for current card
            # Uses direct Gemini API call to avoid triggering evaluate_answer tool
            elif msg_type == "mnemonic_request":
                logger.info("Mnemonic button pressed")

                async def handle_mnemonic():
                    try:
                        import google.generativeai as genai

                        from src.domain.services.card_sanitizer import sanitize_for_tts

                        # Interrupt any ongoing agent speech first
                        session.interrupt()

                        card = agent.get_current_card()
                        if not card:
                            no_card_msg = "I don't have a card loaded to create a mnemonic for."
                            speech_handle = session.say(no_card_msg, add_to_chat_ctx=False)
                            await publisher.publish_agent_message(no_card_msg)
                            await speech_handle
                            return

                        card_front = sanitize_for_tts(card["front"])
                        card_back = sanitize_for_tts(card["back"])

                        # Direct Gemini API call (bypasses LiveKit agent tools)
                        genai.configure(
                            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
                        )
                        model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))

                        prompt = f"""Generate a memorable memory aid for this flashcard:

Question: {card_front}
Answer: {card_back}

Create ONE of these (choose the most effective):
- Acronym using first letters
- Vivid mental image or story
- Rhyme or rhythm
- Association with something familiar
- Analogy to everyday life

Keep it concise (1-2 sentences), memorable, and speak it naturally.
Tone: Warm tutor helping a student remember.
Just output the mnemonic directly, nothing else."""

                        response = await model.generate_content_async(prompt)
                        mnemonic_text = response.text.strip()

                        # Use session.say() to speak the generated mnemonic
                        speech_handle = session.say(mnemonic_text, add_to_chat_ctx=False)
                        await publisher.publish_agent_message(mnemonic_text)
                        await speech_handle
                    except Exception as e:
                        logger.error(f"Error handling mnemonic: {e}")
                        error_msg = "I couldn't generate a mnemonic right now. Let me try again."
                        speech_handle = session.say(error_msg, add_to_chat_ctx=False)
                        await publisher.publish_agent_message(error_msg)
                        await speech_handle

                create_tracked_task(handle_mnemonic(), "handle_mnemonic")

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to parse data packet: {e}")

    # Notify frontend of agent speaking state and publish text simultaneously with speech
    @session.on("speech_created")
    def on_speech_created(event):
        """Publish agent message and notify frontend when agent starts speaking."""
        speech_handle = event.speech_handle

        # Get the text content from the speech source
        # This allows text to appear in UI simultaneously with TTS
        text_content = None
        if hasattr(speech_handle, 'source') and speech_handle.source:
            source = speech_handle.source
            # Source can be a string or have text_content attribute
            if isinstance(source, str):
                text_content = source
            elif hasattr(source, 'text_content'):
                text_content = source.text_content
            elif hasattr(source, 'text'):
                text_content = source.text

        logger.info(
            "Agent speech created",
            extra={
                "room_name": ctx.room.name,
                "agent_id": ctx.room.local_participant.identity,
                "has_text": text_content is not None,
            },
        )

        async def track_speaking_state():
            try:
                # Publish agent message immediately when speech starts (not when it ends)
                if text_content:
                    await publisher.publish_agent_message(text_content)

                # Notify frontend that agent started speaking
                await ctx.room.local_participant.publish_data(
                    json.dumps({"type": "agent_speaking_state", "speaking": True}).encode(),
                    reliable=True,
                    topic="agent-response",
                )

                # Wait for speech to complete
                await speech_handle

                # Notify frontend that agent stopped speaking
                await ctx.room.local_participant.publish_data(
                    json.dumps({"type": "agent_speaking_state", "speaking": False}).encode(),
                    reliable=True,
                    topic="agent-response",
                )

            except Exception as e:
                logger.error(f"Error tracking speaking state: {type(e).__name__}: {e}")

        create_tracked_task(track_speaking_state(), "track_speaking_state")

    # Publish user and agent messages to text panel
    # This captures ALL agent responses (including follow-up questions that bypass evaluate_answer)
    # For assistant messages, skip if already published by uncons_agent.publish_agent_message()
    @session.on("conversation_item_added")
    def on_conversation_item_added(event):
        """Publish user and agent messages to text panel."""
        text = event.item.text_content
        if not text:
            return

        if event.item.role == "user":
            # Publish complete user transcript (not per-segment like user_input_transcribed)
            logger.info(f"User message committed: {text[:50]}...")
            create_tracked_task(
                ctx.room.local_participant.publish_data(
                    json.dumps(
                        {"type": "user_transcript", "text": text, "source": "voice"}
                    ).encode("utf-8"),
                    reliable=True,
                    topic="agent-response",
                ),
                "publish_user_transcript"
            )
        elif event.item.role == "assistant":
            # Check if already published by uncons_agent (prevents duplicates)
            # Hash only first 30 chars - SDK heavily truncates text in this event
            prefix = text[:30]
            text_hash = hashlib.sha256(prefix.encode()).hexdigest()[:16]
            logger.info(f"Checking dedup: hash={text_hash}, prefix='{prefix}'")
            if text_hash in _published_text_hashes:
                if time_module.time() - _published_text_hashes[text_hash] < 30.0:
                    logger.info(f"Skipping duplicate assistant message (hash={text_hash[:8]})")
                    return
                # Clean up old entry
                del _published_text_hashes[text_hash]

            # Publish agent response (only for messages not already published)
            logger.info(f"Agent response captured: {text[:50]}...")
            create_tracked_task(publisher.publish_agent_message(text), "publish_agent_response")

    # Check if frontend already started a session
    # NOTE: We query the recovery store (SQLite) because API and worker are separate processes
    # The API saves session to SQLite, worker reads it from there
    # Use 300 seconds (5 min) to allow time for deck selection and configuration
    session_record = await recovery_store.get_active_session(max_age_seconds=300)
    if session_record:
        logger.info(
            f"Found active session in recovery store: {session_record.id}, deck={session_record.deck_name}"
        )

        # Fetch cards from Anki (new, learning, and due)
        try:
            if session_record.deck_name == "All":
                # Parallel fetch for all decks
                decks = await anki_adapter.get_decks()
                semaphore = asyncio.Semaphore(10)

                async def fetch_deck(deck: str):
                    async with semaphore:
                        return await anki_adapter.get_reviewable_cards(deck)

                results = await asyncio.gather(
                    *[fetch_deck(d) for d in decks],
                    return_exceptions=True,
                )
                all_cards = []
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.warning(f"Failed to fetch cards from deck {decks[i]}: {result}")
                    else:
                        all_cards.extend(result)
                fetched_cards = all_cards
            else:
                fetched_cards = await anki_adapter.get_reviewable_cards(session_record.deck_name)

            logger.info(f"Fetched {len(fetched_cards)} cards from Anki")

            # Initialize orchestrator with cards
            cards = [card.to_dict() for card in fetched_cards]
            orchestrator.create_session(
                session_id=session_record.id,
                deck_name=session_record.deck_name,
                cards=cards,
            )
            agent._session_id = session_record.id

            # CRITICAL: Restore session in session_manager so record_rating works
            # API and worker are separate processes - API set _active_session, but worker's is None
            session_manager.restore_session(
                session_id=session_record.id,
                deck_name=session_record.deck_name,
                cards=fetched_cards,
            )
            logger.info(f"Session restored in session_manager: {session_record.id}")

            # Present first card immediately (no greeting)
            first_card = orchestrator.get_current_card()
            if first_card:
                logger.info(f"Presenting first card: {first_card['front'][:50]}...")
                # Sanitize card content for TTS (strip HTML, hide cloze answers, etc.)
                card_text = sanitize_question_for_tts(first_card["front"])
                # Send card update to frontend so UI displays the first card
                await agent.publish_card_update(first_card)

                # Publish to text mode FIRST (before TTS starts) so text appears immediately
                await publisher.publish_agent_message(card_text)

                # Speak the question directly using session.say() (bypasses LLM)
                # This is more reliable than generate_reply() which can trigger tool calls
                speech_handle = session.say(card_text, add_to_chat_ctx=False)
                try:
                    # Dynamic timeout based on text length (long cards need more time)
                    timeout = calculate_tts_timeout(card_text)
                    await asyncio.wait_for(speech_handle, timeout=timeout)
                    logger.info(f"First card presented (timeout={timeout:.1f}s)")
                except TimeoutError:
                    logger.warning(f"TTS timed out presenting first card (timeout={timeout:.1f}s)")
            else:
                logger.info("No cards due for review")
                speech_handle = session.generate_reply(
                    instructions="Let the user know there are no cards due for review right now."
                )
                try:
                    await asyncio.wait_for(speech_handle, timeout=10.0)
                except TimeoutError:
                    logger.warning("TTS timed out for no-cards message")

        except Exception as e:
            logger.error(f"Failed to fetch cards from Anki: {e}")
            speech_handle = session.generate_reply(
                instructions="There was an error loading your flashcards. Please try again."
            )
            try:
                await asyncio.wait_for(speech_handle, timeout=10.0)
            except TimeoutError:
                logger.warning("TTS timed out for error message")
    else:
        # No session - this shouldn't happen if frontend flow is correct
        # Wait for user input
        logger.warning("No active session found in recovery store, waiting for user input")


if __name__ == "__main__":
    agents.cli.run_app(server)
