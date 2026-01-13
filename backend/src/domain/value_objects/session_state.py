"""Session state value object for session lifecycle management."""

from enum import StrEnum


class SessionState(StrEnum):
    """Session lifecycle states.

    State machine:
        IDLE -> SYNCING_START -> ACTIVE -> SYNCING_END -> COMPLETE
                      |                         |
                      v                         v
                  DEGRADED  <-  (sync fail) -  DEGRADED

    States:
        IDLE: No active session
        SYNCING_START: Pulling cards from Anki
        ACTIVE: Reviewing cards
        DEGRADED: Sync failed, using cached cards or ratings queued locally
        SYNCING_END: Pushing ratings to Anki
        COMPLETE: Session finished successfully
    """

    IDLE = "idle"
    SYNCING_START = "syncing_start"
    ACTIVE = "active"
    DEGRADED = "degraded"
    SYNCING_END = "syncing_end"
    COMPLETE = "complete"

    def is_active(self) -> bool:
        """Check if session is in an active review state."""
        return self in (SessionState.ACTIVE, SessionState.DEGRADED)

    def can_accept_ratings(self) -> bool:
        """Check if session can accept new ratings."""
        return self in (SessionState.ACTIVE, SessionState.DEGRADED)

    def is_terminal(self) -> bool:
        """Check if session is in a terminal state."""
        return self in (SessionState.COMPLETE, SessionState.IDLE)
