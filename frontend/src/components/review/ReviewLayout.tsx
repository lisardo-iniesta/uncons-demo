"use client";

import { ReactNode } from "react";

interface ReviewLayoutProps {
  /** Deck name to display in header */
  deckName: string;
  /** Left sidebar content (voice orb, progress, controls) */
  leftSidebar: ReactNode;
  /** Main card area content */
  children: ReactNode;
  /** Right sidebar content (text mode panel) */
  rightSidebar?: ReactNode;
  /** Whether right sidebar is collapsed */
  rightSidebarCollapsed?: boolean;
}

/**
 * Three-panel layout for the review page.
 *
 * Structure:
 * - Header: Deck name centered
 * - Left Sidebar: Voice orb, progress ring, session stats, controls
 * - Center: Flashcard display area
 * - Right Sidebar: Text mode panel (collapsible)
 */
export function ReviewLayout({
  deckName,
  leftSidebar,
  children,
  rightSidebar,
  rightSidebarCollapsed = false,
}: ReviewLayoutProps) {
  return (
    <div className="h-screen bg-slate-900 text-slate-50 flex flex-col overflow-hidden">
      {/* Header - minimal, just deck name */}
      <header className="h-14 flex items-center justify-center border-b border-slate-800/50 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-20">
        <h1 className="text-sm font-medium text-slate-400 tracking-wide uppercase">
          {deckName}
        </h1>
      </header>

      {/* Main content area with three panels */}
      <div className="flex-1 flex min-h-0">
        {/* Left Sidebar - Voice controls */}
        <aside className="w-64 flex-shrink-0 border-r border-slate-800/50 bg-slate-800/30 p-6 flex flex-col min-h-0">
          {leftSidebar}
        </aside>

        {/* Center - Card area */}
        <main className="flex-1 flex flex-col items-center p-4 min-h-0 overflow-y-auto scrollbar-thin">
          <div className="w-full max-w-2xl">
            {children}
          </div>
        </main>

        {/* Right Sidebar - Text mode (collapsible) */}
        <aside
          className={`flex-shrink-0 border-l border-slate-800/50 bg-slate-800/30 transition-all duration-300 ease-out min-h-0 ${
            rightSidebarCollapsed ? "w-12" : "w-80"
          }`}
        >
          {rightSidebar}
        </aside>
      </div>
    </div>
  );
}
