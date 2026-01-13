"""SQLite-based recovery store for failed reviews and sessions.

Persists reviews that couldn't be synced to Anki, allowing retry
at next session start. Also stores session history for stats tracking.
Uses async-safe operations with threading.
"""

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass
class PendingReview:
    """Review waiting to be synced to Anki."""

    id: int
    card_id: int
    ease: int
    timestamp: datetime
    session_id: str
    retry_count: int


@dataclass
class SessionRecord:
    """Session record for history tracking."""

    id: str
    deck_name: str
    state: str
    started_at: datetime
    ended_at: datetime | None
    cards_reviewed: int
    ratings_synced: int
    ratings_failed: int


class RecoveryStore:
    """SQLite-based recovery store for failed reviews.

    Thread-safe async operations using asyncio.Lock and to_thread.
    Stores reviews that couldn't be synced, replays them on next session.
    """

    def __init__(self, db_path: str = "recovery.db"):
        """Initialize recovery store.

        Args:
            db_path: Path to SQLite database file

        Database tables are created synchronously on construction.
        """
        self._db_path = Path(db_path)
        self._lock = asyncio.Lock()
        self._initialized = False
        # Initialize tables synchronously (safe during startup)
        self._init_db()
        self._initialized = True

    async def initialize(self) -> None:
        """Initialize database schema (async-safe).

        Must be called after construction before using the store.
        Uses asyncio.to_thread to avoid blocking the event loop.
        """
        if not self._initialized:
            await asyncio.to_thread(self._init_db)
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        """Create optimized SQLite connection.

        Applies performance PRAGMAs for better concurrency.
        Note: journal_mode=WAL persists to database file.
        """
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._connect() as conn:
            # WAL mode persists to database file (only needs to be set once)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id INTEGER NOT NULL,
                    ease INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    synced_at TEXT
                )
            """
            )
            # Partial index for efficient unsynced query
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_unsynced
                ON pending_reviews(synced_at) WHERE synced_at IS NULL
            """
            )
            # Sessions table for history tracking
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    deck_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    cards_reviewed INTEGER DEFAULT 0,
                    ratings_synced INTEGER DEFAULT 0,
                    ratings_failed INTEGER DEFAULT 0
                )
            """
            )
            # Index for finding incomplete sessions
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_incomplete
                ON sessions(state) WHERE state NOT IN ('complete', 'idle')
            """
            )

    async def save_review(
        self,
        card_id: int,
        ease: int,
        session_id: str,
    ) -> int:
        """Save review to recovery store (async-safe).

        Args:
            card_id: Anki card ID
            ease: Rating value (1-4)
            session_id: Current session identifier

        Returns:
            ID of the saved review record
        """
        async with self._lock:
            return await asyncio.to_thread(self._save_review_sync, card_id, ease, session_id)

    def _save_review_sync(
        self,
        card_id: int,
        ease: int,
        session_id: str,
    ) -> int:
        """Synchronous save implementation."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO pending_reviews (card_id, ease, timestamp, session_id)
                VALUES (?, ?, ?, ?)
                """,
                (card_id, ease, datetime.now(UTC).isoformat(), session_id),
            )
            return cursor.lastrowid or 0

    async def get_pending_reviews(self) -> list[PendingReview]:
        """Get all unsynced reviews ordered by timestamp."""
        async with self._lock:
            return await asyncio.to_thread(self._get_pending_sync)

    def _get_pending_sync(self) -> list[PendingReview]:
        """Synchronous get implementation."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM pending_reviews
                WHERE synced_at IS NULL
                ORDER BY timestamp ASC
                """
            ).fetchall()
            return [
                PendingReview(
                    id=row["id"],
                    card_id=row["card_id"],
                    ease=row["ease"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    session_id=row["session_id"],
                    retry_count=row["retry_count"],
                )
                for row in rows
            ]

    async def mark_synced(self, review_id: int) -> None:
        """Mark review as successfully synced."""
        async with self._lock:
            await asyncio.to_thread(self._mark_synced_sync, review_id)

    def _mark_synced_sync(self, review_id: int) -> None:
        """Synchronous mark implementation."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pending_reviews
                SET synced_at = ?
                WHERE id = ?
                """,
                (datetime.now(UTC).isoformat(), review_id),
            )

    async def increment_retry(self, review_id: int) -> None:
        """Increment retry count for failed sync attempt."""
        async with self._lock:
            await asyncio.to_thread(self._increment_retry_sync, review_id)

    def _increment_retry_sync(self, review_id: int) -> None:
        """Synchronous increment implementation."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pending_reviews
                SET retry_count = retry_count + 1
                WHERE id = ?
                """,
                (review_id,),
            )

    async def cleanup_old(self, days: int = 30) -> int:
        """Delete synced reviews older than N days.

        Args:
            days: Age threshold for cleanup

        Returns:
            Number of deleted records
        """
        async with self._lock:
            return await asyncio.to_thread(self._cleanup_sync, days)

    def _cleanup_sync(self, days: int) -> int:
        """Synchronous cleanup implementation."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM pending_reviews
                WHERE synced_at IS NOT NULL
                AND datetime(synced_at) < datetime('now', ?)
                """,
                (f"-{days} days",),
            )
            return cursor.rowcount

    async def get_pending_count(self) -> int:
        """Get count of pending (unsynced) reviews."""
        async with self._lock:
            return await asyncio.to_thread(self._get_pending_count_sync)

    def _get_pending_count_sync(self) -> int:
        """Synchronous count implementation."""
        with self._connect() as conn:
            result = conn.execute(
                "SELECT COUNT(*) FROM pending_reviews WHERE synced_at IS NULL"
            ).fetchone()
            return result[0] if result else 0

    # --- Session persistence methods ---

    async def save_session(
        self,
        session_id: str,
        deck_name: str,
        state: str,
        started_at: datetime,
        cards_reviewed: int = 0,
        ratings_synced: int = 0,
        ratings_failed: int = 0,
    ) -> None:
        """Save or update session record.

        Args:
            session_id: Unique session identifier
            deck_name: Name of the deck being reviewed
            state: Current session state
            started_at: When session started
            cards_reviewed: Number of cards reviewed
            ratings_synced: Number of ratings successfully synced
            ratings_failed: Number of ratings that failed to sync
        """
        async with self._lock:
            await asyncio.to_thread(
                self._save_session_sync,
                session_id,
                deck_name,
                state,
                started_at,
                cards_reviewed,
                ratings_synced,
                ratings_failed,
            )

    def _save_session_sync(
        self,
        session_id: str,
        deck_name: str,
        state: str,
        started_at: datetime,
        cards_reviewed: int,
        ratings_synced: int,
        ratings_failed: int,
    ) -> None:
        """Synchronous session save implementation."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, deck_name, state, started_at,
                    cards_reviewed, ratings_synced, ratings_failed
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    state = excluded.state,
                    cards_reviewed = excluded.cards_reviewed,
                    ratings_synced = excluded.ratings_synced,
                    ratings_failed = excluded.ratings_failed
                """,
                (
                    session_id,
                    deck_name,
                    state,
                    started_at.isoformat(),
                    cards_reviewed,
                    ratings_synced,
                    ratings_failed,
                ),
            )

    async def end_session(
        self,
        session_id: str,
        state: str,
        cards_reviewed: int,
        ratings_synced: int,
        ratings_failed: int,
    ) -> None:
        """Mark session as ended with final stats.

        Args:
            session_id: Session identifier
            state: Final session state
            cards_reviewed: Total cards reviewed
            ratings_synced: Total ratings synced
            ratings_failed: Total ratings failed
        """
        async with self._lock:
            await asyncio.to_thread(
                self._end_session_sync,
                session_id,
                state,
                cards_reviewed,
                ratings_synced,
                ratings_failed,
            )

    def _end_session_sync(
        self,
        session_id: str,
        state: str,
        cards_reviewed: int,
        ratings_synced: int,
        ratings_failed: int,
    ) -> None:
        """Synchronous session end implementation."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET state = ?,
                    ended_at = ?,
                    cards_reviewed = ?,
                    ratings_synced = ?,
                    ratings_failed = ?
                WHERE id = ?
                """,
                (
                    state,
                    datetime.now(UTC).isoformat(),
                    cards_reviewed,
                    ratings_synced,
                    ratings_failed,
                    session_id,
                ),
            )

    async def get_active_session(self, max_age_seconds: int = 60) -> SessionRecord | None:
        """Get the most recent active session (for worker initialization).

        Args:
            max_age_seconds: Only return sessions started within this many seconds.
                            Prevents using stale sessions from previous runs.

        Returns:
            Most recent session in 'active' state started recently, or None.
        """
        async with self._lock:
            return await asyncio.to_thread(self._get_active_session_sync, max_age_seconds)

    def _get_active_session_sync(self, max_age_seconds: int) -> SessionRecord | None:
        """Synchronous active session query."""
        # Calculate cutoff time in ISO format (matches how we store timestamps)
        cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
        cutoff_iso = cutoff.isoformat()

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            # Compare ISO strings directly (they sort correctly for same timezone)
            row = conn.execute(
                """
                SELECT * FROM sessions
                WHERE state = 'active'
                AND started_at > ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (cutoff_iso,),
            ).fetchone()
            if row:
                return self._row_to_session(row)
            return None

    async def get_incomplete_sessions(self) -> list[SessionRecord]:
        """Get sessions that didn't complete (for crash recovery)."""
        async with self._lock:
            return await asyncio.to_thread(self._get_incomplete_sessions_sync)

    def _get_incomplete_sessions_sync(self) -> list[SessionRecord]:
        """Synchronous incomplete sessions query."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM sessions
                WHERE state NOT IN ('complete', 'idle')
                ORDER BY started_at DESC
                """
            ).fetchall()
            return [self._row_to_session(row) for row in rows]

    async def purge_old_unsynced(self, days: int = 7) -> int:
        """Purge unsynced reviews older than N days.

        Args:
            days: Age threshold for purging (default 7)

        Returns:
            Number of purged records
        """
        async with self._lock:
            return await asyncio.to_thread(self._purge_old_unsynced_sync, days)

    def _purge_old_unsynced_sync(self, days: int) -> int:
        """Synchronous purge implementation."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM pending_reviews
                WHERE synced_at IS NULL
                AND datetime(timestamp) < datetime('now', ?)
                """,
                (f"-{days} days",),
            )
            return cursor.rowcount

    def _row_to_session(self, row: sqlite3.Row) -> SessionRecord:
        """Convert database row to SessionRecord."""
        return SessionRecord(
            id=row["id"],
            deck_name=row["deck_name"],
            state=row["state"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=(datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None),
            cards_reviewed=row["cards_reviewed"],
            ratings_synced=row["ratings_synced"],
            ratings_failed=row["ratings_failed"],
        )

    async def reset_stale_processing(self) -> int:
        """Reset sessions stuck in processing state from crash.

        Called on startup to clean up any sessions that were left
        in an intermediate state due to server crash.

        Returns:
            Number of sessions reset
        """
        async with self._lock:
            return await asyncio.to_thread(self._reset_stale_processing_sync)

    def _reset_stale_processing_sync(self) -> int:
        """Synchronous reset implementation."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE sessions
                SET state = 'crashed',
                    ended_at = ?
                WHERE state NOT IN ('complete', 'idle', 'crashed')
                AND ended_at IS NULL
                """,
                (datetime.now(UTC).isoformat(),),
            )
            return cursor.rowcount

    def close(self) -> None:
        """Close the recovery store.

        No-op since connections are short-lived per operation.
        Provided for API consistency with cleanup code.
        """
        pass
