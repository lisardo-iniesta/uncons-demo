import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Landing page design system: "Neural Pulse"
        landing: {
          // Backgrounds
          bg: {
            DEFAULT: '#050508',
            elevated: '#0a0a0f',
            card: '#0f0f14',
          },
          // Foregrounds
          fg: {
            DEFAULT: '#fafaf9',
            muted: '#a1a1aa',
            subtle: '#71717a',
          },
          // Accent: Electric Cyan
          accent: {
            DEFAULT: '#06b6d4',
            light: '#22d3ee',
            dark: '#0891b2',
            glow: 'rgba(6, 182, 212, 0.4)',
          },
          // Secondary: Emerald for success states
          success: {
            DEFAULT: '#10b981',
            glow: 'rgba(16, 185, 129, 0.4)',
          },
          // Border
          border: {
            DEFAULT: 'rgba(255, 255, 255, 0.08)',
            hover: 'rgba(255, 255, 255, 0.15)',
          },
        },
        // Keep existing app colors
        primary: {
          50: '#f0f9ff',
          500: '#0ea5e9',
          600: '#0284c7',
          700: '#0369a1',
        },
      },
      fontFamily: {
        // Syne: Bold, distinctive display font for headlines
        display: ['Syne', 'system-ui', 'sans-serif'],
        // Outfit: Clean geometric sans for body
        sans: ['Outfit', 'system-ui', 'sans-serif'],
        // JetBrains Mono: Technical/code elements
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      fontSize: {
        // Landing page type scale
        'hero': ['5rem', { lineHeight: '1', letterSpacing: '-0.02em', fontWeight: '700' }],
        'hero-mobile': ['2.5rem', { lineHeight: '1.1', letterSpacing: '-0.02em', fontWeight: '700' }],
        'section': ['3rem', { lineHeight: '1.2', letterSpacing: '-0.01em', fontWeight: '600' }],
        'section-mobile': ['2rem', { lineHeight: '1.2', letterSpacing: '-0.01em', fontWeight: '600' }],
        'lead': ['1.25rem', { lineHeight: '1.6', fontWeight: '400' }],
        'body': ['1rem', { lineHeight: '1.7', fontWeight: '400' }],
        'small': ['0.875rem', { lineHeight: '1.5', fontWeight: '400' }],
      },
      spacing: {
        'section': '8rem',
        'section-mobile': '4rem',
      },
      maxWidth: {
        'landing': '1200px',
      },
      backgroundImage: {
        // Gradient mesh for hero
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'gradient-mesh': 'radial-gradient(at 40% 20%, rgba(6, 182, 212, 0.15) 0px, transparent 50%), radial-gradient(at 80% 0%, rgba(16, 185, 129, 0.1) 0px, transparent 50%), radial-gradient(at 0% 50%, rgba(6, 182, 212, 0.1) 0px, transparent 50%)',
        'noise': "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 400 400' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E\")",
      },
      animation: {
        'float': 'float 6s ease-in-out infinite',
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'gradient': 'gradient 8s ease infinite',
        'glow': 'glow 2s ease-in-out infinite alternate',
        // Review page animations
        'wave': 'wave 0.8s ease-in-out infinite',
        'pulse-glow': 'pulse-glow 1.5s ease-in-out infinite',
        'expanding-ring': 'expanding-ring 1.5s ease-out infinite',
        'spin-slow': 'spin-slow 3s linear infinite',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-20px)' },
        },
        gradient: {
          '0%, 100%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
        },
        glow: {
          '0%': { boxShadow: '0 0 20px rgba(6, 182, 212, 0.2)' },
          '100%': { boxShadow: '0 0 40px rgba(6, 182, 212, 0.4)' },
        },
        // Review page keyframes
        wave: {
          '0%, 100%': { transform: 'scaleY(1)' },
          '50%': { transform: 'scaleY(2)' },
        },
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 20px rgba(6, 182, 212, 0.3)', transform: 'scale(1)' },
          '50%': { boxShadow: '0 0 40px rgba(6, 182, 212, 0.5)', transform: 'scale(1.02)' },
        },
        'expanding-ring': {
          '0%': { transform: 'scale(0.8)', opacity: '0.8' },
          '100%': { transform: 'scale(1.5)', opacity: '0' },
        },
        'spin-slow': {
          from: { transform: 'rotate(0deg)' },
          to: { transform: 'rotate(360deg)' },
        },
      },
      boxShadow: {
        'glow-sm': '0 0 20px rgba(6, 182, 212, 0.2)',
        'glow-md': '0 0 40px rgba(6, 182, 212, 0.3)',
        'glow-lg': '0 0 60px rgba(6, 182, 212, 0.4)',
      },
    },
  },
  plugins: [],
}

export default config
