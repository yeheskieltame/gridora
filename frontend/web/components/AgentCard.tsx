// Agent identity + performance. ERC-8004 soulbound identity, the giant agentId, and a
// data-forward performance panel (hero Net PnL with a cumulative-PnL sparkline + mini stats).
import type { Agent, Stats } from "@/lib/data";
import { ADDR } from "@/lib/contracts";
import { fmtBps } from "@/lib/format";
import { Address } from "./Address";
import { Sparkline, CornerTicks } from "./Sparkline";
import { GlowCard } from "./GlowCard";

export function AgentCard({
  agent,
  stats,
  network,
  chainId,
  pnlSeries = [],
}: {
  agent: Agent;
  stats: Stats;
  network: string;
  chainId: number;
  pnlSeries?: number[];
}) {
  const windowed = stats.windowed ? ` · last ${stats.detailCount}` : "";
  const winRate = stats.detailCount > 0 ? ((stats.winningTrades / stats.detailCount) * 100).toFixed(0) + "%" : "—";
  const netBps = stats.netPnlBps;
  const netValue = netBps === null ? "—" : fmtBps(netBps);
  const positive = netBps !== null && netBps > 0n;
  const negative = netBps !== null && netBps < 0n;
  const tone = positive ? "text-pos" : negative ? "text-neg" : "text-fg";
  const losses = Math.max(0, stats.detailCount - stats.winningTrades);

  return (
    <section aria-labelledby="agent-heading" className="border-y border-line bg-surface py-14 sm:py-20">
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-12 px-6 sm:px-10 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start lg:gap-20">
        {/* LEFT — identity meta */}
        <div className="space-y-8">
          <div>
            <span className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">
              ERC-8004 · Soulbound
            </span>
            <h2 id="agent-heading" className="mt-4 font-display text-3xl font-semibold tracking-tight text-fg sm:text-4xl">
              A self-custodial agent, <span className="font-serif font-normal italic text-accent">on chain</span>.
            </h2>
            <p className="mt-3 max-w-md text-sm leading-relaxed text-muted">
              One non-transferable NFT is the agent&apos;s identity. Everything below is read straight from BNB Chain — no account, no API key.
            </p>
          </div>

          <dl className="grid grid-cols-1 gap-x-12 gap-y-6 sm:grid-cols-2">
            <Field label="Owner" mono>{agent.owner ? <Address value={agent.owner} label="owner address" /> : <Dash />}</Field>
            <Field label="Trading wallet (TWAK)" mono>{agent.wallet ? <Address value={agent.wallet} label="trading wallet" /> : <Dash />}</Field>
            <Field label="Venue"><span className="text-fg">{agent.venue}</span></Field>
            <Field label="Identity Registry" mono><Address value={ADDR.identity} label="IdentityRegistry" /></Field>
            <Field label="Trade Journal" mono><Address value={ADDR.journal} label="TradeJournal" /></Field>
            <Field label="Strategy Ledger" mono><Address value={ADDR.ledger} label="StrategyLedger" /></Field>
            {agent.uri && (
              <Field label="Registration URI" mono className="sm:col-span-2">
                <span className="block truncate text-xs text-muted" title={agent.uri}>{agent.uri}</span>
              </Field>
            )}
          </dl>
        </div>

        {/* RIGHT — colossal #N */}
        <div className="relative flex flex-col items-start lg:items-end">
          <div className="text-[11px] font-medium uppercase tracking-[0.24em] text-muted">Agent</div>
          <div className="relative mt-1">
            <span aria-hidden className="absolute -left-1 top-0 select-none font-display text-[6rem] font-semibold leading-[0.85] tracking-tighter text-accent/40 sm:text-[8rem]">#</span>
            <span className="block pl-10 font-display text-[7rem] font-semibold leading-[0.85] tracking-tighter tabular-nums text-fg sm:pl-14 sm:text-[10rem] lg:text-[12rem]">
              {agent.agentId > 0n ? agent.agentId.toString() : "—"}
            </span>
          </div>
          <div className="mt-3 flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.18em] text-muted">
            <span>{network}</span><span className="text-faint">·</span><span className="font-mono tabular-nums">chain {chainId}</span>
          </div>
        </div>
      </div>

      {/* PERFORMANCE — asymmetric: hero PnL + sparkline, two mini stats */}
      <div className="mx-auto mt-14 grid max-w-6xl grid-cols-1 gap-4 px-6 sm:mt-20 sm:grid-cols-3 sm:px-10">
        <GlowCard className="card-grid bg-bg p-6 sm:col-span-2">
          <CornerTicks />
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted">Net PnL{stats.windowed ? " · windowed" : ""}</div>
              <div className={`mt-2 font-display text-5xl font-semibold leading-none tracking-tight tabular-nums sm:text-6xl ${tone}`}>{netValue}</div>
              <div className="mt-2 text-xs text-faint">
                basis points · <span className="text-pos">{stats.winningTrades} win</span> / <span className="text-neg">{losses} loss</span>{windowed}
              </div>
            </div>
            <Pill sign={positive ? "pos" : negative ? "neg" : "flat"} />
          </div>
          {pnlSeries.length > 1 ? (
            <div className={`mt-5 h-14 w-full ${tone}`}><Sparkline data={pnlSeries} className="h-full w-full" /></div>
          ) : (
            <div className="mt-5 h-14 w-full rounded-lg border border-dashed border-line" />
          )}
        </GlowCard>

        <div className="grid grid-rows-2 gap-4">
          <MiniStat label="Win rate" value={winRate} hint={stats.windowed ? `last ${stats.detailCount} trades` : "all trades"} />
          <MiniStat label="Settled trades" value={stats.totalTrades.toString()} hint="on-chain count" />
        </div>
      </div>
    </section>
  );
}

function Pill({ sign }: { sign: "pos" | "neg" | "flat" }) {
  const map = {
    pos: ["text-pos", "border-pos/30 bg-pos/10", "▲"],
    neg: ["text-neg", "border-neg/30 bg-neg/10", "▼"],
    flat: ["text-muted", "border-line bg-bg", "—"],
  } as const;
  const [t, b, a] = map[sign];
  return <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-mono text-xs font-semibold ${t} ${b}`}>{a}</span>;
}

function Dash() {
  return <span className="text-faint">— not deployed yet</span>;
}

function Field({ label, children, mono = false, className = "" }: {
  label: string; children: React.ReactNode; mono?: boolean; className?: string;
}) {
  return (
    <div className={className}>
      <dt className="text-[11px] font-medium uppercase tracking-[0.16em] text-muted">{label}</dt>
      <dd className={`mt-2 text-sm text-fg ${mono ? "font-mono" : ""}`}>{children}</dd>
    </div>
  );
}

function MiniStat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <GlowCard className="bg-bg px-6 py-5">
      <CornerTicks />
      <dt className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted">{label}</dt>
      <dd className="mt-1.5 font-display text-3xl font-semibold leading-none tracking-tight tabular-nums text-fg">{value}</dd>
      {hint && <div className="mt-1.5 text-[11px] text-faint">{hint}</div>}
    </GlowCard>
  );
}
