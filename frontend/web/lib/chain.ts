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
export const EXPLORER = CHAIN.blockExplorers?.default.url ?? "https://bscscan.com";
