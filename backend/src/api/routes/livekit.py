"""LiveKit token generation API route."""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException, status
from livekit.api import AccessToken, LiveKitAPI, VideoGrants
from livekit.api.agent_dispatch_service import CreateAgentDispatchRequest
from pydantic import BaseModel

from src.config import get_livekit_api_key, get_livekit_api_secret, get_livekit_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/livekit", tags=["livekit"])

# Track rooms that already have agents dispatched (prevents duplicate agents within short window)
# Each entry stores (room_name, timestamp) for time-based expiry
_dispatched_rooms: dict[str, float] = {}

# Lock to protect the dispatch cache from race conditions
_dispatch_lock = asyncio.Lock()

# Rooms expire after 5 minutes (sessions rarely last longer without reconnection)
_ROOM_EXPIRY_SECONDS = 300


def _cleanup_expired_rooms() -> None:
    """Remove rooms that have expired from the dispatch cache."""
    now = time.time()
    expired = [room for room, ts in _dispatched_rooms.items() if now - ts > _ROOM_EXPIRY_SECONDS]
    for room in expired:
        del _dispatched_rooms[room]
        logger.debug(f"Expired room from dispatch cache: {room}")


def clear_room_dispatch(room_name: str) -> bool:
    """Clear a room from the dispatch cache (call when session ends).

    Returns True if room was in cache, False otherwise.
    """
    if room_name in _dispatched_rooms:
        del _dispatched_rooms[room_name]
        logger.debug(f"Cleared room from dispatch cache: {room_name}")
        return True
    return False


class TokenRequest(BaseModel):
    """Request body for LiveKit token generation."""

    room_name: str
    participant_name: str
    deck_name: str | None = None  # Deck selected by user (passed to agent via metadata)
    input_mode: str | None = None  # "vad" (default) or "push_to_talk"


class TokenResponse(BaseModel):
    """Response containing LiveKit access token."""

    token: str
    url: str


@router.post("/token", response_model=TokenResponse)
async def get_token(request: TokenRequest) -> TokenResponse:
    """Generate a LiveKit access token for joining a room.

    The token grants the participant permission to join the specified room.
    Also dispatches an UNCONS agent to the room.
    """
    logger.warning(
        f"[LIVEKIT] Token request: room={request.room_name}, participant={request.participant_name}, deck={request.deck_name}, input_mode={request.input_mode}"
    )
    api_key = get_livekit_api_key()
    api_secret = get_livekit_api_secret()
    livekit_url = get_livekit_url()

    if not api_key or not api_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "LIVEKIT_NOT_CONFIGURED",
                    "message": "LiveKit API credentials are not configured",
                }
            },
        )

    # Create access token with video grants (builder pattern)
    token = (
        AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(request.participant_name)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=request.room_name,
                can_publish=True,  # For audio
                can_publish_data=True,  # For text input data channel
            )
        )
    )

    # Dispatch an agent to the room (with lock to prevent race conditions)
    async with _dispatch_lock:
        # Cleanup expired rooms before checking
        _cleanup_expired_rooms()
        logger.info(f"[DISPATCH] Cache state: {list(_dispatched_rooms.keys())}")

        # Skip dispatch if room is already in cache (prevents duplicate agents)
        # The cache tracks rooms we've already dispatched to - trust it first
        if request.room_name in _dispatched_rooms:
            logger.info(
                f"[DISPATCH] Room {request.room_name} already in dispatch cache, skipping dispatch"
            )
            should_dispatch = False
        else:
            logger.info(f"[DISPATCH] Room {request.room_name} not in cache, will dispatch")
            should_dispatch = True

        if should_dispatch:
            try:
                # Convert wss:// to https:// for API calls
                api_url = livekit_url.replace("wss://", "https://").replace("ws://", "http://")
                lk_api = LiveKitAPI(api_url, api_key, api_secret)

                # Check if room already has an agent participant (defense in depth)
                try:
                    participants = await lk_api.room.list_participants(request.room_name)
                    logger.info(
                        f"[DISPATCH] Room participants: {[p.identity for p in participants]}"
                    )
                    agent_participants = [
                        p for p in participants if p.identity.startswith("agent-")
                    ]
                    if agent_participants:
                        logger.info(
                            f"[DISPATCH] Room {request.room_name} already has agent(s): {[p.identity for p in agent_participants]}, skipping dispatch"
                        )
                        should_dispatch = False
                except Exception as e:
                    # Room may not exist yet - that's fine, continue with dispatch
                    logger.info(
                        f"[DISPATCH] Room doesn't exist yet or error: {type(e).__name__}: {e}"
                    )

                if should_dispatch:
                    _dispatched_rooms[request.room_name] = time.time()  # Mark/update timestamp

                    # Pass deck name and input mode via dispatch metadata (not room metadata - room doesn't exist yet)
                    metadata_dict = {}
                    if request.deck_name:
                        metadata_dict["deck_name"] = request.deck_name
                    if request.input_mode:
                        metadata_dict["input_mode"] = request.input_mode
                    dispatch_metadata = json.dumps(metadata_dict) if metadata_dict else None
                    logger.info(
                        f"[DISPATCH] Metadata dict: {metadata_dict}, JSON: {dispatch_metadata}"
                    )

                    # Request agent dispatch to this room with metadata
                    logger.info(f"[DISPATCH] Calling create_dispatch for room: {request.room_name}")
                    await lk_api.agent_dispatch.create_dispatch(
                        CreateAgentDispatchRequest(
                            room=request.room_name,
                            metadata=dispatch_metadata,
                        )
                    )
                    logger.info(
                        f"[DISPATCH] Agent dispatched successfully to room: {request.room_name}"
                    )

                await lk_api.aclose()
            except Exception as e:
                # Remove from dict if dispatch failed so retry is possible
                _dispatched_rooms.pop(request.room_name, None)
                logger.warning(f"Agent dispatch failed: {type(e).__name__}: {e}")
        else:
            logger.debug(f"Agent already dispatched to room: {request.room_name}")

    return TokenResponse(
        token=token.to_jwt(),
        url=livekit_url,
    )
