/**
 * DeckCard Component
 *
 * Displays a deck with its card counts for selection.
 */

interface DeckCardProps {
  /** Deck name */
  name: string;
  /** Number of new cards */
  newCount: number;
  /** Number of cards in learning */
  learnCount: number;
  /** Number of cards due for review */
  dueCount: number;
  /** Click handler */
  onClick?: () => void;
  /** Whether this deck is selected */
  selected?: boolean;
  /** Additional CSS classes */
  className?: string;
}

/**
 * Card displaying a deck for selection.
 */
export function DeckCard({
  name,
  newCount,
  learnCount,
  dueCount,
  onClick,
  selected = false,
  className = "",
}: DeckCardProps) {
  const totalCount = newCount + learnCount + dueCount;
  const hasCards = totalCount > 0;

  return (
    <button
      onClick={onClick}
      disabled={!hasCards}
      className={`
        relative p-6 rounded-xl border-2 transition-all duration-200
        text-left w-full
        ${
          selected
            ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
            : hasCards
              ? "border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-700 hover:shadow-md"
              : "border-gray-100 dark:border-gray-800 opacity-50 cursor-not-allowed"
        }
        ${className}
      `}
      data-testid="deck-card"
    >
      <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
        {name}
      </h3>
      <p
        className={`text-sm ${
          hasCards
            ? "text-blue-600 dark:text-blue-400"
            : "text-gray-400 dark:text-gray-600"
        }`}
      >
        {hasCards
          ? `${newCount} new | ${learnCount} learn | ${dueCount} due`
          : "No cards available"}
      </p>

      {/* Selection indicator */}
      {selected && (
        <div className="absolute top-3 right-3">
          <div className="w-6 h-6 rounded-full bg-blue-500 flex items-center justify-center">
            <svg
              className="w-4 h-4 text-white"
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
        </div>
      )}
    </button>
  );
}
