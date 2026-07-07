"use client";

// THE TAPE — the whole point of the site. Every journaled trade, newest first,
// with a working filter capsule and a BscScan proof link per row.
import { useMemo, useState } from "react";
import { CopyButton } from "@/components/CopyButton";
import { ArrowUpRight } from "@/components/icons";

export type TapeTrade = {
  index: number;
  pnlBps: number;
  closedAt: number;          // unix seconds
  tradeHash: string;
  txHash: string | null;
};

function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString("en-GB", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  }).toLowerCase();
}

function short(h: string): string {
  return `${h.slice(0, 10)}…${h.slice(-6)}`;
}

export function Tape({ trades, explorer, gated, windowed }: {
  trades: TapeTrade[]; explorer: string; gated: boolean; windowed: boolean;
}) {
  const [q, setQ] = useState("");
  const [show, setShow] = useState(60);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return trades;
    return trades.filter((t) =>
      String(t.index).includes(needle) ||
      t.tradeHash.toLowerCase().includes(needle) ||
      (t.txHash ?? "").toLowerCase().includes(needle) ||
      (needle === "win" && t.pnlBps > 0) ||
      (needle === "loss" && t.pnlBps < 0),
    );
  }, [q, trades]);

  const wins = trades.filter((t) => t.pnlBps > 0).length;

  return (
    <section id="tape" className="relative z-10 bg-bg pb-24 scroll-mt-28">
      <div className="max-w-7xl mx-auto px-6 md:px-16 lg:px-20">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <h2 className="font-display text-3xl md:text-4xl font-semibold tracking-tight lowercase text-fg">
              the tape
            </h2>
            <p className="mt-2 text-[14px] text-muted max-w-xl">
              {trades.length} journaled episodes on-chain · {wins} closed green.
              each row is a real BNB Chain transaction — click it, check it.
              {windowed && " (showing the scanned window)"}
            </p>
          </div>
          {/* the filter capsule — a REAL control: filters the rows below as you type */}
          <div className="bg-white rounded-[6px] border border-line p-1 pl-4 flex items-center shadow-sm w-full sm:w-80">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder='filter: index, hash, "win", "loss"…'
              className="flex-1 bg-transparent outline-none text-[14px] text-fg placeholder:text-faint font-sans"
            />
            <button type="button" aria-label="clear filter" onClick={() => setQ("")}
                    className={`bg-fg text-white w-8 h-8 rounded-full relative shrink-0 transition-opacity ${q ? "opacity-100" : "opacity-30"}`}>
              <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 absolute inset-0 m-auto" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>
        </div>

        <div className="mt-8 surface-card overflow-hidden">
          {gated ? (
            <div className="p-10 text-center text-[14px] text-muted">
              the RPC gated event logs right now — the on-chain counter above is still
              authoritative. try again shortly.
            </div>
          ) : filtered.length === 0 ? (
            <div className="p-10 text-center text-[14px] text-muted">
              {q ? <>nothing matches “{q}” — <button className="underline" onClick={() => setQ("")}>clear the filter</button></> : "no journaled trades yet."}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-[0.16em] text-faint border-b border-line">
                    <th className="px-5 py-3 font-medium">#</th>
                    <th className="px-3 py-3 font-medium">closed</th>
                    <th className="px-3 py-3 font-medium">pnl</th>
                    <th className="px-3 py-3 font-medium hidden md:table-cell">fills root</th>
                    <th className="px-5 py-3 font-medium text-right">proof</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.slice(0, show).map((t) => (
                    <tr key={t.index} className="border-b border-line/60 last:border-0 hover:bg-bg/50 transition-colors">
                      <td className="px-5 py-3 font-mono text-faint">{t.index}</td>
                      <td className="px-3 py-3 font-mono text-muted whitespace-nowrap">{fmtDate(t.closedAt)}</td>
                      <td className="px-3 py-3">
                        <span className={`${t.pnlBps >= 0 ? "chip-volt" : "chip-neg"} px-2.5 py-0.5 text-[12px]`}>
                          {t.pnlBps >= 0 ? "+" : ""}{t.pnlBps} bps
                        </span>
                      </td>
                      <td className="px-3 py-3 hidden md:table-cell">
                        <span className="inline-flex items-center gap-1.5 font-mono text-muted">
                          {short(t.tradeHash)}
                          <CopyButton value={t.tradeHash} label="trade hash" />
                        </span>
                      </td>
                      <td className="px-5 py-3 text-right">
                        {t.txHash ? (
                          <a href={`${explorer}/tx/${t.txHash}`} target="_blank" rel="noreferrer"
                             className="link-quiet inline-flex items-center gap-1 font-mono text-accent">
                            bscscan <ArrowUpRight className="w-3 h-3" />
                          </a>
                        ) : (
                          <span className="text-faint">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {filtered.length > show && (
            <button onClick={() => setShow((s) => s + 100)}
                    className="w-full py-3.5 text-[13px] lowercase text-accent hover:bg-bg/60 transition-colors border-t border-line">
              show {Math.min(100, filtered.length - show)} more ({filtered.length - show} left)
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
