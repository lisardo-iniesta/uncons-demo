"use client";

import { useRef, useEffect, FormEvent } from "react";
import { ChevronLeft, ChevronRight, Mic } from "lucide-react";

interface Message {
  role: "user" | "agent";
  text: string;
  source?: "voice" | "text";
}

interface RightSidebarProps {
  /** Whether the panel is collapsed */
  isCollapsed: boolean;
  /** Toggle collapsed state */
  onToggleCollapse: () => void;
  /** Chat messages */
  messages: Message[];
  /** Current input value */
  inputValue: string;
  /** Input change handler */
  onInputChange: (value: string) => void;
  /** Form submit handler */
  onSubmit: (e: FormEvent) => void;
  /** Whether input is disabled */
  inputDisabled?: boolean;
  /** Placeholder text */
  placeholder?: string;
  /** Whether in question mode */
  isQuestionMode?: boolean;
  /** Exit question mode handler */
  onExitQuestionMode?: () => void;
  /** Connection status text */
  connectionStatus?: string;
}

/**
 * Right sidebar for text mode conversation.
 * Collapsible panel with message transcript and text input.
 */
export function RightSidebar({
  isCollapsed,
  onToggleCollapse,
  messages,
  inputValue,
  onInputChange,
  onSubmit,
  inputDisabled = false,
  placeholder = "Type your answer...",
  isQuestionMode = false,
  onExitQuestionMode,
  connectionStatus,
}: RightSidebarProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (isCollapsed) {
    return (
      <div className="h-full flex flex-col items-center py-4">
        <button
          onClick={onToggleCollapse}
          className="p-2 rounded-lg hover:bg-slate-700/50 text-slate-400 hover:text-slate-200 transition-colors"
          title="Expand text mode"
        >
          <ChevronLeft className="w-5 h-5" />
        </button>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-slate-700/50">
        <span className="text-sm text-slate-400">
          {isQuestionMode ? "Question Mode" : "Conversation"}
        </span>
        <div className="flex items-center gap-2">
          {isQuestionMode && onExitQuestionMode && (
            <button
              onClick={onExitQuestionMode}
              className="text-xs px-2 py-1 bg-slate-700/50 text-slate-300 rounded hover:bg-slate-600/50 transition-colors"
              data-testid="question-mode-done"
            >
              Done
            </button>
          )}
          <button
            onClick={onToggleCollapse}
            className="p-1.5 rounded-lg hover:bg-slate-700/50 text-slate-400 hover:text-slate-200 transition-colors"
            title="Collapse panel"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div
        className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0 scrollbar-glow"
        data-testid="text-mode-transcript"
      >
        {messages.length === 0 && (
          <div className="text-slate-500 text-center py-8 text-sm">
            {connectionStatus || (isQuestionMode ? "Ask a question about this card" : "No messages yet")}
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={onSubmit} className="p-4 border-t border-slate-700/50">
        <div className="flex gap-2">
          <input
            value={inputValue}
            onChange={(e) => onInputChange(e.target.value)}
            placeholder={placeholder}
            disabled={inputDisabled}
            className="flex-1 bg-slate-700/50 border border-slate-600/50 rounded-lg px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-500/50 focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            data-testid="text-mode-input"
          />
          <button
            type="submit"
            disabled={inputDisabled || !inputValue.trim()}
            className="px-4 py-2.5 bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            data-testid="text-mode-send"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}

/**
 * Individual message bubble component
 */
function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const isVoice = message.source === "voice";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] px-3 py-2 rounded-xl text-sm ${
          isUser
            ? isVoice
              ? "bg-emerald-500/20 text-emerald-200 border border-emerald-500/30"
              : "bg-cyan-500/20 text-cyan-200 border border-cyan-500/30"
            : "bg-slate-700/50 text-slate-200 border border-slate-600/30"
        }`}
      >
        {isVoice && (
          <Mic className="inline-block w-3 h-3 mr-1.5 opacity-60" />
        )}
        {message.text}
      </div>
    </div>
  );
}
