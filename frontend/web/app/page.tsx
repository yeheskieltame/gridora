// Gridora public verifier. Server-rendered (revalidate=30s) so reads come straight from
// BSC via viem. The page is the agent's proof-of-work: ERC-8004 identity, append-only
// TradeJournal, and the commit→attest StrategyLedger.
import { AgentCard } from "@/components/AgentCard";
import { LiveBadge } from "@/components/LiveBadge";
import { ThemeToggle } from "@/components/ThemeToggle";
import { TradesTable } from "@/components/TradesTable";
import { VerifyPanel } from "@/components/VerifyPanel";
import { Wordmark } from "@/components/Wordmark";
import { CornerTicks } from "@/components/Sparkline";
import { GlowCard } from "@/components/GlowCard";
import { RPC_URL } from "@/lib/chain";
import { fetchAgent, fetchStats, fetchTrades, resolveAgentId } from "@/lib/data";

export const revalidate = 30; // re-read live chain data at most every 30s

const TESTNET = process.env.NEXT_PUBLIC_TESTNET !== "false";
const NETWORK = TESTNET ? "BSC Testnet" : "BNB Smart Chain";
const CHAIN_ID = TESTNET ? 97 : 56;

export default async function Page() {
  const readAtMs = Date.now();
  const agentId = await resolveAgentId();
  const [agent, trades] = await Promise.all([fetchAgent(agentId), fetchTrades(agentId)]);
  const stats = await fetchStats(agentId, trades);
  // Cumulative PnL (bps), oldest→newest, for the performance sparkline.
  let cum = 0;
  const pnlSeries = [...trades].reverse().map((t) => (cum += Number(t.pnlBps)));

  return (
    <div className="relative z-10">
      {/* HEADER */}
      <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur supports-[backdrop-filter]:bg-bg/70">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-4 sm:px-10">
          <Wordmark />
          <div className="flex items-center gap-4">
            <div className="hidden sm:block">
              <LiveBadge readAtMs={readAtMs} network={NETWORK} />
            </div>
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* HERO */}
      <section className="mx-auto max-w-6xl px-6 pb-4 pt-14 sm:px-10 sm:pt-20">
        <p className="text-[11px] font-medium uppercase tracking-[0.24em] text-accent">
          Status · {NETWORK} · chain {CHAIN_ID}
        </p>
        <h1 className="mt-4 max-w-3xl font-display text-4xl font-bold leading-[1.05] tracking-tight text-fg sm:text-5xl lg:text-6xl">
          An adaptive grid, <span className="font-serif font-normal italic text-accent">verifiable</span> on BNB Chain.
        </h1>
        <p className="mt-6 max-w-2xl text-base leading-relaxed text-muted">
          Gridora is a non-custodial trading agent. It runs an adaptive maker grid on{" "}
          <span className="font-medium text-fg">BNB Chain</span>, signs every order locally through the{" "}
          <span className="font-medium text-fg">Trust Wallet Agent Kit</span>, and mirrors every settled trade
          to the on-chain <span className="font-medium text-fg">TradeJournal</span>. The identity below is an{" "}
          <span className="font-medium text-fg">ERC-8004</span> NFT this agent owns. No dashboard, no custody, no trust required.
        </p>

        {/* benefit chips */}
        <ul className="mt-8 flex flex-wrap gap-2.5">
          {[
            ["Non-custodial", "keys never leave TWAK"],
            ["Verifiable", "every trade on-chain"],
            ["Adaptive", "regime-aware grid"],
          ].map(([h, s]) => (
            <li key={h} className="inline-flex items-baseline gap-2 rounded-full border border-line bg-surface px-3.5 py-1.5">
              <span className="text-sm font-semibold text-fg">{h}</span>
              <span className="text-xs text-muted">· {s}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* HOW IT WORKS — three clear steps */}
      <section aria-label="How it works" className="mx-auto max-w-6xl px-6 pt-12 sm:px-10">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[
            ["1", "Commit", "The agent pins its grid config hash to the StrategyLedger before placing a single order — a timestamped pre-commitment it can't backdate."],
            ["2", "Trade", "It runs the adaptive grid on PancakeSwap via TWAK, signing locally. It buys dips / sells rips, re-centers with price, and defends with hard risk limits."],
            ["3", "Attest", "When an episode closes, the booked outcome is attested on-chain and every settled trade is mirrored to the append-only TradeJournal."],
          ].map(([n, h, s]) => (
            <GlowCard key={n} className="p-6">
              <CornerTicks />
              <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-accent/40 bg-accent/10 font-mono text-sm font-semibold text-accent">{n}</div>
              <h3 className="mt-4 text-base font-semibold text-fg">{h}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{s}</p>
            </GlowCard>
          ))}
        </div>
      </section>

      {/* AGENT IDENTITY */}
      <div className="mt-14 sm:mt-20">
        <AgentCard agent={agent} stats={stats} network={NETWORK} chainId={CHAIN_ID} pnlSeries={pnlSeries} />
      </div>

      {/* TRADES */}
      <section aria-label="Trades" className="mx-auto max-w-6xl px-6 py-16 sm:px-10 sm:py-24">
        <div className="mb-8 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-accent">Append-only ledger</p>
            <h2 className="mt-2 font-display text-2xl font-bold tracking-tight text-fg sm:text-3xl">
              Settled trades
              <span className="ml-3 align-baseline font-mono text-base tabular-nums text-muted">({stats.totalTrades.toString()})</span>
            </h2>
          </div>
          <p className="max-w-xs text-sm leading-relaxed text-muted">
            Mirrored from the agent&apos;s close-path to the <span className="font-medium text-fg">TradeJournal</span> contract. Click any row for the on-chain detail.
          </p>
        </div>
        <TradesTable trades={trades} count={stats.totalTrades} gated={stats.logsGated} />
      </section>

      {/* VERIFY */}
      <VerifyPanel agentId={agentId} owner={agent.owner} rpcUrl={RPC_URL} />

      {/* BUILT ON */}
      <section aria-label="Built on" className="border-b border-line">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-6 px-6 py-14 sm:px-10">
          <span className="text-[11px] font-medium uppercase tracking-[0.24em] text-muted">Built on</span>
          <ul className="flex flex-wrap items-center justify-center gap-4 sm:gap-5">
            {[
              ["BNB Chain", "/bnb-chain-logo.png"],
              ["CoinMarketCap", "/cmc-logo.jpg"],
              ["Trust Wallet", "/trustwallet-logo.svg"],
              ["PancakeSwap", "/pancakeswap-logo.svg"],
            ].map(([name, src]) => (
              <li
                key={name}
                className="flex h-16 w-44 items-center justify-center rounded-2xl border border-line bg-white px-5 shadow-sm transition-transform duration-300 hover:-translate-y-0.5"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={src} alt={name} className="max-h-10 w-auto object-contain" />
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col gap-5 px-6 py-10 sm:flex-row sm:items-center sm:justify-between sm:px-10">
          <div className="flex items-center gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/gridora-mark.svg" alt="" width={30} height={30} />
            <div>
              <div className="font-sans text-sm font-semibold tracking-tight text-fg">gridora</div>
              <div className="text-xs text-muted">Verifiable grid agent on BNB Chain</div>
            </div>
          </div>
          <div className="flex flex-col gap-1 text-xs text-muted sm:items-end">
            <span>BNB Hack: AI Trading Agent Edition · CoinMarketCap × Trust Wallet × BNB Chain</span>
            <span className="font-mono text-faint">chain {CHAIN_ID}</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
