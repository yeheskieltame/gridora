// Console control-plane client. Talks ONLY to the backend control API (the
// GridService/supervisor seam exposed over HTTP by `runner --serve`) — never to the
// engine or a wallet SDK on the server. Wallet auth = SIWE-lite personal_sign,
// verified server-side against GRIDORA_OWNER.

import { createWalletClient, custom, type Address } from "viem";

export const CONTROL_API =
  process.env.NEXT_PUBLIC_CONTROL_API ?? "http://127.0.0.1:8317";

export type ConsoleState = {
  market: string;
  mode: "dry" | "paper" | "live" | string;
  venue: string;
  chain_id: number;
  agent_address: string;
  status: string;
  autopilot: boolean;
  is_running: boolean;
  active_strategy: string;
  strategies: Record<string, boolean>;
  owner_set: boolean;
  regime: {
    label: string;
    fear_greed: number;
    momentum: number;
    funding_bps: number;
    stale: boolean;
  } | null;
  decision: {
    strategy_key: string;
    reasoning: string;
    confidence: string;
    source: string;
    cost_usd: number;
    latency_ms: number;
  } | null;
  grid: {
    band: [string, string];
    levels: number;
    bias: number;
    net_base: string;
    avg_entry: string;
    resting_orders: number;
    fills: number;
    realized: number;
    unrealized: number;
    pnl_bps: number;
    max_dd_bps: number;
  };
  account: {
    start_equity: number;
    equity: number;
    pnl_pct: number;
    cum_realized: number;
    episodes: number;
    wins: number;
    win_rate: number;
  };
  equity_history: [number, number][];
  episode_history: [number, string, number, number][];
  onchain: {
    identity_tx: string;
    commit_tx: string;
    attest_tx: string;
    journal_count: number;
  };
  log: string[];
};

/** Public autopilot read model (GET /api/autopilot). Older backends don't serve it —
 *  callers must treat a 404 as "feature absent", not an error loop. */
export type AutopilotPosition = {
  sym: string;
  qty: number;
  cost: number; // USD spent to open
  eff: number;  // effective entry price (fees in)
  ts: number;   // unix, position opened
};

export type AutopilotState = {
  pos: AutopilotPosition | null;
  peak: number | null;
  px_peak: number | null;
  pending: { sym: string; count: number } | null;
  cooldown: Record<string, unknown>;
  paper?: Record<string, number>; // present ⇒ paper mode (simulated balances)
  last_alert: number;
  updated: number; // unix
  running: boolean;
};

/** One autopilot fill (GET /api/trades?limit=N, newest-first).
 *  buy rows carry cost/eff; sell rows carry proceeds/net/bps/reason. */
export type AutopilotTrade = {
  event: "buy" | "sell";
  sym: string;
  qty: number;
  cost?: number;
  eff?: number;
  proceeds?: number;
  net?: number;
  bps?: number;
  reason?: string;
  ts: number;
  at: string;
};

export type ControlAction =
  | { action: "autopilot"; on: boolean }
  | { action: "decide" }
  | { action: "halt" }
  | {
      action: "strategy";
      key: string;
      half_band?: number;
      levels?: number;
      bias?: number;
    };

async function api<T>(path: string, body?: unknown, token?: string): Promise<T> {
  const res = await fetch(`${CONTROL_API}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error((data as { error?: string }).error ?? `HTTP ${res.status}`);
    (err as Error & { status?: number }).status = res.status;
    throw err;
  }
  return data as T;
}

export function fetchConsoleState(): Promise<ConsoleState> {
  return api<ConsoleState>("/api/state");
}

export function fetchAutopilot(): Promise<AutopilotState> {
  return api<AutopilotState>("/api/autopilot");
}

export function fetchAutopilotTrades(limit = 20): Promise<{ trades: AutopilotTrade[] }> {
  return api<{ trades: AutopilotTrade[] }>(`/api/trades?limit=${limit}`);
}

/** Connect the injected wallet, sign the nonce, exchange it for a session token.
 *  Throws with a human-readable message on every failure path (no wallet, user
 *  rejected, not the owner). */
export async function walletLogin(): Promise<{ token: string; address: Address }> {
  const eth = (window as unknown as { ethereum?: unknown }).ethereum;
  if (!eth) throw new Error("No wallet found — install MetaMask or Trust Wallet.");
  const wallet = createWalletClient({ transport: custom(eth as Parameters<typeof custom>[0]) });
  const [address] = await wallet.requestAddresses();
  if (!address) throw new Error("Wallet returned no account.");
  const { nonce, message } = await api<{ nonce: string; message: string }>("/api/auth/nonce");
  const signature = await wallet.signMessage({ account: address, message });
  const { token } = await api<{ token: string }>("/api/auth/verify", {
    address,
    signature,
    nonce,
  });
  return { token, address };
}

export function sendControl(token: string, action: ControlAction): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>("/api/control", action, token);
}
