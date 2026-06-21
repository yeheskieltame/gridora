"use client";

// Append-only settled-trade ledger. Each row expands to show the on-chain record
// (tradeHash, signed PnL, close time). When the RPC gates event logs but the on-chain
// counter is non-zero, we render an honest "logs gated" notice with the true count.
import { useState } from "react";
import { fmtBps, fmtHash, fmtTimestamp, fmtRelative } from "@/lib/format";
import type { Trade } from "@/lib/data";
import { ChevronDown } from "./icons";
import { CopyButton } from "./CopyButton";

export function TradesTable({ trades, count, gated }: { trades: Trade[]; count: bigint; gated: boolean }) {
  if (trades.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-line bg-surface p-12 text-center sm:p-16">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted">
          {gated ? `${count.toString()} settled trade(s) on chain` : "No settled trades yet"}
        </p>
        <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-muted">
          {gated ? (
            <>
              The on-chain <span className="font-mono text-fg">totalTrades</span> counter confirms{" "}
              {count.toString()} record(s), but this public RPC gates{" "}
              <span className="font-mono text-fg">eth_getLogs</span>. Point{" "}
              <span className="font-mono text-fg">NEXT_PUBLIC_BSC_RPC_URL</span> at a log-serving
              endpoint to list each trade.
            </>
          ) : (
            <>Each settled trade appears here the moment it&apos;s mirrored to the TradeJournal contract — with its hash, signed PnL, and close time.</>
          )}
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface">
      <header className="hidden grid-cols-[1fr_1.4fr_auto_auto] items-baseline gap-6 border-b border-line px-6 py-3 text-[11px] font-medium uppercase tracking-[0.16em] text-muted sm:grid">
        <span>Closed at</span>
        <span>Trade hash</span>
        <span className="text-right">PnL</span>
        <span className="w-4" aria-hidden />
      </header>
      <ol className="divide-y divide-line">
        {trades.map((t, idx) => (
          <TradeRow key={`${t.tradeHash}-${idx}`} trade={t} index={idx} />
        ))}
      </ol>
    </div>
  );
}

function TradeRow({ trade, index }: { trade: Trade; index: number }) {
  const [open, setOpen] = useState(false);
  const up = trade.pnlBps > 0n;
  const down = trade.pnlBps < 0n;
  const tone = up ? "text-pos" : down ? "text-neg" : "text-muted";
  const edge = up ? "border-pos/60" : down ? "border-neg/60" : "border-transparent";

  return (
    <li className={`group animate-ticker-rise border-l-2 ${edge}`} style={{ animationDelay: `${Math.min(index * 30, 240)}ms` }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="grid w-full cursor-pointer grid-cols-[auto_1fr_auto] items-baseline gap-3 px-6 py-4 text-left transition-colors hover:bg-bg sm:grid-cols-[1fr_1.4fr_auto_auto] sm:gap-6 sm:py-5"
      >
        <span className={`font-display text-2xl font-semibold tabular-nums tracking-tight sm:hidden ${tone}`}>
          {fmtBps(trade.pnlBps)}
        </span>

        <div className="flex flex-col gap-0.5 sm:contents">
          <span className="font-mono text-sm tabular-nums text-fg">{fmtTimestamp(trade.closedAt)}</span>
          <span className="text-[11px] uppercase tracking-wider text-faint sm:hidden">{fmtRelative(trade.closedAt)}</span>
        </div>

        <span className="hidden items-center gap-2 sm:flex">
          <span className="font-mono text-sm text-muted" title={trade.tradeHash}>{fmtHash(trade.tradeHash)}</span>
          <CopyButton value={trade.tradeHash} label="trade hash" />
        </span>

        <span className={`hidden font-display text-2xl font-semibold tabular-nums tracking-tight sm:inline ${tone}`}>
          {fmtBps(trade.pnlBps)}
        </span>

        <ChevronDown className={`h-4 w-4 text-faint transition-transform duration-200 ${open ? "rotate-180 text-accent" : ""}`} />
      </button>

      <div className="expand-enter" data-open={open}>
        <div>
          <ExpandedDetail trade={trade} />
        </div>
      </div>
    </li>
  );
}

function ExpandedDetail({ trade }: { trade: Trade }) {
  return (
    <div className="border-l-2 border-accent/60 bg-bg px-6 pb-6 pt-2">
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-[1fr_1fr] sm:gap-12">
        <div className="space-y-3">
          <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted">What this proves</div>
          <p className="text-sm leading-relaxed text-muted">
            A <span className="font-mono text-fg">Recorded</span> event the agent&apos;s wallet appended to
            the TradeJournal — gated by <span className="font-mono text-fg">ownerOf(agentId)</span>, so only
            this agent can write it. The <span className="text-fg">tradeHash</span> commits to the fills; the
            signed <span className="text-fg">PnL (bps)</span> is the booked result.
          </p>
        </div>
        <div className="space-y-3">
          <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted">On-chain record</div>
          <dl className="space-y-2 text-sm">
            <DetailRow label="Trade hash">
              <span className="inline-flex items-center gap-2 font-mono text-fg">
                <span title={trade.tradeHash}>{fmtHash(trade.tradeHash)}</span>
                <CopyButton value={trade.tradeHash} label="trade hash" />
              </span>
            </DetailRow>
            <DetailRow label="PnL (bps)">
              <span className="font-mono tabular-nums text-fg">{trade.pnlBps.toString()}</span>
            </DetailRow>
            <DetailRow label="Closed at">
              <span className="font-mono tabular-nums text-fg">{fmtTimestamp(trade.closedAt)}</span>
            </DetailRow>
            <DetailRow label="Agent / index">
              <span className="font-mono tabular-nums text-fg">#{trade.agentId.toString()} · {trade.index.toString()}</span>
            </DetailRow>
          </dl>
        </div>
      </div>
    </div>
  );
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line pb-2 last:border-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
