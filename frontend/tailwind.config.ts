import type { Config } from 'tailwindcss';
import forms from '@tailwindcss/forms';

/**
 * Design tokens.
 *
 * These are the single source of truth for the visual language. Components
 * reference semantic names (`surface-800`, `profit`, `danger`) rather than raw
 * hex values, so a palette change happens here and nowhere else.
 */
const config: Config = {
  darkMode: 'class',
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Height-based breakpoints. The auth forms must fit the VIEWPORT HEIGHT
      // without scrolling; width breakpoints (sm/md/lg) are the wrong axis for
      // that. `short` = laptops ≤ 800px tall (13" screens, windowed browsers).
      screens: {
        short: { raw: '(max-height: 800px)' },
        tiny: { raw: '(max-height: 680px)' },
      },
      fontFamily: {
        sans: ['var(--font-jakarta)', 'system-ui', 'sans-serif'],
        // Monospace for every number: prices, quantities, P&L, order IDs.
        // Tabular figures keep table columns aligned as values change.
        mono: ['var(--font-mono)', 'ui-monospace', 'monospace'],
      },
      colors: {
        obsidian: '#05070B',
        surface: {
          900: '#090D14',
          850: '#0C111C',
          800: '#111726',
          700: '#1A233A',
          600: '#23304E',
        },
        // Semantic trading colours. Named by MEANING, not by hue, so a
        // colour-blind-friendly palette swap does not require touching JSX.
        profit: {
          DEFAULT: '#34d399',
          strong: '#10b981',
          dim: '#064e3b',
        },
        loss: {
          DEFAULT: '#f87171',
          strong: '#ef4444',
          dim: '#7f1d1d',
        },
        /** Paper / simulated mode. Never used for anything else. */
        paper: {
          DEFAULT: '#fbbf24',
          strong: '#f59e0b',
          dim: '#78350f',
        },
        brand: {
          400: '#22d3ee',
          500: '#06b6d4',
          600: '#0891b2',
          hover: '#00e5ff',
        },
      },
      boxShadow: {
        'glow-brand': '0 0 25px -5px rgba(6, 182, 212, 0.35)',
        'glow-brand-lg': '0 0 45px -10px rgba(6, 182, 212, 0.45)',
        elevated:
          '0 20px 50px -12px rgba(0, 0, 0, 0.75), 0 0 0 1px rgba(255, 255, 255, 0.05)',
      },
      keyframes: {
        shimmer: {
          '0%': { backgroundPosition: '0% center' },
          '100%': { backgroundPosition: '200% center' },
        },
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        shimmer: 'shimmer 8s linear infinite',
        'fade-in': 'fade-in 200ms ease-out',
      },
    },
  },
  plugins: [forms],
};

export default config;
