// Gridora wordmark — a minimal 3×3 grid glyph (the trading grid) with one gold
// cell lit, paired with the lowercase mark. Pure SVG so it never waits on a font.

export function Wordmark() {
  return (
    <a
      href="/"
      aria-label="Gridora"
      className="group inline-flex items-center gap-2.5 text-fg transition-colors"
    >
      <svg viewBox="0 0 18 18" width="18" height="18" fill="none" aria-hidden>
        <g stroke="currentColor" strokeWidth="1.4" className="text-muted transition-colors group-hover:text-fg">
          <path d="M2 6.5H16M2 11.5H16M6.5 2V16M11.5 2V16" strokeLinecap="square" />
        </g>
        {/* one lit grid cell — the active level */}
        <rect x="11.5" y="2" width="4.5" height="4.5" fill="#ebb918" opacity="0.95" />
      </svg>
      <span className="font-sans text-base font-semibold tracking-tight">gridora</span>
    </a>
  );
}
