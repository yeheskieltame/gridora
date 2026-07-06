// Server-side BSC reads via viem (no wallet connect, no engine import).
// Reads degrade gracefully: an unconfigured/unreadable address returns empty so
// the page renders its shell instead of crashing.
//
// Two read paths, by design:
//  • O(1) counters (totalAgents / totalTrades / tradeCountByAgent) are plain
//    eth_call — they work on ANY RPC, including free ones that gate eth_getLogs.
//  • Per-trade detail lives in the `Recorded` event log (getContractEvents).
//    Some free RPCs gate ranged getLogs; when that happens the counter still
//    gives an authoritative trade COUNT and the table shows a "logs gated" note.
import { publicClient, logsClient } from "./chain";
import { ADDR } from "./contracts";
import { identityAbi, tradeJournalAbi } from "./abis";

export type Agent = {
  agentId: bigint;
  owner: `0x${string}` | null;
  wallet: `0x${string}` | null;
  uri: string | null;
  venue: string;
};

export type Trade = {
  agentId: bigint;
  index: bigint;
  tradeHash: `0x${string}`;
  pnlBps: bigint;
  closedAt: bigint;
};

export type Stats = {
  totalTrades: bigint;       // on-chain counter — authoritative on any RPC
  detailCount: number;       // trades we got full event detail for
  winningTrades: number;
  netPnlBps: bigint | null;  // null when no event detail is available
  logsGated: boolean;        // counter > 0 but no events could be read
  windowed: boolean;         // detail covers only PART of totalTrades (stats are over a window)
  chainOk: boolean;          // the on-chain counter read succeeded (false = chain unreachable)
};

// Execution: on-chain spot on BNB Chain, signed locally by TWAK (TWAK routes the swap;
// no CEX, no specific DEX hardcoded).
const VENUE = "BNB Chain · spot · via TWAK";

function configured(addr: string): boolean {
  return /^0x[0-9a-fA-F]{40}$/.test(addr);
}

async function safe<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

/** Default to the most-recently-minted agent (totalAgents); fall back to #1. */
export async function resolveAgentId(): Promise<bigint> {
  if (!configured(ADDR.identity)) return 0n;   // 0 = no agent → callers render the empty shell
  const total = (await safe(() =>
    publicClient.readContract({
      address: ADDR.identity, abi: identityAbi, functionName: "totalAgents",
    }),
  )) as bigint | null;
  return total && total > 0n ? total : 0n;     // empty/unreadable registry → sentinel, don't probe agent #1
}

export async function fetchAgent(agentId: bigint): Promise<Agent> {
  const blank: Agent = { agentId, owner: null, wallet: null, uri: null, venue: VENUE };
  if (!configured(ADDR.identity) || agentId <= 0n) return blank;
  const info = (await safe(() =>
    publicClient.readContract({
      address: ADDR.identity, abi: identityAbi, functionName: "agentInfo", args: [agentId],
    }),
  )) as readonly [`0x${string}`, `0x${string}`, string] | null;
  if (!info) return blank;
  const [owner, wallet, uri] = info;
  return { agentId, owner, wallet, uri: uri || null, venue: VENUE };
}

export async function fetchTrades(agentId: bigint, limit = 50): Promise<Trade[]> {
  if (!configured(ADDR.journal) || agentId <= 0n) return [];
  try {
    const fromBlock = BigInt(process.env.NEXT_PUBLIC_JOURNAL_FROM_BLOCK ?? "0");
    // Many BSC RPCs cap a single getLogs range (Alchemy's free tier = 10 blocks). LOG_STEP>0
    // splits the scan into ≤(STEP+1)-block windows so it works there; STEP=0 = one call (for
    // an RPC that serves the full range). SCAN_BLOCKS bounds the scan from fromBlock (0 = to tip).
    const step = BigInt(process.env.NEXT_PUBLIC_JOURNAL_LOG_STEP ?? "0");
    const scan = BigInt(process.env.NEXT_PUBLIC_JOURNAL_SCAN_BLOCKS ?? "0");
    const tip = await logsClient.getBlockNumber();
    const toBlock = scan > 0n && fromBlock + scan < tip ? fromBlock + scan : tip;

    const windows: Array<[bigint, bigint]> = [];
    if (step > 0n) {
      for (let b = fromBlock; b <= toBlock; b += step + 1n)
        windows.push([b, b + step < toBlock ? b + step : toBlock]);
    } else {
      windows.push([fromBlock, toBlock]);
    }

    const trades: Trade[] = [];
    const BATCH = 3; // small concurrent batches — the shared NodeReal key rate-limits bursts
    for (let i = 0; i < windows.length; i += BATCH) {
      const res = await Promise.all(
        windows.slice(i, i + BATCH).map(([f, t]) =>
          logsClient
            .getContractEvents({
              address: ADDR.journal, abi: tradeJournalAbi, eventName: "Recorded",
              args: { agentId }, fromBlock: f, toBlock: t,
            })
            .then((logs) =>
              logs.map((l) => {
                const a = l.args as {
                  agentId: bigint; index: bigint; tradeHash: `0x${string}`; pnlBps: bigint; closedAt: bigint;
                };
                return {
                  agentId: a.agentId, index: a.index, tradeHash: a.tradeHash,
                  pnlBps: a.pnlBps, closedAt: a.closedAt,
                } as Trade;
              }),
            )
            .catch(() => [] as Trade[]),
        ),
      );
      for (const r of res) trades.push(...r);
    }
    return trades.sort((a, b) => Number(b.index - a.index)).slice(0, limit);
  } catch {
    return []; // RPC gated getLogs — counters below still render the true count.
  }
}

/** O(1) on-chain trade counter. Independent of the event scan by design, so it can run
 *  in the SAME Promise.all as fetchAgent/fetchTrades (no fetch waterfall).
 *  count=null + chainOk=true  → nothing configured to read (render the empty shell);
 *  count=null + chainOk=false → the read itself failed (chain unreachable). */
export type TradeCount = { count: bigint | null; chainOk: boolean };

export async function fetchTradeCount(agentId: bigint): Promise<TradeCount> {
  if (!configured(ADDR.journal) || agentId <= 0n) return { count: null, chainOk: true };
  const c = (await safe(() =>
    publicClient.readContract({
      address: ADDR.journal, abi: tradeJournalAbi, functionName: "tradeCountByAgent", args: [agentId],
    }),
  )) as bigint | null;
  return { count: c, chainOk: c !== null };
}

/** Pure derivation — no RPC. Combines the authoritative counter with whatever event
 *  detail the log scan produced (win rate / net PnL come from the trades already fetched). */
export function deriveStats(counter: TradeCount, trades: Trade[]): Stats {
  const total = counter.count ?? BigInt(trades.length);
  let win = 0;
  let net = 0n;
  for (const t of trades) {
    if (t.pnlBps > 0n) win++;
    net += t.pnlBps;
  }
  return {
    totalTrades: total,
    detailCount: trades.length,
    winningTrades: win,
    netPnlBps: trades.length > 0 ? net : null,
    logsGated: total > 0n && trades.length === 0,
    windowed: trades.length > 0 && BigInt(trades.length) < total,
    chainOk: counter.chainOk,
  };
}
