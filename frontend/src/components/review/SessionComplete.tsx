"use client";

import { useEffect } from "react";
import { motion } from "framer-motion";
import confetti from "canvas-confetti";

interface SessionStats {
  cardsReviewed: number;
  ratingDistribution: {
    again: number;
    hard: number;
    good: number;
    easy: number;
  };
  durationMinutes?: number;
  syncedCount?: number;
  failedCount?: number;
}

interface SessionCompleteProps {
  stats: SessionStats;
  onClose?: () => void;
}

interface StatCardProps {
  label: string;
  value: number;
  color: "red" | "amber" | "green" | "blue";
}

const colorStyles = {
  red: "bg-red-500/20 text-red-300 border-red-500/30",
  amber: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  green: "bg-green-500/20 text-green-300 border-green-500/30",
  blue: "bg-blue-500/20 text-blue-300 border-blue-500/30",
};

function StatCard({ label, value, color }: StatCardProps) {
  return (
    <div className={`rounded-xl p-4 border ${colorStyles[color]} text-center`}>
      <div className="text-2xl font-bold font-mono">{value}</div>
      <div className="text-xs uppercase tracking-wider opacity-80">{label}</div>
    </div>
  );
}

/**
 * Session completion overlay with celebration animation.
 * Features:
 * - Confetti burst on mount
 * - Stats cards for rating distribution
 * - Fade-in animation
 */
export function SessionComplete({ stats, onClose }: SessionCompleteProps) {
  // Trigger confetti on mount
  useEffect(() => {
    // Fire confetti from both sides
    const duration = 2000;
    const end = Date.now() + duration;

    const frame = () => {
      confetti({
        particleCount: 3,
        angle: 60,
        spread: 55,
        origin: { x: 0, y: 0.7 },
        colors: ["#06b6d4", "#10b981", "#8b5cf6"],
      });
      confetti({
        particleCount: 3,
        angle: 120,
        spread: 55,
        origin: { x: 1, y: 0.7 },
        colors: ["#06b6d4", "#10b981", "#8b5cf6"],
      });

      if (Date.now() < end) {
        requestAnimationFrame(frame);
      }
    };

    // Initial burst
    confetti({
      particleCount: 100,
      spread: 70,
      origin: { y: 0.6 },
      colors: ["#06b6d4", "#10b981", "#8b5cf6", "#22d3ee"],
    });

    // Continuous smaller confetti
    frame();
  }, []);

  const formatDuration = (minutes?: number) => {
    if (!minutes) return null;
    if (minutes < 1) return "< 1 min";
    if (minutes < 60) return `${Math.round(minutes)} min`;
    const hours = Math.floor(minutes / 60);
    const mins = Math.round(minutes % 60);
    return `${hours}h ${mins}m`;
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="absolute inset-0 bg-slate-900/95 backdrop-blur-sm flex items-center justify-center z-50"
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.9, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ delay: 0.2, duration: 0.4 }}
        className="glass-card p-8 max-w-md w-full mx-4 text-center"
      >
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <h2 className="text-2xl font-bold text-slate-100 mb-2">
            Session Complete!
          </h2>
          <p className="text-slate-400 mb-6">
            Great work! Here&apos;s how you did.
          </p>
        </motion.div>

        {/* Total cards */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="mb-6"
        >
          <div className="text-5xl font-bold text-gradient mb-1">
            {stats.cardsReviewed}
          </div>
          <div className="text-sm text-slate-400 uppercase tracking-wider">
            Cards Reviewed
          </div>
        </motion.div>

        {/* Rating distribution */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6 }}
          className="grid grid-cols-4 gap-3 mb-6"
        >
          <StatCard label="Again" value={stats.ratingDistribution.again} color="red" />
          <StatCard label="Hard" value={stats.ratingDistribution.hard} color="amber" />
          <StatCard label="Good" value={stats.ratingDistribution.good} color="green" />
          <StatCard label="Easy" value={stats.ratingDistribution.easy} color="blue" />
        </motion.div>

        {/* Duration and sync info */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.7 }}
          className="text-sm text-slate-500 space-y-1 mb-6"
        >
          {stats.durationMinutes && (
            <p>Duration: {formatDuration(stats.durationMinutes)}</p>
          )}
          {stats.syncedCount !== undefined && (
            <p className="text-emerald-400">
              {stats.syncedCount} ratings synced to Anki
            </p>
          )}
          {stats.failedCount !== undefined && stats.failedCount > 0 && (
            <p className="text-amber-400">
              {stats.failedCount} failed to sync
            </p>
          )}
        </motion.div>

        {/* Close button */}
        {onClose && (
          <motion.button
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.8 }}
            onClick={onClose}
            className="w-full py-3 rounded-xl bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 transition-colors font-medium"
          >
            Continue
          </motion.button>
        )}
      </motion.div>
    </motion.div>
  );
}
