import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0a0a0b',
        panel: '#111114',
        border: '#1f1f24',
        text: '#e4e4e7',
        muted: '#71717a',
        accent: '#7c5cff',
        ok: '#10b981',
        warn: '#f59e0b',
        err: '#ef4444',
      },
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', '-apple-system'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo'],
      },
    },
  },
  plugins: [],
};

export default config;
