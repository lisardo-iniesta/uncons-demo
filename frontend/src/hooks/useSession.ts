/**
 * useSession Hook
 *
 * Manages Anki review session state including cards, progress, and sync.
 */

import { useCallback, useState } from "react";
import {
  Card,
  SessionStats,
  startSession as apiStartSession,
  endSession as apiEndSession,
  getCurrentSession as apiGetCurrentSession,
  hasActiveSession as apiHasActiveSession,
  forceEndSession as apiForceEndSession,
  ApiClientError,
} from "../lib/api";

export type SessionState =
  | "idle"
  | "loading"
  | "active"
  | "ending"
  | "complete"
  | "error";

export interface UseSessionState {
  /** Current session state */
  state: SessionState;
  /** Session ID if active */
  sessionId: string | null;
  /** Name of the deck being reviewed */
  deckName: string | null;
  /** All cards in the session */
  cards: Card[];
  /** Index of current card */
  currentIndex: number;
  /** Current card being reviewed */
  currentCard: Card | null;
  /** Session statistics (available after end) */
  stats: SessionStats | null;
  /** Error message */
  error: string | null;
  /** Number of recovered ratings from previous session */
  recoveredRatings: number;
}

export interface UseSessionReturn extends UseSessionState {
  /** Start a new session with the given deck */
  start: (deckName: string) => Promise<void>;
  /** End the current session */
  end: () => Promise<void>;
  /** Move to the next card */
  nextCard: () => void;
  /** Get progress as fraction (0-1) */
  progress: number;
  /** Get remaining card count */
  remaining: number;
  /** Recover existing session (returns true if recovered) */
  recover: () => Promise<boolean>;
  /** Force-end any active session (DEV ONLY) */
  forceEnd: () => Promise<void>;
}

const initialState: UseSessionState = {
  state: "idle",
  sessionId: null,
  deckName: null,
  cards: [],
  currentIndex: 0,
  currentCard: null,
  stats: null,
  error: null,
  recoveredRatings: 0,
};

/**
 * Hook for managing Anki review sessions.
 *
 * @example
 * ```tsx
 * function ReviewPage({ deck }: { deck: string }) {
 *   const {
 *     state,
 *     currentCard,
 *     progress,
 *     start,
 *     end,
 *     nextCard,
 *   } = useSession();
 *
 *   useEffect(() => {
 *     start(deck);
 *   }, [deck]);
 *
 *   if (state === 'loading') return <Loading />;
 *   if (state === 'complete') return <Complete stats={stats} />;
 *
 *   return (
 *     <div>
 *       <ProgressBar value={progress} />
 *       <CardDisplay card={currentCard} />
 *     </div>
 *   );
 * }
 * ```
 */
export function useSession(): UseSessionReturn {
  const [session, setSession] = useState<UseSessionState>(initialState);

  const start = useCallback(async (deckName: string) => {
    setSession((prev) => ({
      ...prev,
      state: "loading",
      error: null,
      deckName,
    }));

    try {
      const response = await apiStartSession(deckName);

      setSession({
        state: "active",
        sessionId: response.session_id,
        deckName: response.deck_name,
        cards: response.cards,
        currentIndex: 0,
        currentCard: response.cards[0] || null,
        stats: null,
        error: null,
        recoveredRatings: response.recovered_ratings,
      });
    } catch (err) {
      const message =
        err instanceof ApiClientError
          ? err.message
          : "Failed to start session";
      setSession((prev) => ({
        ...prev,
        state: "error",
        error: message,
      }));
    }
  }, []);

  const end = useCallback(async () => {
    if (!session.sessionId) return;

    setSession((prev) => ({
      ...prev,
      state: "ending",
    }));

    try {
      const response = await apiEndSession(session.sessionId);

      setSession((prev) => ({
        ...prev,
        state: "complete",
        stats: response.stats,
        error: response.warning || null,
      }));
    } catch (err) {
      const message =
        err instanceof ApiClientError ? err.message : "Failed to end session";
      setSession((prev) => ({
        ...prev,
        state: "error",
        error: message,
      }));
    }
  }, [session.sessionId]);

  const nextCard = useCallback(() => {
    setSession((prev) => {
      const nextIndex = prev.currentIndex + 1;

      // If no more cards, keep current state
      if (nextIndex >= prev.cards.length) {
        return prev;
      }

      return {
        ...prev,
        currentIndex: nextIndex,
        currentCard: prev.cards[nextIndex],
      };
    });
  }, []);

  const recover = useCallback(async (): Promise<boolean> => {
    // First check with HEAD to avoid 404 error logging
    const exists = await apiHasActiveSession();
    if (!exists) {
      return false;
    }

    try {
      const response = await apiGetCurrentSession();

      // Session exists - restore it
      setSession({
        state: "active",
        sessionId: response.session_id,
        deckName: response.deck_name,
        cards: [], // We don't get full cards back from /current
        currentIndex: response.cards_reviewed,
        currentCard: response.current_card,
        stats: null,
        error: null,
        recoveredRatings: 0,
      });
      return true;
    } catch {
      // Session disappeared between HEAD and GET - that's fine
      return false;
    }
  }, []);

  const forceEnd = useCallback(async (): Promise<void> => {
    try {
      await apiForceEndSession();
      setSession(initialState);
    } catch (err) {
      console.error("Failed to force-end session:", err);
    }
  }, []);

  const progress =
    session.cards.length > 0
      ? session.currentIndex / session.cards.length
      : 0;

  const remaining = session.cards.length - session.currentIndex;

  return {
    ...session,
    start,
    end,
    nextCard,
    progress,
    remaining,
    recover,
    forceEnd,
  };
}
