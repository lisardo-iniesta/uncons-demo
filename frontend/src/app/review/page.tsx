"use client";

import { useEffect, useCallback, Suspense, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useSession, useVoiceSession } from "../../hooks";
import { getLiveKitToken } from "../../lib/api";
import {
  ReviewLayout,
  LeftSidebar,
  RightSidebar,
  GlassCard,
  VoiceOrb,
  ProgressRing,
  ActionButtons,
  ControlButtons,
  SessionComplete,
} from "../../components/review";

// Feature flag for text input mode (for testing without speaking)
const TEXT_MODE_ENABLED = process.env.NEXT_PUBLIC_ENABLE_TEXT_MODE === "true";

interface TextMessage {
  role: "user" | "agent";
  text: string;
  source?: "voice" | "text";
}

function ReviewContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const deckName = searchParams.get("deck");
  const textModeOnly = searchParams.get("textModeOnly") === "true";

  const session = useSession();
  const voice = useVoiceSession();

  // Text mode state
  const [textInput, setTextInput] = useState("");
  const [isTextPanelCollapsed, setIsTextPanelCollapsed] = useState(false);
  const [messages, setMessages] = useState<TextMessage[]>([]);
  const [isTextModeConnecting, setIsTextModeConnecting] = useState(false);

  // Question mode state
  const [isQuestionMode, setIsQuestionMode] = useState(false);

  // Session complete state (for in-page celebration)
  const [showSessionComplete, setShowSessionComplete] = useState(false);
  const [sessionCompleteStats, setSessionCompleteStats] = useState<{
    cardsReviewed: number;
    ratingDistribution: { again: number; hard: number; good: number; easy: number };
    durationMinutes?: number;
    syncedCount?: number;
    failedCount?: number;
  } | null>(null);

  // Latency tracking (placeholder for future implementation)
  const lastLatency: number | null = null;

  // Start session on mount
  useEffect(() => {
    async function initSession() {
      if (deckName && session.state === "idle") {
        const recovered = await session.recover();
        if (!recovered) {
          session.start(deckName);
        }
      }
    }
    initSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deckName]);

  // Manual voice connection handler
  const handleStartVoice = useCallback(async () => {
    if (session.state === "active" && session.sessionId) {
      if (voice.status === "connected") {
        await voice.enableMicrophone();
        return;
      }

      if (voice.status === "disconnected") {
        try {
          const { token } = await getLiveKitToken(
            `session-${session.sessionId}`,
            "user",
            deckName || undefined,
            "push_to_talk"
          );
          await voice.connect(token, {
            enableMicrophone: !textModeOnly,
            pttMode: true,
            deckName: deckName || undefined,
            sessionId: session.sessionId,
          });
        } catch (err) {
          console.error("Failed to connect to voice:", err);
        }
      }
    }
  }, [session.state, session.sessionId, voice, deckName, textModeOnly]);

  // Track navigation state
  const isNavigatingRef = useRef(false);
  const disconnectRef = useRef(voice.disconnect);
  disconnectRef.current = voice.disconnect;
  const completionTriggeredRef = useRef(false);

  // Handle session completion - prepare stats when voice signals complete
  useEffect(() => {
    if (voice.isComplete && session.sessionId && !completionTriggeredRef.current) {
      completionTriggeredRef.current = true;

      // Prepare stats for celebration
      if (voice.sessionStats) {
        const ratings = voice.sessionStats.ratingDistribution;
        setSessionCompleteStats({
          cardsReviewed: voice.sessionStats.cardsReviewed,
          ratingDistribution: {
            again: ratings["again"] ?? ratings["1"] ?? 0,
            hard: ratings["hard"] ?? ratings["2"] ?? 0,
            good: ratings["good"] ?? ratings["3"] ?? 0,
            easy: ratings["easy"] ?? ratings["4"] ?? 0,
          },
          durationMinutes: voice.sessionStats.durationMinutes,
          syncedCount: voice.sessionStats.syncedCount,
          failedCount: voice.sessionStats.failedCount,
        });
      }
    }
  }, [voice.isComplete, voice.sessionStats, session.sessionId]);

  // Show celebration when agent stops speaking after completion
  useEffect(() => {
    if (completionTriggeredRef.current && !voice.isAgentSpeaking && !isNavigatingRef.current) {
      isNavigatingRef.current = true;
      // Small delay for audio to fully finish, then show celebration
      const timer = setTimeout(() => {
        disconnectRef.current();
        setShowSessionComplete(true);
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [voice.isAgentSpeaking]);

  // API-triggered completion (fallback)
  useEffect(() => {
    if (session.state === "complete" && session.sessionId && !isNavigatingRef.current) {
      isNavigatingRef.current = true;
      if (session.stats) {
        sessionStorage.setItem("lastSessionStats", JSON.stringify(session.stats));
      }
      router.push(`/complete?session_id=${session.sessionId}`);
    }
  }, [session.state, session.sessionId, session.stats, router]);

  const handleEndSession = useCallback(async () => {
    voice.disconnect();
    await session.end();
  }, [voice, session]);

  // Auto-connect text mode on session start
  useEffect(() => {
    async function autoOpenTextMode() {
      if (session.state === "active" && voice.status === "disconnected" && session.sessionId) {
        setIsTextModeConnecting(true);
        try {
          const { token } = await getLiveKitToken(
            `session-${session.sessionId}`,
            "user",
            deckName || undefined,
            "push_to_talk"
          );
          await voice.connect(token, {
            enableMicrophone: false,
            pttMode: true,
            deckName: deckName || undefined,
            sessionId: session.sessionId,
          });
        } catch (err) {
          console.error("Failed to auto-connect for text mode:", err);
        } finally {
          setIsTextModeConnecting(false);
        }
      }
    }
    autoOpenTextMode();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.state, session.sessionId]);

  // Listen for agent messages
  useEffect(() => {
    if (voice.agentMessage) {
      setMessages((prev) => [...prev, { role: "agent", text: voice.agentMessage! }]);
    }
  }, [voice.agentMessage]);

  // Listen for voice transcripts
  useEffect(() => {
    console.log("[page.tsx] voice.lastVoiceTranscript effect fired:", voice.lastVoiceTranscript);
    if (voice.lastVoiceTranscript) {
      console.log("[page.tsx] Adding voice transcript to messages:", voice.lastVoiceTranscript);
      setMessages((prev) => [
        ...prev,
        { role: "user", text: voice.lastVoiceTranscript!, source: "voice" },
      ]);
    }
  }, [voice.lastVoiceTranscript]);

  // Spacebar PTT handling
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const isInputFocused = target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable;

      if (e.code === "Space" && voice.status === "connected" && !isInputFocused && !voice.isPTTRecording) {
        e.preventDefault();
        voice.startPTT();
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const isInputFocused = target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable;

      if (e.code === "Space" && voice.isPTTRecording && !isInputFocused) {
        e.preventDefault();
        voice.endPTT();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
    // Only re-run when specific voice properties change (more precise than whole object)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voice.status, voice.isPTTRecording, voice.startPTT, voice.endPTT]);

  // Text input submission
  const handleTextSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!textInput.trim()) return;

      setMessages((prev) => [...prev, { role: "user", text: textInput, source: "text" }]);

      if (isQuestionMode || voice.showingResult) {
        await voice.sendQuestion(textInput);
      } else {
        await voice.sendTextInput(textInput);
      }
      setTextInput("");
    },
    [textInput, voice, isQuestionMode]
  );

  // Action handlers
  const handleNext = useCallback(async () => {
    setMessages((prev) => [...prev, { role: "user", text: "Next Card", source: "text" }]);
    await voice.sendTextInput("next");
    setIsQuestionMode(false);
  }, [voice]);

  const handleExplainMore = useCallback(async () => {
    setIsQuestionMode(true);
    setIsTextPanelCollapsed(false);
    setMessages((prev) => [...prev, { role: "user", text: "Explain more", source: "text" }]);
    await voice.sendQuestion("Explain more about this concept");
  }, [voice]);

  const handleGiveExample = useCallback(async () => {
    setIsQuestionMode(true);
    setIsTextPanelCollapsed(false);
    setMessages((prev) => [...prev, { role: "user", text: "Give example", source: "text" }]);
    await voice.sendQuestion("Give me a practical example");
  }, [voice]);

  const handleMnemonic = useCallback(async () => {
    setMessages((prev) => [...prev, { role: "user", text: "Mnemonic", source: "text" }]);
    await voice.sendMnemonicRequest();
  }, [voice]);

  const handleHint = useCallback(async () => {
    setMessages((prev) => [...prev, { role: "user", text: "Hint", source: "text" }]);
    await voice.sendHint();
  }, [voice]);

  const handleGiveUp = useCallback(async () => {
    setMessages((prev) => [...prev, { role: "user", text: "Show Answer", source: "text" }]);
    await voice.sendGiveUp();
  }, [voice]);

  const handleExitQuestionMode = useCallback(() => {
    setIsQuestionMode(false);
  }, []);

  const handlePTTToggle = useCallback(() => {
    if (voice.isPTTRecording) {
      voice.endPTT();
    } else {
      voice.startPTT();
    }
  }, [voice]);

  const handleSessionCompleteClose = useCallback(() => {
    // Navigate to complete page or home
    if (session.sessionId) {
      if (sessionCompleteStats) {
        sessionStorage.setItem("lastSessionStats", JSON.stringify({
          cards_reviewed: sessionCompleteStats.cardsReviewed,
          ratings: sessionCompleteStats.ratingDistribution,
          duration_minutes: sessionCompleteStats.durationMinutes,
          synced_count: sessionCompleteStats.syncedCount,
          failed_count: sessionCompleteStats.failedCount,
        }));
      }
      router.push(`/complete?session_id=${session.sessionId}`);
    } else {
      router.push("/");
    }
  }, [router, session.sessionId, sessionCompleteStats]);

  // No deck specified
  if (!deckName) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-400 mb-4">No deck selected</p>
          <button
            onClick={() => router.push("/")}
            className="px-4 py-2 bg-cyan-600 text-white rounded-lg hover:bg-cyan-500 transition-colors"
          >
            Select a deck
          </button>
        </div>
      </div>
    );
  }

  // Loading state
  if (session.state === "loading") {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin w-12 h-12 border-4 border-cyan-500 border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-slate-400">Starting session...</p>
        </div>
      </div>
    );
  }

  // Error state
  if (session.state === "error") {
    const isSessionConflict = session.error?.includes("Another session");

    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="glass-card p-8 text-center max-w-md">
          <p className="text-red-400 mb-4">{session.error}</p>
          <div className="space-x-4">
            <button
              onClick={() => router.push("/")}
              className="px-4 py-2 bg-slate-700 text-slate-200 rounded-lg hover:bg-slate-600 transition-colors"
            >
              Back to decks
            </button>
            {isSessionConflict ? (
              <button
                onClick={async () => {
                  await session.forceEnd();
                  session.start(deckName);
                }}
                className="px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-500 transition-colors"
              >
                Force Clear & Retry
              </button>
            ) : (
              <button
                onClick={() => session.start(deckName)}
                className="px-4 py-2 bg-cyan-600 text-white rounded-lg hover:bg-cyan-500 transition-colors"
              >
                Retry
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Calculate progress
  const cardsReviewed = voice.progress?.cardsReviewed ?? (session.cards.length - session.remaining);
  const totalCards = voice.progress ? (voice.progress.cardsReviewed + voice.progress.cardsRemaining) : session.cards.length;
  const currentCard = voice.currentCard || session.currentCard;

  // Active review
  return (
    <ReviewLayout
      deckName={deckName}
      leftSidebar={
        <LeftSidebar
          voiceOrb={
            <VoiceOrb
              status={voice.status}
              isSpeaking={voice.isSpeaking || voice.isPTTRecording}
              isProcessing={voice.isProcessing}
              isAgentSpeaking={voice.isAgentSpeaking}
              onClick={handleStartVoice}
            />
          }
          progressRing={
            <ProgressRing
              cardsReviewed={cardsReviewed}
              totalCards={totalCards}
            />
          }
          controls={
            <ControlButtons
              isPTTRecording={voice.isPTTRecording}
              isConnected={voice.status === "connected"}
              onPTTToggle={handlePTTToggle}
              onEndSession={handleEndSession}
            />
          }
          latency={lastLatency}
        />
      }
      rightSidebar={
        TEXT_MODE_ENABLED ? (
          <RightSidebar
            isCollapsed={isTextPanelCollapsed}
            onToggleCollapse={() => setIsTextPanelCollapsed(!isTextPanelCollapsed)}
            messages={messages}
            inputValue={textInput}
            onInputChange={setTextInput}
            onSubmit={handleTextSubmit}
            inputDisabled={voice.status !== "connected"}
            placeholder={
              voice.status !== "connected"
                ? "Connecting..."
                : isQuestionMode
                  ? "Ask about this card..."
                  : voice.showingResult
                    ? "Ask a follow-up question..."
                    : "Type your answer..."
            }
            isQuestionMode={isQuestionMode}
            onExitQuestionMode={handleExitQuestionMode}
            connectionStatus={isTextModeConnecting ? "Connecting..." : undefined}
          />
        ) : null
      }
      rightSidebarCollapsed={!TEXT_MODE_ENABLED || isTextPanelCollapsed}
    >
      {/* Main card area */}
      {currentCard ? (
        <>
          <GlassCard
            question={currentCard.question_html}
            answer={voice.showingResult && voice.cardBack ? voice.cardBack : currentCard.answer_html}
            imageUrl={currentCard.image_url}
            showAnswer={voice.showingResult}
            rating={voice.showingResult ? voice.lastRating : null}
            isProcessing={voice.isProcessing}
            cardKey={currentCard.id || currentCard.question_html}
            actionButtons={
              voice.showingResult ? (
                <ActionButtons
                  showPostAnswer
                  onExplainMore={handleExplainMore}
                  onGiveExample={handleGiveExample}
                  onMnemonic={handleMnemonic}
                  onNext={handleNext}
                />
              ) : null
            }
          />

          {/* Pre-answer buttons (Hint, Give Up) */}
          {voice.status === "connected" && !voice.showingResult && (
            <div className="mt-6">
              <ActionButtons
                showPreAnswer
                onHint={handleHint}
                onGiveUp={handleGiveUp}
              />
            </div>
          )}
        </>
      ) : (
        <div className="glass-card p-8 text-center text-slate-400">
          No more cards
        </div>
      )}

      {/* Session complete celebration overlay */}
      {showSessionComplete && sessionCompleteStats && (
        <SessionComplete
          stats={sessionCompleteStats}
          onClose={handleSessionCompleteClose}
        />
      )}
    </ReviewLayout>
  );
}

export default function ReviewPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-slate-900 flex items-center justify-center">
          <div className="animate-spin w-12 h-12 border-4 border-cyan-500 border-t-transparent rounded-full" />
        </div>
      }
    >
      <ReviewContent />
    </Suspense>
  );
}
