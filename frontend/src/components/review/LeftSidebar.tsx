"use client";

import { ReactNode } from "react";

interface LeftSidebarProps {
  /** Voice orb component */
  voiceOrb: ReactNode;
  /** Progress ring component */
  progressRing: ReactNode;
  /** Control buttons (PTT, End) */
  controls: ReactNode;
  /** Optional latency display */
  latency?: number | null;
}

/**
 * Left sidebar for review page.
 * Contains voice orb, progress ring, latency display, and controls.
 */
export function LeftSidebar({
  voiceOrb,
  progressRing,
  controls,
  latency,
}: LeftSidebarProps) {
  // Latency color coding
  const getLatencyColor = (ms: number) => {
    if (ms < 800) return "text-emerald-400";
    if (ms < 1200) return "text-amber-400";
    return "text-red-400";
  };

  return (
    <div className="flex flex-col items-center h-full">
      {/* Voice Orb */}
      <div className="mb-6">
        {voiceOrb}
      </div>

      {/* Latency Display */}
      {latency !== null && latency !== undefined && (
        <div className={`mb-6 font-mono text-sm ${getLatencyColor(latency)}`}>
          ~{latency}ms
        </div>
      )}

      {/* Progress Ring */}
      <div className="mb-8">
        {progressRing}
      </div>

      {/* Spacer to push controls to bottom */}
      <div className="flex-1" />

      {/* Controls */}
      <div className="w-full">
        {controls}
      </div>
    </div>
  );
}
