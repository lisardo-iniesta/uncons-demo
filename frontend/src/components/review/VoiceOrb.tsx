"use client";

import { VoiceSessionStatus } from "@/hooks/useVoiceSession";

interface VoiceOrbProps {
  /** Current connection status */
  status: VoiceSessionStatus;
  /** User is currently speaking/recording */
  isSpeaking?: boolean;
  /** AI is processing the answer */
  isProcessing?: boolean;
  /** Agent is speaking (TTS playing) */
  isAgentSpeaking?: boolean;
  /** Callback when orb is clicked (used for manual start) */
  onClick?: () => void;
  /** Size of the orb in pixels */
  size?: number;
}

type OrbState = "idle" | "connecting" | "listening" | "processing" | "speaking" | "error";

interface OrbConfig {
  outerColor: string;
  innerColor: string;
  glowClass: string;
  animation: string;
  label: string;
}

const ORB_CONFIGS: Record<OrbState, OrbConfig> = {
  idle: {
    outerColor: "bg-slate-700",
    innerColor: "bg-slate-400",
    glowClass: "",
    animation: "",
    label: "Tap to start",
  },
  connecting: {
    outerColor: "bg-cyan-900/50",
    innerColor: "bg-cyan-500",
    glowClass: "",
    animation: "animate-pulse",
    label: "Connecting...",
  },
  listening: {
    outerColor: "bg-cyan-900/30",
    innerColor: "bg-cyan-500",
    glowClass: "orb-glow-cyan",
    animation: "animate-pulse-glow",
    label: "Listening...",
  },
  processing: {
    outerColor: "bg-violet-900/30",
    innerColor: "bg-gradient-to-r from-violet-500 via-cyan-500 to-violet-500",
    glowClass: "orb-glow-violet",
    animation: "animate-spin-slow",
    label: "Thinking...",
  },
  speaking: {
    outerColor: "bg-emerald-900/30",
    innerColor: "bg-emerald-500",
    glowClass: "orb-glow-emerald",
    animation: "",
    label: "Speaking...",
  },
  error: {
    outerColor: "bg-red-900/30",
    innerColor: "bg-red-500",
    glowClass: "",
    animation: "animate-pulse",
    label: "Error",
  },
};

/**
 * Animated voice orb indicator.
 *
 * States:
 * - idle/disconnected: Slate gray, static
 * - connecting: Cyan, pulsing
 * - listening: Cyan with glow, expanding rings
 * - processing: Violet gradient, rotating
 * - speaking: Emerald with wave animation
 * - error: Red, pulsing
 */
export function VoiceOrb({
  status,
  isSpeaking = false,
  isProcessing = false,
  isAgentSpeaking = false,
  onClick,
  size = 120,
}: VoiceOrbProps) {
  // Determine current orb state
  const getOrbState = (): OrbState => {
    if (status === "error") return "error";
    if (status === "disconnected") return "idle";
    if (status === "connecting") return "connecting";
    if (isProcessing) return "processing";
    if (isSpeaking) return "listening";
    if (isAgentSpeaking) return "speaking";
    return "idle";
  };

  const orbState = getOrbState();
  const config = ORB_CONFIGS[orbState];
  const isClickable = status === "disconnected" && onClick;

  // Calculate sizes
  const innerSize = size * 0.67; // 80px for 120px orb
  const waveBarArea = size * 0.5;

  return (
    <div
      className={`flex flex-col items-center gap-3 ${isClickable ? "cursor-pointer" : ""}`}
      onClick={isClickable ? onClick : undefined}
      role={isClickable ? "button" : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onKeyDown={
        isClickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick?.();
              }
            }
          : undefined
      }
    >
      {/* Orb container */}
      <div
        className={`relative ${isClickable ? "hover:scale-105 transition-transform" : ""}`}
        style={{ width: size, height: size }}
      >
        {/* Outer glow ring */}
        <div
          className={`absolute inset-0 rounded-full transition-all duration-300 ${config.outerColor} ${config.glowClass}`}
        />

        {/* Expanding ring effect for listening state */}
        {orbState === "listening" && (
          <>
            <div
              className="absolute inset-0 rounded-full bg-cyan-500/30 animate-expanding-ring"
              style={{ animationDelay: "0s" }}
            />
            <div
              className="absolute inset-0 rounded-full bg-cyan-500/20 animate-expanding-ring"
              style={{ animationDelay: "0.5s" }}
            />
          </>
        )}

        {/* Inner orb */}
        <div
          className={`absolute rounded-full transition-all duration-300 ${config.innerColor} ${config.animation}`}
          style={{
            width: innerSize,
            height: innerSize,
            top: (size - innerSize) / 2,
            left: (size - innerSize) / 2,
          }}
        />

        {/* Waveform animation for speaking state */}
        {orbState === "speaking" && (
          <div
            className="absolute flex items-center justify-center gap-1"
            style={{
              width: waveBarArea,
              height: waveBarArea,
              top: (size - waveBarArea) / 2,
              left: (size - waveBarArea) / 2,
            }}
          >
            {[0, 1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className="w-1 bg-white rounded-full animate-wave"
                style={{
                  height: 12 + Math.random() * 8,
                  animationDelay: `${i * 0.1}s`,
                }}
              />
            ))}
          </div>
        )}

        {/* Processing spinner overlay */}
        {orbState === "processing" && (
          <div
            className="absolute flex items-center justify-center"
            style={{
              width: innerSize,
              height: innerSize,
              top: (size - innerSize) / 2,
              left: (size - innerSize) / 2,
            }}
          >
            <div
              className="border-4 border-white/30 border-t-white rounded-full animate-spin"
              style={{ width: innerSize * 0.6, height: innerSize * 0.6 }}
            />
          </div>
        )}
      </div>

      {/* Status label */}
      <span
        className="text-sm font-medium text-slate-300"
        data-testid="voice-status"
      >
        {config.label}
      </span>
    </div>
  );
}
