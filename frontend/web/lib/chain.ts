// BSC chain config for viem (read-only). ⭐ PORT FROM: BridgeAgent/web/lib/chain.ts
import { createPublicClient, http } from "viem";
import { bsc, bscTestnet } from "viem/chains";

export const CHAIN = process.env.NEXT_PUBLIC_TESTNET === "false" ? bsc : bscTestnet;
// One resolved RPC URL, used for BOTH the page's reads and the printed `cast` commands,
// so "verify this yourself" always shows the URL the page actually read from.
export const RPC_URL =
  process.env.NEXT_PUBLIC_BSC_RPC_URL ??
  (CHAIN.id === bsc.id ? "https://bsc-rpc.publicnode.com" : "https://bsc-testnet.publicnode.com");
export const publicClient = createPublicClient({ chain: CHAIN, transport: http(RPC_URL) });

// Dedicated client for ranged getLogs (the trade history). The main RPC (Alchemy free) caps a
// single getLogs to 10 blocks, so the journal's events span far more blocks than it can read in
// one call; NodeReal's free endpoint allows 50k-block ranges → the whole journal reads in ~6 calls.
// Counters and other reads still use the main client. Falls back to it if this URL is unset.
const LOGS_RPC_URL = process.env.NEXT_PUBLIC_JOURNAL_LOGS_RPC_URL;
export const logsClient = LOGS_RPC_URL
  ? createPublicClient({ chain: CHAIN, transport: http(LOGS_RPC_URL) })
  : publicClient;

export const EXPLORER = CHAIN.blockExplorers?.default.url ?? "https://bscscan.com";
