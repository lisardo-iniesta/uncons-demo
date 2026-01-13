/**
 * useVoiceSession Hook
 *
 * Manages LiveKit voice session state for UNCONS voice tutoring.
 * Provides connection management, status tracking, and visual state.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Room,
  RoomEvent,
  ConnectionState,
  Track,
  RemoteTrack,
} from "livekit-client";

export type VoiceSessionStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | "error";

export interface Card {
  id: number;
  question_html: string;
  answer_html: string;
  deck_name: string;
  image_url?: string;
}

export interface VoiceProgress {
  cardsReviewed: number;
  cardsRemaining: number;
}

export interface VoiceSessionStats {
  cardsReviewed: number;
  ratingDistribution: Record<string, number>;
  durationMinutes: number;
  syncedCount: number;
  failedCount: number;
}

export interface VoiceSessionState {
  /** Current connection status */
  status: VoiceSessionStatus;
  /** User is currently speaking */
  isSpeaking: boolean;
  /** AI is processing the answer */
  isProcessing: boolean;
  /** Agent is speaking (TTS playing) */
  isAgentSpeaking: boolean;
  /** Current flashcard being reviewed */
  currentCard: Card | null;
  /** Live transcript of user speech */
  transcript: string;
  /** Error message if status is 'error' */
  error: string | null;
  /** Whether microphone is muted */
  isMuted: boolean;
  /** Latest agent message (for text mode transcript) */
  agentMessage: string | null;
  /** Latest voice transcript from STT (for text mode display) */
  lastVoiceTranscript: string | null;
  /** Progress info from voice agent */
  progress: VoiceProgress | null;
  /** Whether session is complete */
  isComplete: boolean;
  /** Session stats (available when complete) */
  sessionStats: VoiceSessionStats | null;
  /** Last rating received (1=Again, 2=Hard, 3=Good, 4=Easy) */
  lastRating: number | null;
  /** Whether push-to-talk mode is enabled */
  isPTTMode: boolean;
  /** Whether currently recording in PTT mode */
  isPTTRecording: boolean;
  /** Whether showing rating result (card back + Next button) */
  showingResult: boolean;
  /** Card back content from rating result */
  cardBack: string | null;
  /** Feedback message from rating */
  lastFeedback: string | null;
}

export interface UseVoiceSessionReturn extends VoiceSessionState {
  /** Connect to a voice session */
  connect: (token: string, options?: { enableMicrophone?: boolean; pttMode?: boolean; deckName?: string; sessionId?: string }) => Promise<void>;
  /** Disconnect from the session */
  disconnect: () => void;
  /** Toggle microphone mute */
  setMuted: (muted: boolean) => void;
  /** Enable microphone on existing connection (for upgrading text-only to voice) */
  enableMicrophone: () => Promise<void>;
  /** Send text input (for silent testing mode) */
  sendTextInput: (text: string) => Promise<void>;
  /** Send question in question mode (doesn't affect rating) */
  sendQuestion: (question: string) => Promise<void>;
  /** Start PTT recording (hold button) */
  startPTT: () => Promise<void>;
  /** End PTT recording and submit (release button) */
  endPTT: () => Promise<void>;
  /** Cancel PTT recording (mouse leave, escape) */
  cancelPTT: () => Promise<void>;
  /** Request a hint for the current card */
  sendHint: () => Promise<void>;
  /** Give up on current card and reveal answer */
  sendGiveUp: () => Promise<void>;
  /** Send mnemonic request (generates memory aid for current card) */
  sendMnemonicRequest: () => Promise<void>;
}

const LIVEKIT_URL = process.env.NEXT_PUBLIC_LIVEKIT_URL || "";

// =============================================================================
// Message Deduplication
// =============================================================================
// Prevents duplicate agent messages from being displayed.
// Backend may send same message via pre-send and speech_created event.
// Messages with same ID are deduplicated on the frontend.

const MAX_SEEN_MESSAGES = 100;
const seenMessageIds = new Set<string>();

// =============================================================================
// Audio Autoplay Handling
// =============================================================================
// Browsers block audio autoplay until user interaction.
// We queue failed audio elements and retry on first user gesture (PTT button).

const pendingAudioElements: HTMLAudioElement[] = [];
let audioUnlocked = false;

/**
 * Retry playing any audio elements that failed due to autoplay policy.
 * Call this from a user gesture handler (e.g., PTT button click).
 */
async function retryPendingAudio(): Promise<void> {
  if (audioUnlocked) return;

  console.log("[Audio] Unlocking audio on user gesture, pending:", pendingAudioElements.length);
  audioUnlocked = true;

  // Try to resume any AudioContext (some browsers need this)
  try {
    // @ts-expect-error - webkitAudioContext for Safari
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      const ctx = new AudioContextClass();
      await ctx.resume();
      ctx.close();
    }
  } catch (e) {
    console.log("[Audio] AudioContext resume:", e);
  }

  // Retry playing pending audio elements
  for (const audioEl of pendingAudioElements) {
    try {
      await audioEl.play();
      console.log("[Audio] Retry succeeded:", audioEl.id);
    } catch (e) {
      console.log("[Audio] Retry failed:", audioEl.id, e);
    }
  }
  pendingAudioElements.length = 0; // Clear the queue
}

/**
 * Reset audio state (call on disconnect).
 */
function resetAudioState(): void {
  pendingAudioElements.length = 0;
  audioUnlocked = false;
}

/**
 * Track a message ID and return whether it's new.
 * @returns true if message is NEW (should be displayed)
 * @returns false if message is DUPLICATE (should be skipped)
 */
function trackMessageId(id: string): boolean {
  if (seenMessageIds.has(id)) {
    return false; // Duplicate
  }

  // Add to seen set
  seenMessageIds.add(id);

  // Bounded: remove oldest if over limit (LRU-style)
  if (seenMessageIds.size > MAX_SEEN_MESSAGES) {
    const oldest = seenMessageIds.values().next().value;
    if (oldest) {
      seenMessageIds.delete(oldest);
    }
  }

  return true; // New message
}

/**
 * Reset deduplication state (call on disconnect).
 */
function resetDeduplication(): void {
  seenMessageIds.clear();
}

/**
 * Hook for managing LiveKit voice sessions.
 *
 * @example
 * ```tsx
 * function VoiceReview() {
 *   const {
 *     status,
 *     isSpeaking,
 *     isProcessing,
 *     connect,
 *     disconnect,
 *   } = useVoiceSession();
 *
 *   const handleStart = async () => {
 *     const token = await fetchToken(); // Get from your API
 *     await connect(token);
 *   };
 *
 *   return (
 *     <div>
 *       <VoiceIndicator status={status} isProcessing={isProcessing} />
 *       <button onClick={handleStart}>Start Review</button>
 *     </div>
 *   );
 * }
 * ```
 */
export function useVoiceSession(): UseVoiceSessionReturn {
  const [room, setRoom] = useState<Room | null>(null);
  const [state, setState] = useState<VoiceSessionState>({
    status: "disconnected",
    isSpeaking: false,
    isProcessing: false,
    isAgentSpeaking: false,
    currentCard: null,
    transcript: "",
    error: null,
    isMuted: false,
    agentMessage: null,
    lastVoiceTranscript: null,
    progress: null,
    isComplete: false,
    sessionStats: null,
    lastRating: null,
    isPTTMode: false,
    isPTTRecording: false,
    showingResult: false,
    cardBack: null,
    lastFeedback: null,
  });

  // Refs for PTT state (needed for event handlers to access current state)
  const pttModeRef = useRef(false);
  const pttRecordingRef = useRef(false);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (room) {
        room.disconnect();
      }
    };
  }, [room]);

  const connect = useCallback(async (token: string, options?: { enableMicrophone?: boolean; pttMode?: boolean; deckName?: string; sessionId?: string }) => {
    const enableMicrophone = options?.enableMicrophone ?? true;
    const pttMode = options?.pttMode ?? false;
    const deckName = options?.deckName;
    const sessionId = options?.sessionId;
    if (!LIVEKIT_URL) {
      setState((prev) => ({
        ...prev,
        status: "error",
        error: "LiveKit URL not configured",
      }));
      return;
    }

    setState((prev) => ({ ...prev, status: "connecting", error: null }));

    try {
      const newRoom = new Room({
        adaptiveStream: true,
        dynacast: true,
      });

      // Set up event listeners
      newRoom.on(RoomEvent.ConnectionStateChanged, (connectionState) => {
        if (connectionState === ConnectionState.Connected) {
          // Reset session state when new session connects to prevent stale data from previous sessions
          setState((prev) => ({
            ...prev,
            status: "connected",
            lastRating: null,
            isComplete: false,
            sessionStats: null,
            progress: null,
          }));
        } else if (connectionState === ConnectionState.Disconnected) {
          setState((prev) => ({ ...prev, status: "disconnected" }));
        }
      });

      newRoom.on(RoomEvent.LocalTrackPublished, () => {
        // Local track ready
      });

      // Track agent audio state and play audio
      newRoom.on(
        RoomEvent.TrackSubscribed,
        async (track: RemoteTrack, publication, participant) => {
          console.log("[TrackSubscribed]", {
            kind: track.kind,
            sid: track.sid,
            participant: participant?.identity,
            isMuted: track.isMuted,
            mediaStreamTrack: track.mediaStreamTrack?.readyState,
          });
          if (track.kind === Track.Kind.Audio) {
            // Attach the track to play the audio
            const audioElement = track.attach();
            audioElement.id = `agent-audio-${track.sid}`;
            console.log("[Audio] Element details:", {
              id: audioElement.id,
              muted: audioElement.muted,
              volume: audioElement.volume,
              srcObject: audioElement.srcObject,
              readyState: audioElement.readyState,
            });
            document.body.appendChild(audioElement);

            // Try to play audio - handle autoplay policy
            try {
              console.log("[Audio] Attempting play...");
              const playPromise = audioElement.play();
              // Add timeout to detect hanging play() calls
              const timeoutPromise = new Promise((_, reject) =>
                setTimeout(() => reject(new Error("Play timeout after 5s")), 5000)
              );
              await Promise.race([playPromise, timeoutPromise]);
              console.log("[Audio] Play succeeded:", audioElement.id, "currentTime:", audioElement.currentTime);
            } catch (e) {
              console.error("[Audio] Play failed:", e);
              // Queue for retry on user interaction (autoplay policy)
              if (!audioUnlocked) {
                console.log("[Audio] Queueing for retry on user gesture:", audioElement.id);
                pendingAudioElements.push(audioElement);
              }
            }

            setState((prev) => ({ ...prev, isAgentSpeaking: true }));

            // When track ends, agent stopped speaking
            track.on("ended", () => {
              console.log("[Audio] Track ended:", track.sid);
              setState((prev) => ({ ...prev, isAgentSpeaking: false }));
              audioElement.remove();
            });

            // Log when audio element has data
            audioElement.onloadeddata = () => console.log("[Audio] Data loaded for:", audioElement.id);
            audioElement.onplaying = () => console.log("[Audio] Actually playing:", audioElement.id);
            audioElement.onerror = (e) => console.error("[Audio] Element error:", e);
          }
        }
      );

      newRoom.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
        if (track.kind === Track.Kind.Audio) {
          // Detach and clean up audio element
          track.detach().forEach((el) => el.remove());
          setState((prev) => ({ ...prev, isAgentSpeaking: false }));
        }
      });

      // Track local speaking state
      // In PTT mode, only show speaking when actively recording
      newRoom.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const localIsActive = speakers.some(
          (s) => s.identity === newRoom.localParticipant?.identity
        );

        // Use functional setState to read latest PTT state (avoids stale ref race condition)
        setState((prev) => {
          const shouldShowSpeaking = prev.isPTTMode
            ? localIsActive && prev.isPTTRecording
            : localIsActive;
          console.log("[ActiveSpeakers]", {
            localIsActive,
            pttMode: prev.isPTTMode,
            pttRecording: prev.isPTTRecording,
            shouldShowSpeaking,
          });
          return { ...prev, isSpeaking: shouldShowSpeaking };
        });
      });

      // Handle data messages (for transcript updates, card changes, etc.)
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      newRoom.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
        try {
          const data = JSON.parse(new TextDecoder().decode(payload));
          console.log("[DataReceived]", { type: data.type, topic, data });

          if (data.type === "transcript") {
            setState((prev) => ({
              ...prev,
              transcript: data.text,
              isProcessing: data.isFinal,
            }));
          } else if (data.type === "card") {
            // New card received - reset result state
            setState((prev) => ({
              ...prev,
              currentCard: data.card,
              isProcessing: false,
              progress: data.progress
                ? {
                    cardsReviewed: data.progress.cards_reviewed,
                    cardsRemaining: data.progress.cards_remaining,
                  }
                : prev.progress,
              lastRating: data.last_rating ?? null,
              // Reset result state for new card
              showingResult: false,
              cardBack: null,
              lastFeedback: null,
            }));
          } else if (data.type === "rating_result") {
            // Rating received - show card back and Next button
            setState((prev) => ({
              ...prev,
              showingResult: true,
              lastRating: data.rating,
              cardBack: data.card_back,
              lastFeedback: data.feedback,
              isProcessing: false,
              progress: data.progress
                ? {
                    cardsReviewed: data.progress.cards_reviewed,
                    cardsRemaining: data.progress.cards_remaining,
                  }
                : prev.progress,
            }));
          } else if (data.type === "reveal_answer") {
            // Hint reveal (level 2+) - show card back with Again rating
            // If user needed to reveal, they should see it again (rating 1)
            setState((prev) => ({
              ...prev,
              showingResult: true,
              cardBack: data.card_back,
              lastRating: 1, // Again - user couldn't recall
              isProcessing: false,
              progress: data.progress
                ? {
                    cardsReviewed: data.progress.cards_reviewed,
                    cardsRemaining: data.progress.cards_remaining,
                  }
                : prev.progress,
            }));
          } else if (data.type === "processing") {
            setState((prev) => ({ ...prev, isProcessing: data.value }));
          } else if (data.type === "agent_message") {
            // Deduplicate by message ID (prevents duplicate display from pre-send + event)
            const messageId = data.id as string | undefined;

            if (messageId) {
              // Has ID - use deduplication
              if (trackMessageId(messageId)) {
                // New message - display it
                setState((prev) => ({ ...prev, agentMessage: data.text }));
              } else {
                // Duplicate - skip silently
                console.debug(
                  `Skipping duplicate agent message: ${messageId.slice(0, 8)}...`
                );
              }
            } else {
              // No ID (legacy backend) - always display for backwards compatibility
              setState((prev) => ({ ...prev, agentMessage: data.text }));
            }
          } else if (data.type === "voice_transcript") {
            // Voice transcript from STT (VAD mode) - display in text mode panel
            setState((prev) => ({ ...prev, lastVoiceTranscript: data.text }));
          } else if (data.type === "user_transcript") {
            // Final combined user transcript (PTT mode) - display in text mode panel
            console.log("[user_transcript] Received:", data.text, "Setting lastVoiceTranscript");
            setState((prev) => {
              console.log("[user_transcript] Previous lastVoiceTranscript:", prev.lastVoiceTranscript);
              return { ...prev, lastVoiceTranscript: data.text };
            });
          } else if (data.type === "session_complete") {
            // Session complete - trigger navigation to complete page
            console.log("[session_complete] Received:", data.stats);
            setState((prev) => {
              console.log("[session_complete] Setting isComplete=true");
              return {
                ...prev,
                isComplete: true,
                sessionStats: data.stats
                  ? {
                      cardsReviewed: data.stats.cards_reviewed,
                      ratingDistribution: data.stats.ratings || {},
                      durationMinutes: data.stats.duration_minutes ?? 0,
                      syncedCount: data.stats.synced_count ?? data.stats.cards_reviewed,
                      failedCount: data.stats.failed_count ?? 0,
                    }
                  : null,
              };
            });
          } else if (data.type === "ptt_state") {
            // PTT state update from backend (confirmation of recording state)
            setState((prev) => ({ ...prev, isPTTRecording: data.recording }));
          } else if (data.type === "agent_speaking_state") {
            // Agent speaking state from backend (TTS start/stop)
            // More reliable than audio track events
            setState((prev) => ({ ...prev, isAgentSpeaking: data.speaking }));
          }
        } catch (e) {
          console.error("[DataReceived] Parse error:", e);
        }
      });

      newRoom.on(RoomEvent.Disconnected, () => {
        setState((prev) => ({
          ...prev,
          status: "disconnected",
          isProcessing: false,
          isAgentSpeaking: false,
        }));
      });

      // Connect to room
      await newRoom.connect(LIVEKIT_URL, token);

      // Enable microphone (can be disabled for text-mode-only E2E tests)
      // In PTT mode, microphone is enabled but audio is disabled on backend until button press
      if (enableMicrophone) {
        await newRoom.localParticipant?.setMicrophoneEnabled(true);
      }
      setState((prev) => ({ ...prev, isMuted: !enableMicrophone }));

      setRoom(newRoom);
      pttModeRef.current = pttMode;  // Update ref for event handlers
      setState((prev) => ({ ...prev, status: "connected", isPTTMode: pttMode }));

      // Send init_session to agent if deck name provided (bypasses recovery store)
      if (deckName && newRoom.localParticipant) {
        const encoder = new TextEncoder();
        const initData = encoder.encode(
          JSON.stringify({
            type: "init_session",
            deck_name: deckName,
            session_id: sessionId,
          })
        );
        await newRoom.localParticipant.publishData(initData, {
          reliable: true,
          topic: "user-input",
        });
        console.log("[connect] Sent init_session:", { deckName, sessionId });
      }
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : "Failed to connect";
      setState((prev) => ({
        ...prev,
        status: "error",
        error: errorMessage,
      }));
    }
  }, []);

  const disconnect = useCallback(() => {
    if (room) {
      room.disconnect();
      setRoom(null);
    }

    // Reset deduplication for next session (start fresh)
    resetDeduplication();

    // Reset audio state for next session
    resetAudioState();

    // Reset PTT refs
    pttModeRef.current = false;
    pttRecordingRef.current = false;

    setState({
      status: "disconnected",
      isSpeaking: false,
      isProcessing: false,
      isAgentSpeaking: false,
      currentCard: null,
      transcript: "",
      error: null,
      isMuted: false,
      agentMessage: null,
      lastVoiceTranscript: null,
      progress: null,
      isComplete: false,
      sessionStats: null,
      lastRating: null,
      isPTTMode: false,
      isPTTRecording: false,
      showingResult: false,
      cardBack: null,
      lastFeedback: null,
    });
  }, [room]);

  const setMuted = useCallback(
    (muted: boolean) => {
      if (room?.localParticipant) {
        room.localParticipant.setMicrophoneEnabled(!muted);
        setState((prev) => ({ ...prev, isMuted: muted }));
      }
    },
    [room]
  );

  // Enable microphone on existing connection (for upgrading text-only to voice)
  const enableMicrophone = useCallback(async () => {
    if (room?.localParticipant) {
      await room.localParticipant.setMicrophoneEnabled(true);
      setState((prev) => ({ ...prev, isMuted: false }));
    }
  }, [room]);

  const sendTextInput = useCallback(
    async (text: string) => {
      if (!room?.localParticipant) return;

      const encoder = new TextEncoder();
      const data = encoder.encode(
        JSON.stringify({
          type: "user_text_input",
          text: text,
        })
      );

      await room.localParticipant.publishData(data, {
        reliable: true,
        topic: "user-input",
      });
    },
    [room]
  );

  // Send a question in question mode (doesn't affect rating)
  const sendQuestion = useCallback(
    async (question: string) => {
      if (!room?.localParticipant) return;

      const encoder = new TextEncoder();
      const data = encoder.encode(
        JSON.stringify({
          type: "user_question",
          text: question,
        })
      );

      await room.localParticipant.publishData(data, {
        reliable: true,
        topic: "user-input",
      });
    },
    [room]
  );

  // PTT: Start recording (user presses button)
  const startPTT = useCallback(async () => {
    console.log("[PTT] startPTT called, room:", room, "localParticipant:", room?.localParticipant);

    // Unlock audio on first user gesture (fixes browser autoplay policy)
    await retryPendingAudio();

    if (!room?.localParticipant) {
      console.error("[PTT] Cannot start PTT - room or localParticipant not available");
      return;
    }

    // Enable microphone if not already enabled (for text-mode connections)
    // This publishes the audio track to LiveKit so backend can receive it
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
      console.log("[PTT] Microphone enabled");
    } catch (err) {
      console.error("[PTT] Failed to enable microphone:", err);
      return;
    }

    const encoder = new TextEncoder();
    const data = encoder.encode(JSON.stringify({ type: "ptt_start" }));

    try {
      await room.localParticipant.publishData(data, {
        reliable: true,
        topic: "user-input",
      });
      console.log("[PTT] ptt_start message published");
    } catch (err) {
      console.error("[PTT] Failed to publish ptt_start:", err);
    }

    // Optimistically set recording state (backend will confirm)
    pttRecordingRef.current = true;  // Update ref for event handlers
    setState((prev) => ({ ...prev, isPTTRecording: true, isMuted: false }));
  }, [room]);

  // PTT: End recording and submit (user releases button)
  const endPTT = useCallback(async () => {
    console.log("[PTT] endPTT called, room:", room, "localParticipant:", room?.localParticipant);
    if (!room?.localParticipant) {
      console.error("[PTT] Cannot end PTT - room or localParticipant not available");
      return;
    }

    const encoder = new TextEncoder();
    const data = encoder.encode(JSON.stringify({ type: "ptt_end" }));

    try {
      await room.localParticipant.publishData(data, {
        reliable: true,
        topic: "user-input",
      });
      console.log("[PTT] ptt_end message published");
    } catch (err) {
      console.error("[PTT] Failed to publish ptt_end:", err);
    }

    // Disable microphone after PTT ends (save resources, backend processes buffered audio)
    try {
      await room.localParticipant.setMicrophoneEnabled(false);
      console.log("[PTT] Microphone disabled");
    } catch (err) {
      console.error("[PTT] Failed to disable microphone:", err);
    }

    // Optimistically set recording state (backend will confirm)
    pttRecordingRef.current = false;  // Update ref for event handlers
    setState((prev) => ({ ...prev, isPTTRecording: false, isMuted: true }));
  }, [room]);

  // PTT: Cancel recording (user moves mouse away or presses escape)
  const cancelPTT = useCallback(async () => {
    if (!room?.localParticipant) return;

    const encoder = new TextEncoder();
    const data = encoder.encode(JSON.stringify({ type: "ptt_cancel" }));

    await room.localParticipant.publishData(data, {
      reliable: true,
      topic: "user-input",
    });

    // Disable microphone after PTT cancel
    try {
      await room.localParticipant.setMicrophoneEnabled(false);
    } catch (err) {
      console.error("[PTT] Failed to disable microphone on cancel:", err);
    }

    // Optimistically set recording state (backend will confirm)
    pttRecordingRef.current = false;  // Update ref for event handlers
    setState((prev) => ({ ...prev, isPTTRecording: false, isMuted: true }));
  }, [room]);

  // Send hint request (UI button instead of voice command parsing)
  const sendHint = useCallback(async () => {
    if (!room?.localParticipant) return;

    const encoder = new TextEncoder();
    const data = encoder.encode(
      JSON.stringify({
        type: "hint",
      })
    );

    await room.localParticipant.publishData(data, {
      reliable: true,
      topic: "user-input",
    });
  }, [room]);

  // Send give up request (UI button instead of voice command parsing)
  const sendGiveUp = useCallback(async () => {
    if (!room?.localParticipant) return;

    const encoder = new TextEncoder();
    const data = encoder.encode(
      JSON.stringify({
        type: "give_up",
      })
    );

    await room.localParticipant.publishData(data, {
      reliable: true,
      topic: "user-input",
    });
  }, [room]);

  // Send mnemonic request (generates memory aid for current card)
  const sendMnemonicRequest = useCallback(async () => {
    if (!room?.localParticipant) return;

    const encoder = new TextEncoder();
    const data = encoder.encode(
      JSON.stringify({
        type: "mnemonic_request",
      })
    );

    await room.localParticipant.publishData(data, {
      reliable: true,
      topic: "user-input",
    });
  }, [room]);

  return {
    ...state,
    connect,
    disconnect,
    setMuted,
    enableMicrophone,
    sendTextInput,
    sendQuestion,
    sendHint,
    sendGiveUp,
    sendMnemonicRequest,
    startPTT,
    endPTT,
    cancelPTT,
  };
}
