import type { Config } from "tailwindcss";

// Semantic tokens map to CSS variables (see globals.css) so the whole UI themes by
// toggling the `.dark` class on <html> — light is the default (clean, readable).
const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)",          // page background
        surface: "rgb(var(--surface) / <alpha-value>)", // cards / panels
        elevated: "rgb(var(--elevated) / <alpha-value>)",
        fg: "rgb(var(--fg) / <alpha-value>)",          // primary text
        muted: "rgb(var(--muted) / <alpha-value>)",    // secondary text / labels
        faint: "rgb(var(--faint) / <alpha-value>)",    // tertiary / hints
        line: "rgb(var(--line) / <alpha-value>)",      // borders / dividers
        accent: "rgb(var(--accent) / <alpha-value>)",  // BNB gold brand accent
        pos: "rgb(var(--pos) / <alpha-value>)",        // profit
        neg: "rgb(var(--neg) / <alpha-value>)",        // loss
        // Landing page fixed palette (theme-independent — the landing is always light).
        "bg-base": "#EDEEF5",
        "brand-green": "#9fff00",
        // Raw BNB gold scale (kept for the wordmark glyph + fixed marks).
        gold: {
          50: "#fdf8e7", 100: "#faedbf", 200: "#f5dc84", 300: "#efc94a",
          400: "#ebb918", 500: "#cf9d0d", 600: "#a87a0d", 700: "#855e12",
          800: "#5b4110", 900: "#382809", 950: "#1f1604",
        },
      },
      fontFamily: {
        // Inter for everything readable; JetBrains Mono for data/addresses/numbers;
        // Instrument Serif italic as the single editorial accent.
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono Variable", "JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
        serif: ["Instrument Serif", "ui-serif", "Georgia", "serif"],
        // Landing display font (next/font Outfit sets --font-outfit on the landing root).
        outfit: ["var(--font-outfit)", "Outfit", "ui-sans-serif", "sans-serif"],
      },
      animation: {
        "pulse-soft": "pulse-soft 2.4s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "ticker-rise": "ticker-rise 0.6s cubic-bezier(0.16, 1, 0.3, 1) both",
      },
      keyframes: {
        "pulse-soft": {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.5", transform: "scale(0.9)" },
        },
        "ticker-rise": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
