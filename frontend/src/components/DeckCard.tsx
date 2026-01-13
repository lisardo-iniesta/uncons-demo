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
        relative p-6 rounded-2xl transition-all duration-200
        text-left w-full glass-card
        ${
          selected
            ? "border-cyan-500 ring-2 ring-cyan-500/30"
            : hasCards
              ? "hover:border-cyan-500/50 hover:shadow-lg hover:shadow-cyan-500/10"
              : "opacity-50 cursor-not-allowed"
        }
        ${className}
      `}
      data-testid="deck-card"
    >
      <h3 className="text-lg font-semibold text-slate-50 mb-2">
        {name}
      </h3>
      <p
        className={`text-sm ${
          hasCards
            ? "text-cyan-400"
            : "text-slate-500"
        }`}
      >
        {hasCards
          ? `${newCount} new | ${learnCount} learn | ${dueCount} due`
          : "No cards available"}
      </p>

      {/* Selection indicator */}
      {selected && (
        <div className="absolute top-3 right-3">
          <div className="w-6 h-6 rounded-full bg-cyan-500 flex items-center justify-center">
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
