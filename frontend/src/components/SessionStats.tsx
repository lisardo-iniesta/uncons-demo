/**
 * SessionStats Component
 *
 * Displays session progress and statistics.
 */

import { SessionStats as SessionStatsData } from "../lib/api";

interface SessionStatsProps {
  /** Number of cards reviewed */
  cardsReviewed: number;
  /** Total cards in session */
  totalCards: number;
  /** Progress as fraction (0-1) */
  progress?: number;
  /** Additional CSS classes */
  className?: string;
}

/**
 * Progress bar showing session progress.
 */
export function SessionProgress({
  cardsReviewed,
  totalCards,
  progress,
  className = "",
}: SessionStatsProps) {
  const percent = progress !== undefined ? progress * 100 : (cardsReviewed / totalCards) * 100;

  return (
    <div className={`${className}`} data-testid="session-progress">
      <div className="flex justify-between text-sm text-gray-600 dark:text-gray-400 mb-2">
        <span>{cardsReviewed} reviewed</span>
        <span>{totalCards - cardsReviewed} remaining</span>
      </div>
      <div className="h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

interface SessionSummaryProps {
  /** Session statistics */
  stats: SessionStatsData;
  /** Additional CSS classes */
  className?: string;
}

/**
 * Session summary after completion.
 */
export function SessionSummary({ stats, className = "" }: SessionSummaryProps) {
  const ratingColors: Record<string, string> = {
    again: "bg-red-500",
    hard: "bg-orange-500",
    good: "bg-green-500",
    easy: "bg-blue-500",
  };

  const ratingLabels: Record<string, string> = {
    again: "Again",
    hard: "Hard",
    good: "Good",
    easy: "Easy",
  };

  const maxRating = Math.max(...Object.values(stats.ratings), 1);

  return (
    <div className={`${className}`} data-testid="session-summary">
      {/* Overview */}
      <div className="grid grid-cols-2 gap-4 mb-8">
        <div className="bg-gray-100 dark:bg-gray-800 rounded-lg p-4 text-center">
          <p className="text-3xl font-bold text-gray-900 dark:text-white">
            {stats.cards_reviewed}
          </p>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Cards Reviewed
          </p>
        </div>
        <div className="bg-gray-100 dark:bg-gray-800 rounded-lg p-4 text-center">
          <p className="text-3xl font-bold text-gray-900 dark:text-white">
            {(stats.duration_minutes ?? 0).toFixed(1)}
          </p>
          <p className="text-sm text-gray-500 dark:text-gray-400">Minutes</p>
        </div>
      </div>

      {/* Rating distribution */}
      <div className="space-y-3">
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-4">
          Rating Distribution
        </h3>
        {["easy", "good", "hard", "again"].map((rating) => {
          const count = stats.ratings[rating] || 0;
          const percent = (count / maxRating) * 100;

          return (
            <div key={rating} className="flex items-center gap-3">
              <span className="w-12 text-sm text-gray-600 dark:text-gray-400">
                {ratingLabels[rating]}
              </span>
              <div className="flex-1 h-6 bg-gray-200 dark:bg-gray-700 rounded">
                <div
                  className={`h-full ${ratingColors[rating]} rounded transition-all duration-500`}
                  style={{ width: `${percent}%` }}
                />
              </div>
              <span className="w-8 text-sm text-gray-600 dark:text-gray-400 text-right">
                {count}
              </span>
            </div>
          );
        })}
      </div>

      {/* Sync status */}
      <div className="mt-8 pt-4 border-t border-gray-200 dark:border-gray-700">
        <div className="flex justify-between text-sm">
          <span className="text-gray-500 dark:text-gray-400">
            Synced to Anki
          </span>
          <span
            className={
              (stats.synced_count ?? stats.cards_reviewed) === stats.cards_reviewed
                ? "text-green-600 dark:text-green-400"
                : "text-orange-600 dark:text-orange-400"
            }
          >
            {stats.synced_count ?? stats.cards_reviewed}/{stats.cards_reviewed}
          </span>
        </div>
        {(stats.failed_count ?? 0) > 0 && (
          <p className="text-sm text-orange-600 dark:text-orange-400 mt-1">
            {stats.failed_count} ratings failed to sync
          </p>
        )}
      </div>
    </div>
  );
}
