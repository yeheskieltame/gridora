// Gridora — the public tape. One job: show this agent's verifiable trading history
// to anyone, beautifully. Server-rendered from BNB Chain (ISR 60s), zero wallet needed.
import { FlexNav } from "@/components/flex/Nav";
import { FlexHero, type HeroStats } from "@/components/flex/Hero";
import { Tape, type TapeTrade } from "@/components/flex/Tape";
import { Proof } from "@/components/flex/Proof";
import { EXPLORER } from "@/lib/chain";
import { deriveStats, fetchAgent, fetchTradeCount, fetchTrades, resolveAgentId } from "@/lib/data";

export const revalidate = 60;

function ago(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default async function Page() {
  const agentId = await resolveAgentId();
  const [agent, trades, counter] = await Promise.all([
    fetchAgent(agentId),
    fetchTrades(agentId, 500),
    fetchTradeCount(agentId),
  ]);
  const stats = deriveStats(counter, trades);

  const heroStats: HeroStats = {
    totalTrades: stats.totalTrades.toString(),
    winRate: stats.detailCount > 0
      ? `${Math.round((stats.winningTrades / stats.detailCount) * 100)}%`
      : null,
    netPct: stats.netPnlBps !== null
      ? `${stats.netPnlBps >= 0n ? "+" : ""}${(Number(stats.netPnlBps) / 100).toFixed(2)}%`
      : null,
    since: trades.length
      ? new Date(Number(trades[trades.length - 1].closedAt) * 1000)
          .toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }).toLowerCase()
      : null,
    lastAgo: trades.length ? ago(Number(trades[0].closedAt)) : null,
  };

  const tapeTrades: TapeTrade[] = trades.map((t) => ({
    index: Number(t.index),
    pnlBps: Number(t.pnlBps),
    closedAt: Number(t.closedAt),
    tradeHash: t.tradeHash,
    txHash: t.txHash,
  }));

  return (
    <div className="relative min-h-screen bg-bg text-fg font-sans antialiased">
      <FlexNav />
      <main>
        <FlexHero stats={heroStats} />
        <Tape trades={tapeTrades} explorer={EXPLORER} gated={stats.logsGated} windowed={stats.windowed} />
        <Proof agent={agent} />
      </main>

      <footer className="relative z-10 bg-bg pb-10">
        <div className="max-w-7xl mx-auto px-6 md:px-16 lg:px-20 flex flex-wrap items-center justify-between gap-4 border-t border-line pt-6 text-[12px] lowercase text-muted">
          <span>© 2026 gridora · an autonomous agent on bnb chain{heroStats.since ? `, trading since ${heroStats.since}` : ""}</span>
          <nav className="flex flex-wrap items-center gap-5">
            <a href="#tape" className="link-quiet hover:text-fg">the tape</a>
            <a href="#proof" className="link-quiet hover:text-fg">proof</a>
            {agent.wallet && (
              <a href={`${EXPLORER}/address/${agent.wallet}`} target="_blank" rel="noreferrer"
                 className="link-quiet hover:text-fg">wallet ↗</a>
            )}
            <a href="https://www.8004scan.io/agents" target="_blank" rel="noreferrer"
               className="link-quiet hover:text-fg">erc-8004 #140004 ↗</a>
          </nav>
        </div>
      </footer>
    </div>
  );
}
