"""FastAPI dependency injection module.

Provides singleton instances of services for API routes.
Uses lifespan events for initialization and cleanup.
"""

import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from src.adapters.anki_connect import AnkiConnectAdapter
from src.domain.services.session_manager import SessionManager
from src.domain.services.sync_orchestrator import SyncOrchestrator
from src.infrastructure.recovery_store import RecoveryStore
from src.ports.flashcard_service import FlashcardService

logger = logging.getLogger(__name__)


# Configuration
def get_anki_url() -> str:
    """Get AnkiConnect URL from environment.

    Default is localhost:8765 for local development.
    In Docker, set ANKI_CONNECT_URL=http://anki:8765
    """
    return os.getenv("ANKI_CONNECT_URL", "http://localhost:8765")


def get_recovery_db_path() -> str:
    """Get recovery database path from environment."""
    default_path = str(Path.home() / ".uncons" / "recovery.db")
    return os.getenv("RECOVERY_DB_PATH", default_path)


def get_flashcard_adapter_type() -> str:
    """Get flashcard adapter type from environment.

    Options:
        - 'anki': Use AnkiConnect (default, requires Anki running)
        - 'local': Use in-memory adapter with test cards (no Anki required)
    """
    return os.getenv("FLASHCARD_ADAPTER", "anki").lower()


# Singletons stored at module level
_recovery_store: RecoveryStore | None = None
_flashcard_service: FlashcardService | None = None
_session_manager: SessionManager | None = None
_sync_orchestrator: SyncOrchestrator | None = None


async def init_dependencies() -> None:
    """Initialize all singleton dependencies.

    Called during FastAPI lifespan startup.
    """
    global _recovery_store, _flashcard_service, _session_manager, _sync_orchestrator

    # Initialize recovery store (async-safe)
    db_path = get_recovery_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _recovery_store = RecoveryStore(db_path)
    await _recovery_store.initialize()

    # Initialize flashcard adapter based on configuration
    adapter_type = get_flashcard_adapter_type()

    if adapter_type == "local":
        from src.adapters.local_test_deck import LocalTestDeckAdapter

        print("[API] Using LOCAL test deck adapter (no Anki required)")
        logger.info("Using local test deck adapter")
        _flashcard_service = LocalTestDeckAdapter()
    elif adapter_type == "anki":
        anki_url = get_anki_url()
        print(f"[API] Connecting to AnkiConnect: {anki_url}")
        _flashcard_service = AnkiConnectAdapter(url=anki_url)

        # Wait for Anki to be available (with retries)
        connected = await _flashcard_service.wait_for_connection()
        if connected:
            print(f"[API] AnkiConnect ready: {anki_url}")
            logger.info(f"AnkiConnect ready for API: {anki_url}")
        else:
            print("[API] AnkiConnect not available at startup - will retry on requests")
            logger.warning("AnkiConnect not available at startup - will retry on requests")
    else:
        raise ValueError(
            f"Invalid FLASHCARD_ADAPTER: '{adapter_type}'. " "Valid options: 'anki', 'local'"
        )

    # Initialize sync orchestrator
    _sync_orchestrator = SyncOrchestrator(
        flashcard_service=_flashcard_service,
        recovery_store=_recovery_store,
    )

    # Initialize session manager
    # Use shorter timeout in dev for easier testing
    is_dev = os.getenv("ENVIRONMENT", "development") != "production"
    timeout_minutes = 5 if is_dev else 30

    _session_manager = SessionManager(
        flashcard_service=_flashcard_service,
        recovery_store=_recovery_store,
        timeout_minutes=timeout_minutes,
    )

    # Reset any stale processing markers from crash
    await _recovery_store.reset_stale_processing()


async def cleanup_dependencies() -> None:
    """Cleanup dependencies on shutdown.

    Called during FastAPI lifespan shutdown.
    Syncs active sessions and closes connections.
    """
    global _session_manager, _flashcard_service

    # Force-end active sessions
    if _session_manager is not None:
        await _session_manager.force_end_all_sessions()

    # Close HTTP client
    if _flashcard_service is not None and hasattr(_flashcard_service, "close"):
        await _flashcard_service.close()


def get_recovery_store() -> RecoveryStore:
    """Dependency: Get RecoveryStore instance."""
    if _recovery_store is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    return _recovery_store


def get_flashcard_service() -> FlashcardService:
    """Dependency: Get FlashcardService instance."""
    if _flashcard_service is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    return _flashcard_service


def get_session_manager() -> SessionManager:
    """Dependency: Get SessionManager instance."""
    if _session_manager is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    return _session_manager


def get_sync_orchestrator() -> SyncOrchestrator:
    """Dependency: Get SyncOrchestrator instance."""
    if _sync_orchestrator is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    return _sync_orchestrator


# Type aliases for dependency injection
RecoveryStoreDep = Annotated[RecoveryStore, Depends(get_recovery_store)]
FlashcardServiceDep = Annotated[FlashcardService, Depends(get_flashcard_service)]
SessionManagerDep = Annotated[SessionManager, Depends(get_session_manager)]
SyncOrchestratorDep = Annotated[SyncOrchestrator, Depends(get_sync_orchestrator)]


# =============================================================================
# In-Memory Rate Limiter
# =============================================================================


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    max_requests: int
    window_seconds: int


# Per-endpoint rate limits (relaxed for development)
RATE_LIMITS: dict[str, RateLimitConfig] = {
    "/api/session/start": RateLimitConfig(max_requests=30, window_seconds=60),
    "/api/session/end": RateLimitConfig(max_requests=30, window_seconds=60),
    "/api/cards/{card_id}/rate": RateLimitConfig(max_requests=120, window_seconds=60),
    "/api/decks": RateLimitConfig(max_requests=60, window_seconds=60),
}


class InMemoryRateLimiter:
    """Simple in-memory rate limiter using sliding window.

    For MVP only - not suitable for multi-process deployments.
    Uses IP address as client identifier.
    """

    def __init__(self) -> None:
        # requests[endpoint][client_ip] = list of timestamps
        self._requests: dict[str, dict[str, list[datetime]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def _cleanup_old_requests(self, endpoint: str, client_ip: str, window_seconds: int) -> None:
        """Remove requests outside the sliding window."""
        now = datetime.now(UTC)
        cutoff = now.timestamp() - window_seconds
        self._requests[endpoint][client_ip] = [
            ts for ts in self._requests[endpoint][client_ip] if ts.timestamp() > cutoff
        ]

    def is_allowed(self, endpoint: str, client_ip: str) -> bool:
        """Check if request is allowed under rate limit.

        Args:
            endpoint: Route path pattern
            client_ip: Client IP address

        Returns:
            True if request is allowed
        """
        config = RATE_LIMITS.get(endpoint)
        if config is None:
            # No rate limit configured
            return True

        self._cleanup_old_requests(endpoint, client_ip, config.window_seconds)

        current_count = len(self._requests[endpoint][client_ip])
        return current_count < config.max_requests

    def record_request(self, endpoint: str, client_ip: str) -> None:
        """Record a request for rate limiting."""
        self._requests[endpoint][client_ip].append(datetime.now(UTC))

    def get_remaining(self, endpoint: str, client_ip: str) -> int:
        """Get remaining requests in current window."""
        config = RATE_LIMITS.get(endpoint)
        if config is None:
            return -1  # Unlimited

        self._cleanup_old_requests(endpoint, client_ip, config.window_seconds)
        current_count = len(self._requests[endpoint][client_ip])
        return max(0, config.max_requests - current_count)


# Singleton rate limiter
_rate_limiter = InMemoryRateLimiter()


def get_rate_limiter() -> InMemoryRateLimiter:
    """Dependency: Get rate limiter instance."""
    return _rate_limiter


RateLimiterDep = Annotated[InMemoryRateLimiter, Depends(get_rate_limiter)]


def rate_limit(endpoint: str):
    """Dependency factory: Rate limit check for endpoint.

    Usage:
        @router.post("/api/session/start")
        async def start_session(
            _: Annotated[None, Depends(rate_limit("/api/session/start"))],
            ...
        ):

    Raises:
        HTTPException 429 if rate limit exceeded
    """

    async def check_rate_limit(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"

        if not _rate_limiter.is_allowed(endpoint, client_ip):
            config = RATE_LIMITS.get(endpoint)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": f"Too many requests. Limit: {config.max_requests}/{config.window_seconds}s",
                    }
                },
            )

        _rate_limiter.record_request(endpoint, client_ip)

    return check_rate_limit
