/**
 * API client helpers for UNCONS backend.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * API error response shape.
 */
export interface ApiError {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

/**
 * Custom error class for API errors.
 */
export class ApiClientError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public details?: Record<string, unknown>
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

/**
 * Generic fetch wrapper with error handling.
 */
async function apiFetch<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;

  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const errorData = (await response.json()) as { detail?: ApiError };
    const error = errorData.detail?.error;
    throw new ApiClientError(
      error?.code || "UNKNOWN_ERROR",
      error?.message || "An unknown error occurred",
      response.status,
      error?.details
    );
  }

  return response.json() as Promise<T>;
}

// =============================================================================
// Types
// =============================================================================

export interface Card {
  id: number;
  question_html: string;
  answer_html: string;
  deck_name: string;
  image_url: string | null;
}

export interface Deck {
  name: string;
  new_count: number;
  learn_count: number;
  due_count: number;
  total_count: number;
}

export interface DecksResponse {
  decks: Deck[];
}

export interface StartSessionResponse {
  session_id: string;
  deck_name: string;
  state: string;
  due_count: number;
  cards: Card[];
  recovered_ratings: number;
}

export interface SessionStats {
  cards_reviewed: number;
  ratings: Record<string, number>;
  synced_count: number;
  failed_count: number;
  duration_minutes: number;
}

export interface EndSessionResponse {
  session_id: string;
  state: string;
  stats: SessionStats;
  warning: string | null;
}

export interface CurrentSessionResponse {
  session_id: string;
  deck_name: string;
  state: string;
  current_card: Card | null;
  remaining_count: number;
  cards_reviewed: number;
}

export interface LiveKitTokenResponse {
  token: string;
  url: string;
}

// =============================================================================
// API Functions
// =============================================================================

/**
 * Fetch available decks with due counts.
 */
export async function fetchDecks(): Promise<DecksResponse> {
  return apiFetch<DecksResponse>("/api/decks");
}

/**
 * Start a new review session.
 */
export async function startSession(
  deckName: string
): Promise<StartSessionResponse> {
  return apiFetch<StartSessionResponse>("/api/session/start", {
    method: "POST",
    body: JSON.stringify({ deck_name: deckName }),
  });
}

/**
 * End the current session and sync ratings.
 */
export async function endSession(
  sessionId: string
): Promise<EndSessionResponse> {
  return apiFetch<EndSessionResponse>("/api/session/end", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

/**
 * Check if an active session exists (HEAD request, no error logging).
 */
export async function hasActiveSession(): Promise<boolean> {
  const url = `${API_BASE_URL}/api/session/current`;
  try {
    const response = await fetch(url, { method: "HEAD" });
    return response.status === 204;
  } catch {
    return false;
  }
}

/**
 * Get current active session.
 */
export async function getCurrentSession(): Promise<CurrentSessionResponse> {
  return apiFetch<CurrentSessionResponse>("/api/session/current");
}

export interface ForceEndResponse {
  ended_sessions: number;
  message: string;
}

/**
 * Force-end any active session (DEV ONLY).
 */
export async function forceEndSession(): Promise<ForceEndResponse> {
  return apiFetch<ForceEndResponse>("/api/session/force-end", {
    method: "DELETE",
  });
}

/**
 * Get LiveKit token for joining a voice room.
 * @param inputMode - "vad" (auto-detect speech) or "push_to_talk" (manual button)
 */
export async function getLiveKitToken(
  roomName: string,
  participantName: string,
  deckName?: string,
  inputMode?: "vad" | "push_to_talk"
): Promise<LiveKitTokenResponse> {
  return apiFetch<LiveKitTokenResponse>("/api/livekit/token", {
    method: "POST",
    body: JSON.stringify({
      room_name: roomName,
      participant_name: participantName,
      deck_name: deckName,
      input_mode: inputMode,
    }),
  });
}

/**
 * Health check.
 */
export async function healthCheck(): Promise<{ status: string }> {
  return apiFetch<{ status: string }>("/health");
}
