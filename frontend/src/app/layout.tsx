import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'UNCONS - Voice AI Tutor for Anki',
  description: 'Privacy-first AI voice tutor for Feynman Technique learning with Anki flashcards',
  icons: {
    icon: '/icon.svg',
    apple: '/apple-icon.svg',
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white dark:bg-gray-900">
        {children}
      </body>
    </html>
  )
}
