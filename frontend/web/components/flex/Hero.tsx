"use client";

// Flex hero: ambient video masked into porcelain, two-tone editorial headline,
// live on-chain stat strip. Stats arrive pre-computed from the server (JSON-safe).
import { motion } from "motion/react";

const VIDEO_URL =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260603_132049_036591b8-6e92-4760-b94c-a7ea6eef315c.mp4";

export type HeroStats = {
  totalTrades: string;
  winRate: string | null;   // "57%" or null when logs are gated
  netPct: string | null;    // "+6.5%" cumulative journaled bps
  since: string | null;     // "22 jun 2026"
  lastAgo: string | null;   // "3h ago"
};

function EyePill() {
  return (
    <span className="w-[16px] md:w-[42px] lg:w-[62px] h-[0.62em] border-[2px] border-fg rounded-full inline-flex items-center justify-center align-middle mx-1">
      <span className="w-2 h-2 rounded-full bg-fg" />
    </span>
  );
}

export function FlexHero({ stats }: { stats: HeroStats }) {
  const cells: Array<[string, string, boolean]> = [
    ["journaled trades", stats.totalTrades, false],
    ["win rate", stats.winRate ?? "—", false],
    ["net pnl (journaled)", stats.netPct ?? "—", (stats.netPct ?? "").startsWith("+")],
    ["last trade", stats.lastAgo ?? "—", false],
  ];

  return (
    <section id="top" className="relative w-full overflow-hidden bg-bg pt-32 md:pt-40 pb-10">
      <div className="absolute top-0 left-0 w-full h-[70vh] z-0 pointer-events-none opacity-[0.55]">
        <video autoPlay loop muted playsInline className="w-full h-full object-cover" src={VIDEO_URL} />
        <div className="absolute inset-0 bg-gradient-to-b from-bg via-bg/40 to-bg" />
      </div>

      <div className="max-w-7xl w-full mx-auto px-6 md:px-16 lg:px-20 relative z-10 grid grid-cols-12 gap-x-4 md:gap-x-8">
        <div className="col-span-12 md:col-span-10 md:col-start-2">
          <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.6 }}
                    className="flex items-center gap-2.5 text-[11px] font-medium uppercase tracking-[0.18em] text-muted">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inset-0 animate-pulse-soft rounded-full bg-accent" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
            </span>
            <span className="font-semibold text-accent">live on bnb chain</span>
            <span className="text-faint">·</span>
            <span>agent #140004</span>
          </motion.p>

          <motion.h1 initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }}
                     transition={{ duration: 0.8 }}
                     className="mt-5 font-display text-[clamp(2.2rem,5.6vw,4.5rem)] leading-[1.06] tracking-tight">
            <span className="text-fg">the tape doesn&apos;t lie.</span>
            <br />
            <span className="text-muted">every trade this agent settles</span>
            <br />
            <span className="text-muted">
              is written <EyePill /> on-chain. read it yourself.
            </span>
          </motion.h1>

          <motion.dl initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }}
                     transition={{ duration: 0.8, delay: 0.15 }}
                     className="mt-10 grid grid-cols-2 md:grid-cols-4 gap-px rounded-2xl overflow-hidden border border-line bg-line/60">
            {cells.map(([k, v, volt]) => (
              <div key={k} className="bg-surface/90 backdrop-blur-sm px-5 py-4">
                <dt className="text-[10px] uppercase tracking-[0.16em] text-faint">{k}</dt>
                <dd className={`mt-1.5 font-mono text-xl md:text-2xl font-semibold tabular-nums ${volt ? "text-pos" : "text-fg"}`}>
                  {v}
                </dd>
              </div>
            ))}
          </motion.dl>
        </div>
      </div>
    </section>
  );
}
