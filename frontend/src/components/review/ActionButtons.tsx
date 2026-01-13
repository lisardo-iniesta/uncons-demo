"use client";

import * as Tooltip from "@radix-ui/react-tooltip";
import {
  MessageCircleQuestion,
  Lightbulb,
  Brain,
  ArrowRight,
  Sparkles,
  Flag,
} from "lucide-react";

interface ActionButtonsProps {
  /** Show post-answer buttons (Explain, Example, Mnemonic, Next) */
  showPostAnswer?: boolean;
  /** Show pre-answer buttons (Hint, Give Up) */
  showPreAnswer?: boolean;
  /** Handlers */
  onExplainMore?: () => void;
  onGiveExample?: () => void;
  onMnemonic?: () => void;
  onNext?: () => void;
  onHint?: () => void;
  onGiveUp?: () => void;
}

interface IconButtonProps {
  icon: React.ReactNode;
  tooltip: string;
  onClick?: () => void;
  variant?: "default" | "primary" | "muted";
  testId?: string;
}

/**
 * Icon-only button with tooltip
 */
function IconButton({ icon, tooltip, onClick, variant = "default", testId }: IconButtonProps) {
  const variantStyles = {
    default: "bg-slate-700/50 hover:bg-slate-600/50 text-slate-300 hover:text-slate-100",
    primary: "bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-400 hover:text-cyan-300",
    muted: "bg-slate-700/30 hover:bg-slate-600/30 text-slate-400 hover:text-slate-300",
  };

  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button
            onClick={onClick}
            className={`p-3 rounded-xl transition-all duration-200 hover:scale-105 active:scale-95 ${variantStyles[variant]}`}
            data-testid={testId}
          >
            {icon}
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="bg-slate-800 text-slate-200 text-sm px-3 py-1.5 rounded-lg shadow-lg border border-slate-700/50 z-50"
            sideOffset={8}
          >
            {tooltip}
            <Tooltip.Arrow className="fill-slate-800" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

/**
 * Action buttons for the flashcard.
 *
 * Post-answer buttons: Explain, Example, Mnemonic, Next
 * Pre-answer buttons: Hint, Give Up
 */
export function ActionButtons({
  showPostAnswer = false,
  showPreAnswer = false,
  onExplainMore,
  onGiveExample,
  onMnemonic,
  onNext,
  onHint,
  onGiveUp,
}: ActionButtonsProps) {
  if (showPostAnswer) {
    return (
      <div className="flex items-center justify-center gap-3 flex-wrap">
        {onExplainMore && (
          <IconButton
            icon={<MessageCircleQuestion className="w-5 h-5" />}
            tooltip="Explain more"
            onClick={onExplainMore}
            testId="explain-more-button"
          />
        )}
        {onGiveExample && (
          <IconButton
            icon={<Lightbulb className="w-5 h-5" />}
            tooltip="Give example"
            onClick={onGiveExample}
            testId="give-example-button"
          />
        )}
        {onMnemonic && (
          <IconButton
            icon={<Brain className="w-5 h-5" />}
            tooltip="Mnemonic"
            onClick={onMnemonic}
            testId="mnemonic-button"
          />
        )}
        {onNext && (
          <IconButton
            icon={<ArrowRight className="w-5 h-5" />}
            tooltip="Next card"
            onClick={onNext}
            variant="primary"
            testId="next-button"
          />
        )}
      </div>
    );
  }

  if (showPreAnswer) {
    return (
      <div className="flex items-center justify-center gap-3">
        {onHint && (
          <IconButton
            icon={<Sparkles className="w-5 h-5" />}
            tooltip="Get hint"
            onClick={onHint}
            testId="hint-button"
          />
        )}
        {onGiveUp && (
          <IconButton
            icon={<Flag className="w-5 h-5" />}
            tooltip="Show answer"
            onClick={onGiveUp}
            variant="muted"
            testId="give-up-button"
          />
        )}
      </div>
    );
  }

  return null;
}

/**
 * Control buttons for the left sidebar (PTT, End Session)
 */
interface ControlButtonsProps {
  isPTTRecording: boolean;
  isConnected: boolean;
  onPTTToggle: () => void;
  onEndSession: () => void;
}

export function ControlButtons({
  isPTTRecording,
  isConnected,
  onPTTToggle,
  onEndSession,
}: ControlButtonsProps) {
  return (
    <div className="flex flex-col gap-3 w-full">
      {/* PTT Button */}
      <button
        onClick={onPTTToggle}
        disabled={!isConnected}
        className={`w-full py-3 rounded-xl font-medium transition-all duration-200 flex items-center justify-center gap-2 ${
          isPTTRecording
            ? "bg-red-500 text-white shadow-lg shadow-red-500/25 scale-[1.02]"
            : isConnected
              ? "bg-slate-700/50 text-slate-300 hover:bg-slate-600/50"
              : "bg-slate-800/50 text-slate-500 cursor-not-allowed"
        }`}
        data-testid="ptt-button"
      >
        {isPTTRecording ? (
          <>
            <span className="w-2 h-2 bg-white rounded-full animate-pulse" />
            Recording...
          </>
        ) : (
          <>
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
            Push to Talk
          </>
        )}
      </button>

      {/* Hint text */}
      {isConnected && (
        <p className="text-xs text-slate-500 text-center">
          {isPTTRecording ? "Tap to send" : "Hold Space or tap"}
        </p>
      )}

      {/* End Session Button */}
      <button
        onClick={onEndSession}
        className="w-full py-2.5 rounded-xl text-sm font-medium bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors"
      >
        End Session
      </button>
    </div>
  );
}
