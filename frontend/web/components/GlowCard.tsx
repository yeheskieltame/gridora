"use client";

// Spotlight card (21st.dev signature): a gold radial glow follows the cursor, an accent
// hairline lights the top edge on hover, and the card lifts — over a depth-shadowed
// surface. Theme-aware (uses the --accent token). Wrap any content.
import { useRef, useState } from "react";

export function GlowCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [p, setP] = useState({ x: 50, y: 0, on: false });

  return (
    <div
      ref={ref}
      onMouseMove={(e) => {
        const r = ref.current?.getBoundingClientRect();
        if (r) setP({ x: e.clientX - r.left, y: e.clientY - r.top, on: true });
      }}
      onMouseLeave={() => setP((s) => ({ ...s, on: false }))}
      className={`group surface-card relative overflow-hidden transition-[transform,border-color] duration-300 hover:-translate-y-1 hover:border-accent/40 ${className}`}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 transition-opacity duration-300"
        style={{
          opacity: p.on ? 1 : 0,
          background: `radial-gradient(440px circle at ${p.x}px ${p.y}px, rgb(var(--accent) / 0.14), transparent 42%)`,
        }}
      />
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/70 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100"
      />
      <div className="relative z-10">{children}</div>
    </div>
  );
}
