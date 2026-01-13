"""Session manager service for review session lifecycle management."""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime

from src.domain.entities.card import Card
from src.domain.entities.session import PendingRating, Session
from src.domain.value_objects.rating import Rating
from src.domain.value_objects.session_state import SessionState
from src.infrastructure.recovery_store import RecoveryStore
from src.ports.flashcard_service import FlashcardService

logger = logging.getLogger(__name__)


class SessionConflictError(Exception):
    """Raised when attempting to start a session while one is already active."""

    def __init__(self, existing_session_id: str, started_at: datetime):
        self.existing_session_id = existing_session_id
        self.started_at = started_at
        super().__init__(f"Session {existing_session_id} already active")


class SessionNotFoundError(Exception):
    """Raised when no active session exists."""

    pass


class SessionExpiredError(Exception):
    """Raised when session has timed out."""

    pass


@dataclass
class StartSessionResult:
    """Result of starting a session."""

    session: Session
    recovered_ratings: int


@dataclass
class EndSessionResult:
    """Result of ending a session."""

    session_id: str
    state: SessionState
    stats: dict
    warning: str | None = None


class SessionManager:
    """Manages review session lifecycle.

    Responsibilities:
    - Single active session enforcement
    - Session state machine management
    - Coordination with FlashcardService and RecoveryStore
    - Timeout detection

    The SessionManager holds the active session in memory and
    persists session metadata to RecoveryStore for crash recovery.
    """

    def __init__(
        self,
        flashcard_service: FlashcardService,
        recovery_store: RecoveryStore,
        timeout_minutes: int = 30,
    ):
        """Initialize session manager.

        Args:
            flashcard_service: Port for Anki operations
            recovery_store: Persistence for crash recovery
            timeout_minutes: Session inactivity timeout
        """
        self._flashcard_service = flashcard_service
        self._recovery_store = recovery_store
        self._timeout_minutes = timeout_minutes
        self._active_session: Session | None = None

    @property
    def has_active_session(self) -> bool:
        """Check if there's an active session."""
        return self._active_session is not None

    def get_active_session_ids(self) -> list[str]:
        """Get IDs of all active sessions (used for cleanup operations)."""
        if self._active_session is not None:
            return [self._active_session.id]
        return []

    def get_active_session(self) -> Session | None:
        """Get the active session if one exists.

        Returns:
            Active session or None

        Raises:
            SessionExpiredError: If session has timed out
        """
        if self._active_session is None:
            return None

        # Check for timeout
        if self._active_session.is_timed_out(self._timeout_minutes):
            # Session timed out - it will be synced when client notices
            raise SessionExpiredError("Session has timed out due to inactivity")

        # Touch session to update last_activity
        self._active_session.touch()
        return self._active_session

    def restore_session(self, session_id: str, deck_name: str, cards: list[Card]) -> Session:
        """Restore a session from recovery store (for worker process).

        This is used when the worker process connects and finds an active session
        in the recovery store. Since API and worker are separate processes,
        the worker needs to reconstruct the in-memory session.

        Args:
            session_id: Session ID from recovery store
            deck_name: Deck name from recovery store
            cards: Cards fetched from Anki

        Returns:
            Restored Session entity
        """
        # Create session with the existing ID
        session = Session.create(deck_name, cards)
        # Override the generated ID with the one from recovery store
        # NOTE: Session is a dataclass with field named 'id', not '_id'
        session.id = session_id
        self._active_session = session
        return session

    async def start_session(self, deck_name: str) -> StartSessionResult:
        """Start a new review session.

        Args:
            deck_name: Name of deck to review (or "All" for all decks)

        Returns:
            StartSessionResult with session and recovered rating count

        Raises:
            SessionConflictError: If a session is already active
        """
        # Check for existing active session
        if self._active_session is not None:
            if self._active_session.is_timed_out(self._timeout_minutes):
                # Timed out session - end it silently
                await self._end_timed_out_session()
            else:
                raise SessionConflictError(
                    existing_session_id=self._active_session.id,
                    started_at=self._active_session.started_at,
                )

        # Recover pending ratings from previous sessions (background, no mention)
        recovered_count = await self._recover_pending_ratings()

        # Fetch cards from Anki (new, learning, and due)
        try:
            if deck_name == "All":
                # Get all reviewable cards across all decks (parallel fetch)
                decks = await self._flashcard_service.get_decks()
                semaphore = asyncio.Semaphore(10)

                async def fetch_deck(deck: str) -> list[Card]:
                    async with semaphore:
                        return await self._flashcard_service.get_reviewable_cards(deck)

                results = await asyncio.gather(
                    *[fetch_deck(d) for d in decks],
                    return_exceptions=True,
                )
                # Flatten results, skip errors
                all_cards: list[Card] = []
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.warning(f"Failed to fetch cards from deck {decks[i]}: {result}")
                    else:
                        all_cards.extend(result)
                cards = all_cards
            else:
                cards = await self._flashcard_service.get_reviewable_cards(deck_name)

            # Create session in ACTIVE state
            session = Session.create(deck_name, cards)

            # Persist session start to recovery store
            await self._recovery_store.save_session(
                session_id=session.id,
                deck_name=session.deck_name,
                state=session.state.value,
                started_at=session.started_at,
            )

            self._active_session = session
            return StartSessionResult(session=session, recovered_ratings=recovered_count)

        except Exception as e:
            # If sync fails, we could enter degraded mode with cached cards
            # For now, re-raise the error
            raise e

    async def end_session(self, session_id: str) -> EndSessionResult:
        """End the active session and sync ratings.

        Args:
            session_id: ID of session to end

        Returns:
            EndSessionResult with final stats

        Raises:
            SessionNotFoundError: If no matching session exists
        """
        if self._active_session is None:
            raise SessionNotFoundError("No active session")

        if self._active_session.id != session_id:
            raise SessionNotFoundError(f"Session {session_id} not found")

        session = self._active_session
        warning = None

        # Transition to syncing state (may already be in syncing or terminal state)
        with contextlib.suppress(ValueError):
            session.transition_to(SessionState.SYNCING_END)

        # Sync ratings to Anki
        synced_count = 0
        failed_count = 0

        for pending_rating in session.pending_ratings:
            if pending_rating.synced:
                synced_count += 1
                continue

            try:
                await self._flashcard_service.submit_review(
                    pending_rating.card_id, pending_rating.rating
                )
                pending_rating.synced = True
                synced_count += 1

                # Mark as synced in recovery store
                # (ratings are already saved during recording)
            except Exception:
                # Save failed rating to recovery store for later retry
                await self._recovery_store.save_review(
                    card_id=pending_rating.card_id,
                    ease=int(pending_rating.rating),
                    session_id=session.id,
                )
                failed_count += 1

        # Update stats
        stats = session.get_stats()
        stats["synced_count"] = synced_count
        stats["failed_count"] = failed_count

        # Determine final state
        if failed_count > 0:
            session.state = SessionState.DEGRADED
            warning = "Some ratings couldn't be synced. They'll be saved next time."
        else:
            session.state = SessionState.COMPLETE

        # Persist session end to recovery store
        await self._recovery_store.end_session(
            session_id=session.id,
            state=session.state.value,
            cards_reviewed=stats["cards_reviewed"],
            ratings_synced=synced_count,
            ratings_failed=failed_count,
        )

        # Clear active session
        self._active_session = None

        return EndSessionResult(
            session_id=session.id,
            state=session.state,
            stats=stats,
            warning=warning,
        )

    async def record_rating(
        self, session_id: str, card_id: int, rating: Rating
    ) -> tuple[Card | None, int]:
        """Record a rating for a card.

        Args:
            session_id: Active session ID
            card_id: Card being rated
            rating: User's rating

        Returns:
            Tuple of (next_card, remaining_count)

        Raises:
            SessionNotFoundError: If no matching session
            ValueError: If card_id doesn't match current card
        """
        session = self._get_session_or_raise(session_id)

        # Find the card in the session (don't require it to be "current")
        # The orchestrator manages card progression, session_manager just records ratings
        card_in_session = next((c for c in session.cards if c.id == card_id), None)
        if card_in_session is None:
            raise ValueError(f"Card ID {card_id} not found in session")

        # Record the rating directly (don't advance session index - orchestrator handles that)
        session.pending_ratings.append(PendingRating(card_id=card_id, rating=rating))
        session.touch()
        next_card = session.get_current_card()  # May be stale, but that's OK

        # Save to recovery store immediately (crash protection)
        await self._recovery_store.save_review(
            card_id=card_id,
            ease=int(rating),
            session_id=session_id,
        )

        return next_card, session.get_remaining_count()

    async def skip_card(self, session_id: str) -> tuple[Card | None, int]:
        """Skip current card and move it to end of queue.

        Args:
            session_id: Active session ID

        Returns:
            Tuple of (next_card, remaining_count)

        Raises:
            SessionNotFoundError: If no matching session
        """
        session = self._get_session_or_raise(session_id)
        next_card = session.skip_current_card()
        return next_card, session.get_remaining_count()

    def _get_session_or_raise(self, session_id: str) -> Session:
        """Get active session or raise error.

        Args:
            session_id: Expected session ID

        Returns:
            Active session

        Raises:
            SessionNotFoundError: If no matching session
            SessionExpiredError: If session has timed out
        """
        if self._active_session is None:
            raise SessionNotFoundError("No active session")

        if self._active_session.id != session_id:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if self._active_session.is_timed_out(self._timeout_minutes):
            raise SessionExpiredError("Session has timed out due to inactivity")

        self._active_session.touch()
        return self._active_session

    async def _recover_pending_ratings(self) -> int:
        """Recover and sync pending ratings from previous sessions.

        Returns:
            Number of ratings recovered and synced
        """
        pending = await self._recovery_store.get_pending_reviews()
        recovered = 0

        for review in pending:
            try:
                await self._flashcard_service.submit_review(review.card_id, Rating(review.ease))
                await self._recovery_store.mark_synced(review.id)
                recovered += 1
            except Exception:
                # Failed to sync - increment retry count, try next time
                await self._recovery_store.increment_retry(review.id)

        return recovered

    async def _end_timed_out_session(self) -> None:
        """End a timed-out session, syncing what we can."""
        if self._active_session is None:
            return

        session = self._active_session
        session.state = SessionState.DEGRADED

        # Try to sync ratings
        for pending_rating in session.pending_ratings:
            if pending_rating.synced:
                continue

            try:
                await self._flashcard_service.submit_review(
                    pending_rating.card_id, pending_rating.rating
                )
                pending_rating.synced = True
            except Exception:
                # Save to recovery store
                await self._recovery_store.save_review(
                    card_id=pending_rating.card_id,
                    ease=int(pending_rating.rating),
                    session_id=session.id,
                )

        # Persist end
        stats = session.get_stats()
        await self._recovery_store.end_session(
            session_id=session.id,
            state=SessionState.DEGRADED.value,
            cards_reviewed=stats["cards_reviewed"],
            ratings_synced=stats["synced_count"],
            ratings_failed=stats["failed_count"],
        )

        self._active_session = None

    async def force_end_all_sessions(self) -> int:
        """Force-end all active sessions (for graceful shutdown).

        Returns:
            Number of sessions ended
        """
        if self._active_session is None:
            return 0

        try:
            await self.end_session(self._active_session.id)
        except Exception:
            # Best effort - save to recovery store
            await self._end_timed_out_session()

        return 1
