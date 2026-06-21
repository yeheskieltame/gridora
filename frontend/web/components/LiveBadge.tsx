"use client";

// Live data indicator: a pulsing gold dot + a self-updating "last read Ns ago"
// label. Receives the server-render timestamp and ticks locally each second.
// The page has `revalidate = 30`, so the server re-reads the chain every 30s.

import { useEffect, useState } from "react";

export function LiveBadge({ readAtMs, network }: { readAtMs: number; network: string }) {
  const [now, setNow] = useState(readAtMs);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const elapsed = Math.max(0, Math.floor((now - readAtMs) / 1000));
  const relative =
    elapsed < 60 ? `${elapsed}s ago` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s ago`;

  return (
    <div className="flex items-center gap-2.5 text-[10px] font-medium uppercase tracking-[0.18em] text-muted">
      <span className="relative flex h-1.5 w-1.5">
        <span className="absolute inset-0 animate-pulse-soft rounded-full bg-accent" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
      </span>
      <span className="font-semibold text-accent">LIVE</span>
      <span className="text-faint">·</span>
      <span>{network}</span>
      <span className="text-faint">·</span>
      <span className="font-mono tabular-nums">last read {relative}</span>
    </div>
  );
}
