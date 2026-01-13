"""
UNCONS Backend - FastAPI Application

Voice-first AI tutor for Anki flashcards.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (parent of backend/)
# Must happen before importing modules that use environment variables
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from src.api.dependencies import cleanup_dependencies, init_dependencies  # noqa: E402
from src.api.routes import (  # noqa: E402
    cards_router,
    decks_router,
    livekit_router,
    session_router,
    worker_router,
)
from src.config import (  # noqa: E402
    CORS_ALLOWED_HEADERS,
    CORS_ALLOWED_METHODS,
    get_cors_allow_credentials,
    get_cors_origins,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan manager for startup/shutdown events.

    Startup:
    - Initialize singleton dependencies
    - Reset stale processing markers

    Shutdown:
    - Force-end active sessions (sync ratings)
    - Close HTTP connections
    """
    logger.info("Starting UNCONS backend...")

    # Initialize dependencies
    await init_dependencies()
    logger.info("Dependencies initialized")

    yield

    # Cleanup on shutdown
    logger.info("Shutting down UNCONS backend...")
    await cleanup_dependencies()
    logger.info("Shutdown complete")


app = FastAPI(
    title="UNCONS API",
    description="Voice-first AI tutor for Anki flashcards",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS configuration - loaded from environment with restrictive defaults
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=get_cors_allow_credentials(),
    allow_methods=CORS_ALLOWED_METHODS,
    allow_headers=CORS_ALLOWED_HEADERS,
)

# Register API routers
app.include_router(session_router)
app.include_router(decks_router)
app.include_router(cards_router)
app.include_router(livekit_router)
app.include_router(worker_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "uncons-backend",
        "version": "0.1.0",
    }
