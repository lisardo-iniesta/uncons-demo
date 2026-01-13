"use client";

import { motion } from "framer-motion";

interface ProgressRingProps {
  /** Number of cards reviewed */
  cardsReviewed: number;
  /** Total number of cards in session */
  totalCards: number;
  /** Size of the ring in pixels */
  size?: number;
  /** Stroke width */
  strokeWidth?: number;
}

/**
 * Circular progress ring showing session progress.
 * Features:
 * - SVG circle with animated stroke-dashoffset
 * - Cards reviewed / total display in center
 * - Smooth transition on progress update
 */
export function ProgressRing({
  cardsReviewed,
  totalCards,
  size = 80,
  strokeWidth = 6,
}: ProgressRingProps) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = totalCards > 0 ? cardsReviewed / totalCards : 0;
  const strokeDashoffset = circumference * (1 - progress);

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative" style={{ width: size, height: size }}>
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="transform -rotate-90"
        >
          {/* Background circle */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke="currentColor"
            strokeWidth={strokeWidth}
            fill="none"
            className="text-slate-700"
          />
          {/* Progress circle */}
          <motion.circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke="currentColor"
            strokeWidth={strokeWidth}
            fill="none"
            strokeLinecap="round"
            className="text-cyan-500"
            initial={{ strokeDashoffset: circumference }}
            animate={{ strokeDashoffset }}
            transition={{ duration: 0.5, ease: "easeOut" }}
            style={{
              strokeDasharray: circumference,
            }}
          />
        </svg>

        {/* Center text */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-lg font-semibold text-slate-100 font-mono">
            {cardsReviewed}
          </span>
          <span className="text-xs text-slate-500">/ {totalCards}</span>
        </div>
      </div>

      {/* Label */}
      <span className="text-xs text-slate-500 uppercase tracking-wider">
        Progress
      </span>
    </div>
  );
}
