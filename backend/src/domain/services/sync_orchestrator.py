"""Sync orchestrator service for resilient Anki synchronization."""

import logging
from dataclasses import dataclass

from src.domain.entities.session import PendingRating
from src.domain.value_objects.rating import Rating
from src.infrastructure.recovery_store import PendingReview, RecoveryStore
from src.infrastructure.retry import TransientError, retry_operation
from src.ports.flashcard_service import FlashcardService

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a sync operation."""

    synced_count: int
    failed_count: int
    errors: list[str]


@dataclass
class RecoveryResult:
    """Result of recovering pending ratings."""

    recovered_count: int
    failed_count: int


class SyncOrchestrator:
    """Orchestrates sync operations with Anki using retry logic.

    Handles:
    - Push sync: Send ratings to Anki with retry
    - Recovery replay: Replay pending ratings from crash recovery
    - Graceful degradation: Save failed ratings for later retry

    Uses exponential backoff (3 attempts, 2s base) for transient failures.
    """

    def __init__(
        self,
        flashcard_service: FlashcardService,
        recovery_store: RecoveryStore,
        max_retry_attempts: int = 3,
        initial_wait: float = 2.0,
    ):
        """Initialize sync orchestrator.

        Args:
            flashcard_service: Port for Anki operations
            recovery_store: Persistence for crash recovery
            max_retry_attempts: Max retry attempts per operation
            initial_wait: Initial wait time for exponential backoff
        """
        self._flashcard_service = flashcard_service
        self._recovery_store = recovery_store
        self._max_retry_attempts = max_retry_attempts
        self._initial_wait = initial_wait

    async def sync_ratings(
        self,
        ratings: list[PendingRating],
        session_id: str,
    ) -> SyncResult:
        """Sync a batch of ratings to Anki.

        Attempts to sync each rating with retry logic. Failed ratings
        are saved to the recovery store for later retry.

        Args:
            ratings: Pending ratings to sync
            session_id: Session ID for recovery tracking

        Returns:
            SyncResult with counts and error messages
        """
        synced = 0
        failed = 0
        errors: list[str] = []

        for rating in ratings:
            if rating.synced:
                synced += 1
                continue

            try:
                await self._sync_single_rating(rating)
                rating.synced = True
                synced += 1
                logger.debug(f"Synced rating for card {rating.card_id}")
            except Exception as e:
                # All retries failed - save to recovery store
                failed += 1
                error_msg = f"Card {rating.card_id}: {str(e)}"
                errors.append(error_msg)
                logger.warning(f"Failed to sync rating: {error_msg}")

                # Save to recovery store
                await self._recovery_store.save_review(
                    card_id=rating.card_id,
                    ease=int(rating.rating),
                    session_id=session_id,
                )

        return SyncResult(synced_count=synced, failed_count=failed, errors=errors)

    async def _sync_single_rating(self, rating: PendingRating) -> None:
        """Sync a single rating with retry.

        Args:
            rating: Rating to sync

        Raises:
            Exception: If all retry attempts fail
        """

        async def _do_sync() -> None:
            try:
                await self._flashcard_service.submit_review(rating.card_id, rating.rating)
            except Exception as e:
                # Wrap connection errors as transient for retry
                if self._is_transient_error(e):
                    raise TransientError(str(e)) from e
                raise

        await retry_operation(
            _do_sync,
            max_attempts=self._max_retry_attempts,
            initial_wait=self._initial_wait,
            on_retry=lambda attempt, exc: logger.info(
                f"Retry {attempt} for card {rating.card_id}: {exc}"
            ),
        )

    async def recover_pending_ratings(self) -> RecoveryResult:
        """Recover and sync pending ratings from previous sessions.

        Called at session start to replay failed syncs.
        Uses background recovery (no user notification per plan).

        Returns:
            RecoveryResult with counts
        """
        pending = await self._recovery_store.get_pending_reviews()

        if not pending:
            return RecoveryResult(recovered_count=0, failed_count=0)

        logger.info(f"Recovering {len(pending)} pending ratings")

        recovered = 0
        failed = 0

        for review in pending:
            try:
                await self._sync_pending_review(review)
                await self._recovery_store.mark_synced(review.id)
                recovered += 1
            except Exception as e:
                failed += 1
                await self._recovery_store.increment_retry(review.id)
                logger.warning(f"Recovery failed for review {review.id}: {e}")

        logger.info(f"Recovery complete: {recovered} synced, {failed} failed")
        return RecoveryResult(recovered_count=recovered, failed_count=failed)

    async def _sync_pending_review(self, review: PendingReview) -> None:
        """Sync a pending review from recovery store.

        Args:
            review: Review to sync

        Raises:
            Exception: If all retry attempts fail
        """

        async def _do_sync() -> None:
            try:
                await self._flashcard_service.submit_review(review.card_id, Rating(review.ease))
            except Exception as e:
                if self._is_transient_error(e):
                    raise TransientError(str(e)) from e
                raise

        await retry_operation(
            _do_sync,
            max_attempts=self._max_retry_attempts,
            initial_wait=self._initial_wait,
        )

    async def purge_old_ratings(self, days: int = 7) -> int:
        """Purge old unsynced ratings beyond retention period.

        Per plan: 7-day retention, then auto-purge with warning.

        Args:
            days: Retention period in days

        Returns:
            Number of purged ratings
        """
        purged = await self._recovery_store.purge_old_unsynced(days)
        if purged > 0:
            logger.warning(
                f"Purged {purged} ratings older than {days} days that couldn't be synced"
            )
        return purged

    def _is_transient_error(self, error: Exception) -> bool:
        """Check if an error is transient and should be retried.

        Args:
            error: The exception to check

        Returns:
            True if error is transient
        """
        transient_indicators = [
            "timeout",
            "connection",
            "unavailable",
            "temporary",
            "network",
        ]
        error_str = str(error).lower()
        return any(indicator in error_str for indicator in transient_indicators)
