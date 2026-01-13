"use client";

import { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { SessionSummary } from "../../components";
import { SessionStats } from "../../lib/api";

function CompleteContent() {
  const router = useRouter();
  // searchParams available for future use (e.g., deep linking)
  useSearchParams();

  // Get stats from sessionStorage (set by review page)
  const [stats, setStats] = useState<SessionStats | null>(null);

  useEffect(() => {
    // Try to get stats from sessionStorage
    const storedStats = sessionStorage.getItem("lastSessionStats");
    if (storedStats) {
      setStats(JSON.parse(storedStats));
      sessionStorage.removeItem("lastSessionStats");
    }
  }, []);

  const handleNewSession = () => {
    router.push("/");
  };

  // If no stats available, show basic completion
  if (!stats) {
    return (
      <main className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-lg p-8 max-w-md text-center">
          <div className="w-16 h-16 bg-green-100 dark:bg-green-900/30 rounded-full flex items-center justify-center mx-auto mb-6">
            <svg
              className="w-8 h-8 text-green-600 dark:text-green-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M5 13l4 4L19 7"
              />
            </svg>
          </div>

          <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-2">
            Session Complete!
          </h1>
          <p className="text-gray-600 dark:text-gray-400 mb-8">
            Great job on your review session.
          </p>

          <button
            onClick={handleNewSession}
            className="w-full px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition font-medium"
          >
            Start New Session
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <header className="bg-white dark:bg-gray-800 shadow-sm">
        <div className="max-w-4xl mx-auto px-4 py-6">
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
            Session Complete!
          </h1>
        </div>
      </header>

      {/* Content */}
      <div className="max-w-4xl mx-auto px-4 py-8">
        <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-lg p-8">
          {/* Success icon */}
          <div className="w-20 h-20 bg-green-100 dark:bg-green-900/30 rounded-full flex items-center justify-center mx-auto mb-8">
            <svg
              className="w-10 h-10 text-green-600 dark:text-green-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M5 13l4 4L19 7"
              />
            </svg>
          </div>

          {/* Stats */}
          <SessionSummary stats={stats} className="mb-8" />

          {/* Action */}
          <button
            onClick={handleNewSession}
            className="w-full px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition font-medium"
          >
            Start New Session
          </button>
        </div>
      </div>
    </main>
  );
}

export default function CompletePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
          <div className="animate-spin w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full" />
        </div>
      }
    >
      <CompleteContent />
    </Suspense>
  );
}
