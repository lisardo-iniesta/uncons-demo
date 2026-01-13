"""Worker health check API routes."""

import logging
import os

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/worker", tags=["worker"])


class WorkerHealthResponse(BaseModel):
    """Response for worker health check."""

    status: str
    pid: int | None = None
    memory_mb: float | None = None
    cpu_percent: float | None = None
    message: str | None = None


class ErrorResponse(BaseModel):
    """Error response wrapper."""

    error: dict


@router.get(
    "/health",
    response_model=WorkerHealthResponse,
    responses={
        503: {"model": ErrorResponse, "description": "Worker not found or unhealthy"},
        403: {"model": ErrorResponse, "description": "Not available in production"},
    },
)
async def worker_health() -> WorkerHealthResponse:
    """Check worker process health (DEV ONLY).

    Returns worker process info including PID, memory usage, and CPU percent.
    This endpoint is only available in development mode.
    """
    if os.getenv("ENVIRONMENT", "development") == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not available in production",
                }
            },
        )

    try:
        import psutil
    except ImportError:
        # psutil not installed, return basic health
        return WorkerHealthResponse(
            status="unknown",
            message="psutil not installed, cannot check worker process",
        )

    # Look for worker process
    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "cpu_percent"]):
        try:
            cmdline = proc.info.get("cmdline", [])
            if cmdline and "worker.py" in " ".join(cmdline):
                memory_info = proc.info.get("memory_info")
                memory_mb = memory_info.rss / 1024 / 1024 if memory_info else None

                return WorkerHealthResponse(
                    status="healthy",
                    pid=proc.pid,
                    memory_mb=round(memory_mb, 1) if memory_mb else None,
                    cpu_percent=proc.cpu_percent(),
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # Worker not found
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": {
                "code": "WORKER_NOT_FOUND",
                "message": "Worker process not found",
            }
        },
    )
