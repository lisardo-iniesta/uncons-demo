"""API layer - FastAPI routes and dependencies."""

from .dependencies import (
    FlashcardServiceDep,
    InMemoryRateLimiter,
    RateLimiterDep,
    RecoveryStoreDep,
    SessionManagerDep,
    SyncOrchestratorDep,
    cleanup_dependencies,
    get_flashcard_service,
    get_rate_limiter,
    get_recovery_store,
    get_session_manager,
    get_sync_orchestrator,
    init_dependencies,
    rate_limit,
)
from .routes import cards_router, decks_router, session_router

__all__ = [
    # Routes
    "session_router",
    "decks_router",
    "cards_router",
    # Dependencies
    "init_dependencies",
    "cleanup_dependencies",
    "get_recovery_store",
    "get_flashcard_service",
    "get_session_manager",
    "get_sync_orchestrator",
    "get_rate_limiter",
    "rate_limit",
    # Type aliases
    "RecoveryStoreDep",
    "FlashcardServiceDep",
    "SessionManagerDep",
    "SyncOrchestratorDep",
    "RateLimiterDep",
    "InMemoryRateLimiter",
]
