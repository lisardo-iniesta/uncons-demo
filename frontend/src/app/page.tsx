"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { DeckCard } from "../components";
import { fetchDecks, Deck, ApiClientError } from "../lib/api";

type PageState = "loading" | "ready" | "error";

export default function Home() {
  const router = useRouter();
  const [state, setState] = useState<PageState>("loading");
  const [decks, setDecks] = useState<Deck[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadDecks() {
      try {
        const response = await fetchDecks();
        setDecks(response.decks);
        setState("ready");
      } catch (err) {
        const message =
          err instanceof ApiClientError
            ? err.code === "ANKI_UNAVAILABLE"
              ? "Anki is not available. Please start Anki and try again."
              : err.message
            : "Failed to load decks";
        setError(message);
        setState("error");
      }
    }

    loadDecks();
  }, []);

  const handleDeckSelect = (deckName: string) => {
    router.push(`/review?deck=${encodeURIComponent(deckName)}`);
  };

  return (
    <main className="min-h-screen bg-slate-900 text-slate-50">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/80 backdrop-blur-sm">
        <div className="max-w-4xl mx-auto px-4 py-6">
          <h1 className="text-3xl font-bold text-slate-50">
            UNCONS
          </h1>
          <p className="text-slate-400 mt-1">
            Voice-first AI tutor for Anki
          </p>
        </div>
      </header>

      {/* Content */}
      <div className="max-w-4xl mx-auto px-4 py-8">
        {state === "loading" && (
          <div className="flex items-center justify-center py-16">
            <div className="animate-spin w-8 h-8 border-4 border-cyan-500 border-t-transparent rounded-full" />
            <span className="ml-3 text-slate-400">
              Loading decks...
            </span>
          </div>
        )}

        {state === "error" && (
          <div className="glass-card p-6 text-center border border-red-500/30">
            <p className="text-red-400 mb-4">{error}</p>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition"
            >
              Retry
            </button>
          </div>
        )}

        {state === "ready" && (
          <>
            <h2 className="text-xl font-semibold text-slate-200 mb-6">
              Select a deck to review
            </h2>

            {decks.length === 0 ? (
              <div className="text-center py-16 text-slate-400">
                <p>No decks found.</p>
                <p className="text-sm mt-2">
                  Create some decks in Anki to get started.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {decks.map((deck) => (
                  <DeckCard
                    key={deck.name}
                    name={deck.name}
                    newCount={deck.new_count}
                    learnCount={deck.learn_count}
                    dueCount={deck.due_count}
                    onClick={() => handleDeckSelect(deck.name)}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
