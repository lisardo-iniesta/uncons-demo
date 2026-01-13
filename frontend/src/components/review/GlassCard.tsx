"use client";

import { ReactNode, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Image from "next/image";
import DOMPurify from "dompurify";
import { formatCardContent } from "@/lib/formatCardContent";

// Configure DOMPurify once at module level
const PURIFY_CONFIG = {
  ALLOWED_TAGS: ['b', 'i', 'em', 'strong', 'a', 'p', 'br', 'ul', 'ol', 'li', 'code', 'pre', 'blockquote', 'span', 'div', 'img'],
  ALLOWED_ATTR: ['href', 'src', 'alt', 'class', 'style'],
  ALLOW_DATA_ATTR: false,
};

function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, PURIFY_CONFIG);
}

/** Calculate font sizes based on total content length (question + answer) */
function getAdaptiveFontSizes(questionLength: number, answerLength: number, showingAnswer: boolean): { question: string; answer: string } {
  if (!showingAnswer) {
    // Question only - moderate size, will be centered
    if (questionLength < 100) {
      return { question: "text-xl md:text-2xl leading-relaxed", answer: "" };
    }
    if (questionLength < 200) {
      return { question: "text-lg md:text-xl leading-relaxed", answer: "" };
    }
    return { question: "text-base md:text-lg leading-relaxed", answer: "" };
  }

  const totalLength = questionLength + answerLength;

  // Very short content - use large, comfortable fonts
  if (totalLength < 250) {
    return {
      question: "text-xl leading-relaxed",
      answer: "text-lg leading-relaxed"
    };
  }
  // Short content - generous sizing
  if (totalLength < 400) {
    return {
      question: "text-lg leading-relaxed",
      answer: "text-base leading-relaxed"
    };
  }
  // Medium content - balanced
  if (totalLength < 600) {
    return {
      question: "text-base leading-snug",
      answer: "text-base leading-normal"
    };
  }
  // Medium-long content
  if (totalLength < 900) {
    return {
      question: "text-base leading-snug",
      answer: "text-sm leading-normal"
    };
  }
  // Long content - start compressing
  if (totalLength < 1200) {
    return {
      question: "text-sm leading-snug",
      answer: "text-sm leading-normal"
    };
  }
  // Very long content
  if (totalLength < 1600) {
    return {
      question: "text-sm leading-tight",
      answer: "text-xs leading-normal"
    };
  }
  // Extremely long content
  if (totalLength < 2000) {
    return {
      question: "text-xs leading-tight",
      answer: "text-xs leading-tight"
    };
  }
  // Maximum compression for huge cards
  return {
    question: "text-xs leading-tight",
    answer: "text-[11px] leading-tight"
  };
}

interface GlassCardProps {
  /** Card question HTML */
  question: string;
  /** Card answer HTML (shown when showAnswer is true) */
  answer?: string;
  /** Image URL if card has image */
  imageUrl?: string | null;
  /** Whether to show the answer */
  showAnswer?: boolean;
  /** Rating (1-4) to display as badge and color wash */
  rating?: number | null;
  /** Action buttons to render */
  actionButtons?: ReactNode;
  /** Whether card is processing (skeleton state) */
  isProcessing?: boolean;
  /** Unique key for card transitions */
  cardKey?: string | number;
}

/** Rating labels and colors */
const RATING_CONFIG: Record<number, { label: string; bgColor: string; textColor: string; washClass: string }> = {
  1: { label: "Again", bgColor: "bg-red-500/20", textColor: "text-red-300", washClass: "rating-wash-again" },
  2: { label: "Hard", bgColor: "bg-amber-500/20", textColor: "text-amber-300", washClass: "rating-wash-hard" },
  3: { label: "Good", bgColor: "bg-green-500/20", textColor: "text-green-300", washClass: "rating-wash-good" },
  4: { label: "Easy", bgColor: "bg-blue-500/20", textColor: "text-blue-300", washClass: "rating-wash-easy" },
};

/**
 * Glassmorphism flashcard with animations.
 * Features:
 * - Glass background with blur
 * - Large, centered question typography
 * - Smooth answer reveal animation
 * - Rating color wash effect
 * - Slide + fade transitions between cards
 */
export function GlassCard({
  question,
  answer,
  imageUrl,
  showAnswer = false,
  rating,
  actionButtons,
  isProcessing = false,
  cardKey,
}: GlassCardProps) {
  const ratingConfig = rating ? RATING_CONFIG[rating] : null;

  // Memoize sanitized HTML to avoid re-sanitizing on every render
  const sanitizedQuestion = useMemo(() => sanitizeHtml(question), [question]);
  const sanitizedAnswer = useMemo(
    () => (answer ? sanitizeHtml(formatCardContent(answer)) : ""),
    [answer]
  );

  // Calculate content lengths for adaptive sizing
  const contentLengths = useMemo(() => {
    const questionText = question.replace(/<[^>]*>/g, "");
    const answerText = answer ? answer.replace(/<[^>]*>/g, "") : "";
    return { question: questionText.length, answer: answerText.length, total: questionText.length + answerText.length };
  }, [question, answer]);

  // Calculate adaptive font sizes based on total content length
  const fontSizes = useMemo(() => {
    return getAdaptiveFontSizes(contentLengths.question, contentLengths.answer, showAnswer);
  }, [contentLengths, showAnswer]);

  // Use compact mode for long content (reduced padding/margins)
  const isCompact = showAnswer && contentLengths.total > 1200;

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={cardKey}
        initial={{ opacity: 0, x: 50 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: -50 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="relative"
      >
        <div
          className={`glass-card relative ${isCompact ? 'p-4' : 'p-6'} ${!showAnswer ? 'min-h-[200px] flex flex-col justify-center' : ''}`}
          data-testid="card-display"
        >
          {/* Rating color wash overlay */}
          <AnimatePresence>
            {rating && ratingConfig && (
              <motion.div
                className={`absolute inset-0 rounded-2xl pointer-events-none ${ratingConfig.washClass}`}
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 0.3, 0] }}
                transition={{ duration: 0.6 }}
              />
            )}
          </AnimatePresence>

          {/* Rating badge - top right */}
          {ratingConfig && (
            <motion.span
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              className={`absolute top-4 right-4 px-3 py-1 rounded-full text-sm font-medium ${ratingConfig.bgColor} ${ratingConfig.textColor}`}
              data-testid="rating-badge"
            >
              {ratingConfig.label}
            </motion.span>
          )}

          {/* Question - adaptive typography, centered when alone */}
          <div className={`${showAnswer ? (isCompact ? "mb-2" : "mb-4") : "text-center"} ${ratingConfig ? "pr-16" : ""}`}>
            <div
              className={`font-medium text-slate-50 ${fontSizes.question}`}
              data-testid="card-question"
              dangerouslySetInnerHTML={{ __html: sanitizedQuestion }}
            />
          </div>

          {/* Image */}
          {imageUrl && (
            <div className="mb-6">
              <Image
                src={imageUrl}
                alt="Card image"
                className="max-w-full h-auto rounded-lg mx-auto"
                width={500}
                height={300}
              />
            </div>
          )}

          {/* Processing skeleton */}
          {isProcessing && !showAnswer && (
            <div className="space-y-3 mt-6 pt-6 border-t border-slate-600/50">
              <div className="h-4 bg-slate-600/50 rounded animate-pulse w-3/4" />
              <div className="h-4 bg-slate-600/50 rounded animate-pulse w-1/2" />
              <div className="h-4 bg-slate-600/50 rounded animate-pulse w-2/3" />
            </div>
          )}

          {/* Answer section */}
          <AnimatePresence>
            {showAnswer && answer && (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{ duration: 0.3 }}
                className={isCompact ? "pt-2 mt-2 border-t border-slate-600/50" : "pt-4 mt-4 border-t border-slate-600/50"}
              >
                <p className={`font-medium text-slate-400 ${isCompact ? "text-[10px] mb-1" : "text-xs mb-2"}`}>
                  Answer
                </p>
                <div
                  className={`text-slate-200 prose prose-invert max-w-none prose-p:my-1 prose-ul:my-1 prose-li:my-0 ${fontSizes.answer}`}
                  data-testid="card-answer"
                  dangerouslySetInnerHTML={{ __html: sanitizedAnswer }}
                />
              </motion.div>
            )}
          </AnimatePresence>

          {/* Action buttons */}
          {showAnswer && actionButtons && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.1 }}
              className={isCompact ? "mt-2 pt-2 border-t border-slate-600/50" : "mt-4 pt-4 border-t border-slate-600/50"}
            >
              {actionButtons}
            </motion.div>
          )}
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
