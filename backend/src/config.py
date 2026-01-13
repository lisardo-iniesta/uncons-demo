"""Application configuration loaded from environment variables.

Provides type-safe access to configuration with sensible defaults.
Production defaults are restrictive for security.
"""

import os


def get_cors_origins() -> list[str]:
    """Get allowed CORS origins from environment.

    Environment variable: CORS_ORIGINS (comma-separated)
    Default: localhost ports 3000-3005 for development
    """
    default_origins = "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003,http://localhost:3004,http://localhost:3005"
    origins_str = os.getenv("CORS_ORIGINS", default_origins)
    return [origin.strip() for origin in origins_str.split(",") if origin.strip()]


def get_cors_allow_credentials() -> bool:
    """Get CORS allow_credentials setting.

    Environment variable: CORS_ALLOW_CREDENTIALS
    Default: true for development (needed for cookies/auth)
    """
    return os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"


# Restricted HTTP methods - only what the API actually uses
CORS_ALLOWED_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]

# Restricted headers - only what's needed for the API
CORS_ALLOWED_HEADERS = [
    "Accept",
    "Accept-Language",
    "Content-Type",
    "Authorization",
    "X-Requested-With",
]


def is_production() -> bool:
    """Check if running in production environment."""
    return os.getenv("ENVIRONMENT", "development").lower() == "production"


def get_livekit_url() -> str:
    """Get LiveKit server URL.

    Environment variable: LIVEKIT_URL
    Default: ws://localhost:7880 for development
    """
    return os.getenv("LIVEKIT_URL", "ws://localhost:7880")


def get_livekit_api_key() -> str:
    """Get LiveKit API key.

    Environment variable: LIVEKIT_API_KEY
    Required in production.
    """
    return os.getenv("LIVEKIT_API_KEY", "")


def get_livekit_api_secret() -> str:
    """Get LiveKit API secret.

    Environment variable: LIVEKIT_API_SECRET
    Required in production.
    """
    return os.getenv("LIVEKIT_API_SECRET", "")
