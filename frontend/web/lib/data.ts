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
import { publicClient } from "./chain";
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
    const logs = await publicClient.getContractEvents({
      address: ADDR.journal, abi: tradeJournalAbi, eventName: "Recorded",
      args: { agentId }, fromBlock, toBlock: "latest",
    });
    const trades = logs.map((l) => {
      const a = l.args as {
        agentId: bigint; index: bigint; tradeHash: `0x${string}`; pnlBps: bigint; closedAt: bigint;
      };
      return { agentId: a.agentId, index: a.index, tradeHash: a.tradeHash, pnlBps: a.pnlBps, closedAt: a.closedAt };
    });
    return trades.sort((a, b) => Number(b.index - a.index)).slice(0, limit);
  } catch {
    return []; // RPC gated getLogs — counters below still render the true count.
  }
}

export async function fetchStats(agentId: bigint, trades: Trade[]): Promise<Stats> {
  let total = BigInt(trades.length);
  let chainOk = true;
  if (configured(ADDR.journal) && agentId > 0n) {
    const c = (await safe(() =>
      publicClient.readContract({
        address: ADDR.journal, abi: tradeJournalAbi, functionName: "tradeCountByAgent", args: [agentId],
      }),
    )) as bigint | null;
    if (c !== null) total = c;
    else chainOk = false;        // configured but the counter read failed → chain unreachable
  }
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
    chainOk,
  };
}
