"""Session entity for review session lifecycle management."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import uuid4

from src.domain.entities.card import Card
from src.domain.value_objects.rating import Rating
from src.domain.value_objects.session_state import SessionState


@dataclass
class PendingRating:
    """Rating waiting to be synced to Anki.

    Attributes:
        card_id: Anki card ID
        rating: User's rating (1-4)
        timestamp: When the rating was recorded
        synced: Whether rating has been synced to Anki
    """

    card_id: int
    rating: Rating
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    synced: bool = False


@dataclass
class Session:
    """Review session entity.

    Manages the lifecycle of a single review session including:
    - Session state transitions
    - Card queue management (with skip support)
    - Rating accumulation for batch sync

    Attributes:
        id: Unique session identifier (UUID v4)
        deck_name: Name of the deck being reviewed
        state: Current session state
        cards: List of cards to review (loaded at session start)
        current_index: Current position in card queue
        pending_ratings: Ratings to sync at session end
        started_at: When session started
        last_activity: Last user activity timestamp (for timeout)
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    deck_name: str = ""
    state: SessionState = SessionState.IDLE
    cards: list[Card] = field(default_factory=list)
    current_index: int = 0
    pending_ratings: list[PendingRating] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(cls, deck_name: str, cards: list[Card]) -> Self:
        """Create a new active session.

        Args:
            deck_name: Name of the deck being reviewed
            cards: Cards to review in this session

        Returns:
            New session in ACTIVE state
        """
        return cls(
            deck_name=deck_name,
            state=SessionState.ACTIVE,
            cards=list(cards),  # Copy to avoid mutating caller's list
        )

    def get_current_card(self) -> Card | None:
        """Get the current card to review.

        Returns:
            Current card or None if queue is empty
        """
        if self.current_index >= len(self.cards):
            return None
        return self.cards[self.current_index]

    def get_remaining_count(self) -> int:
        """Get number of cards remaining in queue."""
        return max(0, len(self.cards) - self.current_index)

    def record_rating(self, rating: Rating) -> Card | None:
        """Record a rating for the current card and advance.

        Args:
            rating: User's rating for current card

        Returns:
            Next card to review, or None if queue is empty

        Raises:
            ValueError: If no current card or session can't accept ratings
        """
        if not self.state.can_accept_ratings():
            raise ValueError(f"Cannot record rating in state {self.state}")

        current_card = self.get_current_card()
        if current_card is None:
            raise ValueError("No card to rate - queue is empty")

        self.pending_ratings.append(PendingRating(card_id=current_card.id, rating=rating))
        self.current_index += 1
        self.touch()

        return self.get_current_card()

    def skip_current_card(self) -> Card | None:
        """Skip current card and move it to end of queue.

        Returns:
            Next card to review, or None if queue is empty

        Raises:
            ValueError: If no current card or session can't accept ratings
        """
        if not self.state.can_accept_ratings():
            raise ValueError(f"Cannot skip card in state {self.state}")

        current_card = self.get_current_card()
        if current_card is None:
            raise ValueError("No card to skip - queue is empty")

        # Remove current card and append to end
        self.cards.pop(self.current_index)
        self.cards.append(current_card)
        self.touch()

        return self.get_current_card()

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now(UTC)

    def is_timed_out(self, timeout_minutes: int = 30) -> bool:
        """Check if session has timed out due to inactivity.

        Args:
            timeout_minutes: Inactivity threshold in minutes

        Returns:
            True if session has timed out
        """
        timeout_delta = timedelta(minutes=timeout_minutes)
        return datetime.now(UTC) - self.last_activity > timeout_delta

    def get_stats(self) -> dict:
        """Get session statistics.

        Returns:
            Dictionary with session stats
        """
        rating_counts = {"again": 0, "hard": 0, "good": 0, "easy": 0}
        for pr in self.pending_ratings:
            rating_counts[str(pr.rating)] += 1

        synced_count = sum(1 for pr in self.pending_ratings if pr.synced)
        failed_count = len(self.pending_ratings) - synced_count

        duration = datetime.now(UTC) - self.started_at
        duration_minutes = int(duration.total_seconds() / 60)

        return {
            "cards_reviewed": len(self.pending_ratings),
            "ratings": rating_counts,
            "synced_count": synced_count,
            "failed_count": failed_count,
            "duration_minutes": duration_minutes,
        }

    def transition_to(self, new_state: SessionState) -> None:
        """Transition session to a new state.

        Args:
            new_state: Target state

        Raises:
            ValueError: If transition is invalid
        """
        valid_transitions = {
            SessionState.IDLE: {SessionState.SYNCING_START},
            SessionState.SYNCING_START: {SessionState.ACTIVE, SessionState.DEGRADED},
            SessionState.ACTIVE: {SessionState.SYNCING_END, SessionState.DEGRADED},
            SessionState.DEGRADED: {
                SessionState.ACTIVE,
                SessionState.SYNCING_END,
                SessionState.COMPLETE,
            },
            SessionState.SYNCING_END: {SessionState.COMPLETE, SessionState.DEGRADED},
            SessionState.COMPLETE: set(),  # Terminal state
        }

        allowed = valid_transitions.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition from {self.state} to {new_state}. " f"Allowed: {allowed}"
            )

        self.state = new_state
        self.touch()
