// Minimal area sparkline — colored by the parent's text color (currentColor), so it
// inherits the profit/loss tone. Pure SVG, stretches to its container.
export function Sparkline({ data, className }: { data: number[]; className?: string }) {
  if (!data || data.length < 2) return null;
  const w = 240, h = 56, pad = 4;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((d, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - pad - ((d - min) / range) * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const line = "M" + pts.join(" L");
  const area = `${line} L${w},${h} L0,${h} Z`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={className} aria-hidden>
      <defs>
        <linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.20" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#spark-fill)" />
      <path d={line} fill="none" stroke="currentColor" strokeWidth="1.5"
            strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// Small corner crosshair ticks — terminal/blueprint motif. Decorative.
export function CornerTicks() {
  return (
    <svg aria-hidden className="pointer-events-none absolute right-3 top-3 h-3.5 w-3.5 text-accent/50">
      <path d="M13 0V5M13 0H8" stroke="currentColor" strokeWidth="1.25" />
    </svg>
  );
}
