"use client";

// Gridora operator console — the Textual TUI, on the web. Everyone can WATCH the
// live agent (public read model, same facts as the verifier); only wallets on the
// backend's GRIDORA_OWNER allowlist can DRIVE it. Controls mirror the TUI 1:1
// (auto/manual, strategy picks, band/levels/bias, decide, kill→flat) and the same
// deterministic guardrails clamp every action server-side.
//
// Keyboard (once unlocked): a auto · d decide · k kill · 1-6 strategy · [ ] band ·
// , . levels · b bias — the exact TUI bindings.

import { useCallback, useEffect, useRef, useState } from "react";
import { Sparkline, CornerTicks } from "@/components/Sparkline";
import { Wordmark } from "@/components/Wordmark";
import {
  CONTROL_API,
  fetchAutopilot,
  fetchAutopilotTrades,
  fetchConsoleState,
  sendControl,
  walletLogin,
  type AutopilotState,
  type AutopilotTrade,
  type ConsoleState,
  type ControlAction,
} from "@/lib/control";

/* ---------- formatting ---------- */

const STRAT_LABEL: Record<string, string> = {
  range: "range",
  trend_long: "t.long",
  trend_short: "t.short",
  tight_scalp: "scalp",
  wide_defensive: "wide",
  flat: "flat",
};

const fmtUsd = (v: number) =>
  v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
const fmtPx = (v: string) => {
  const n = parseFloat(v);
  if (!isFinite(n) || n === 0) return "—";
  return n >= 100 ? n.toFixed(2) : n.toPrecision(5);
};
const shortTx = (t: string) => (t ? `${t.slice(0, 10)}…${t.slice(-6)}` : "—");
const tone = (v: number) => (v > 0 ? "text-pos" : v < 0 ? "text-neg" : "text-fg");
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const fmtQty = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(2) : v.toPrecision(4));
/** Backend timestamps are unix seconds; tolerate ms just in case. */
const toSec = (t: number) => (t > 1e12 ? t / 1000 : t);
const fmtClock = (ts: number) =>
  new Date(toSec(ts) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const fmtDur = (sec: number) => {
  if (!isFinite(sec) || sec < 0) return "—";
  if (sec < 60) return `${Math.floor(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
  return `${Math.floor(sec / 86400)}d ${Math.floor((sec % 86400) / 3600)}h`;
};

/* ---------- tiny building blocks ---------- */

function Panel({
  title,
  children,
  className = "",
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`surface-card relative overflow-hidden p-4 ${className}`}>
      <CornerTicks />
      <h2 className="mb-3 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.22em] text-muted">
        <span aria-hidden className="inline-block h-2 w-2 rounded-[2px] bg-accent/70" />
        {title}
      </h2>
      {children}
    </section>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5 text-[13px]">
      <span className="shrink-0 text-muted">{k}</span>
      <span className="text-right font-mono tabular-nums text-fg">{children}</span>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-line bg-bg px-1 font-mono text-[10px] text-muted">
      {children}
    </kbd>
  );
}

function ModeBadge({ mode }: { mode: string }) {
  if (mode === "live")
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-neg px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wide text-white">
        <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-white" />
        live · real money
      </span>
    );
  if (mode === "paper")
    return (
      <span className="rounded-md border border-accent/50 bg-accent/15 px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wide text-accent">
        paper · no real money
      </span>
    );
  return (
    <span className="rounded-md border border-line bg-surface px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wide text-muted">
      {mode} · offline sim
    </span>
  );
}

/* ---------- the console ---------- */

export default function ConsolePage() {
  const [s, setS] = useState<ConsoleState | null>(null);
  const [online, setOnline] = useState<boolean | null>(null); // null = first load
  const [ap, setAp] = useState<AutopilotState | null>(null);
  const [apTrades, setApTrades] = useState<AutopilotTrade[]>([]);
  const [apOk, setApOk] = useState<boolean | null>(null); // null = unknown · false = API absent/unreadable
  const apDead = useRef(false); // 404 once → older backend, stop asking (no console spam)
  const [token, setToken] = useState<string | null>(null);
  const [address, setAddress] = useState<string | null>(null);
  const [authing, setAuthing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ msg: string; err: boolean } | null>(null);
  const [killArmed, setKillArmed] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const killTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const say = useCallback((msg: string, err = false) => {
    setToast({ msg, err });
    setTimeout(() => setToast(null), 4200);
  }, []);

  /* session restore + state poll.
     Hygiene: pauses while the tab is hidden (visibilitychange resumes it) and backs
     off exponentially on consecutive fetch errors — 1.5s → 3 → 6 → max 12s — resetting
     to 1.5s on the first success. */
  useEffect(() => {
    try {
      const t = sessionStorage.getItem("gridora-console-token");
      const a = sessionStorage.getItem("gridora-console-address");
      if (t && a) {
        setToken(t);
        setAddress(a);
      }
    } catch {}
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const BASE = 1500;
    const MAX = 12000;
    let delay = BASE;

    function schedule() {
      if (!alive) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void tick(), delay);
    }

    async function tick() {
      if (!alive) return;
      if (document.hidden) return; // paused — visibilitychange restarts the loop
      try {
        const st = await fetchConsoleState();
        if (!alive) return;
        setS(st);
        setOnline(true);
        delay = BASE; // success → reset backoff
        if (!apDead.current) {
          try {
            const [a, t] = await Promise.all([fetchAutopilot(), fetchAutopilotTrades(20)]);
            if (!alive) return;
            setAp(a);
            setApTrades(Array.isArray(t.trades) ? t.trades : []);
            setApOk(true);
          } catch (e) {
            if (!alive) return;
            // Older backend without the autopilot API → 404: remember and stop asking.
            if ((e as Error & { status?: number }).status === 404) apDead.current = true;
            setApOk(false);
          }
        }
      } catch {
        if (!alive) return;
        setOnline(false);
        delay = Math.min(delay * 2, MAX); // consecutive errors → back off
      }
      schedule();
    }

    const onVis = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      if (!document.hidden) void tick(); // back in view → refresh immediately
    };
    document.addEventListener("visibilitychange", onVis);
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  /* log autoscroll */
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [s?.log]);

  const logout = useCallback(() => {
    setToken(null);
    setAddress(null);
    try {
      sessionStorage.removeItem("gridora-console-token");
      sessionStorage.removeItem("gridora-console-address");
    } catch {}
  }, []);

  const connect = useCallback(async () => {
    setAuthing(true);
    try {
      const { token: t, address: a } = await walletLogin();
      setToken(t);
      setAddress(a);
      try {
        sessionStorage.setItem("gridora-console-token", t);
        sessionStorage.setItem("gridora-console-address", a);
      } catch {}
      say("Owner verified — controls unlocked.");
    } catch (e) {
      say(e instanceof Error ? e.message : "Wallet sign-in failed", true);
    } finally {
      setAuthing(false);
    }
  }, [say]);

  const act = useCallback(
    async (a: ControlAction, label: string) => {
      if (!token) return;
      setBusy(true);
      try {
        await sendControl(token, a);
        say(label);
      } catch (e) {
        const status = (e as Error & { status?: number }).status;
        if (status === 401) {
          logout();
          say("Session expired — connect your wallet again.", true);
        } else {
          say(e instanceof Error ? e.message : "Action failed", true);
        }
      } finally {
        setBusy(false);
      }
    },
    [token, say, logout],
  );

  /* derived */
  const lo = parseFloat(s?.grid.band[0] ?? "0");
  const hi = parseFloat(s?.grid.band[1] ?? "0");
  const halfBand = hi + lo > 0 ? (hi - lo) / (hi + lo) : 0.06;
  const hasGrid = !!s && s.is_running && s.active_strategy !== "flat" && hi > 0;
  const unlocked = !!token;

  const retune = useCallback(
    (patch: { half_band?: number; levels?: number; bias?: number }, label: string) => {
      if (!s || !hasGrid) {
        say("No live grid — pick a strategy first.", true);
        return;
      }
      void act({ action: "strategy", key: s.active_strategy, ...patch }, label);
    },
    [s, hasGrid, act, say],
  );

  const kill = useCallback(() => {
    if (!killArmed) {
      setKillArmed(true);
      if (killTimer.current) clearTimeout(killTimer.current);
      killTimer.current = setTimeout(() => setKillArmed(false), 3000);
      return;
    }
    setKillArmed(false);
    void act({ action: "halt" }, "Kill → flat sent.");
  }, [killArmed, act]);

  const pick = useCallback(
    (key: string) => void act({ action: "strategy", key }, `Strategy → ${STRAT_LABEL[key] ?? key}`),
    [act],
  );

  /* the exact TUI keybindings */
  useEffect(() => {
    if (!unlocked || !s) return;
    const keys = Object.keys(s.strategies);
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable) return;
      if (e.key === "a") void act({ action: "autopilot", on: !s.autopilot }, s.autopilot ? "Manual — you drive." : "Autopilot ON.");
      else if (e.key === "d") void act({ action: "decide" }, "Decide now → Claude.");
      else if (e.key === "k") kill();
      else if (e.key === "[") retune({ half_band: clamp(halfBand - 0.01, 0.01, 0.25) }, "Band narrower.");
      else if (e.key === "]") retune({ half_band: clamp(halfBand + 0.01, 0.01, 0.25) }, "Band wider.");
      else if (e.key === ",") retune({ levels: clamp(s.grid.levels - 2, 4, 50) }, "Fewer levels.");
      else if (e.key === ".") retune({ levels: clamp(s.grid.levels + 2, 4, 50) }, "More levels.");
      else if (e.key === "b") retune({ bias: s.grid.bias >= 1 ? -1 : s.grid.bias + 1 }, "Bias cycled.");
      else if (/^[1-6]$/.test(e.key)) {
        const k = keys[parseInt(e.key, 10) - 1];
        if (k) pick(k);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [unlocked, s, act, kill, retune, pick, halfBand]);

  /* ---------- render ---------- */

  const fg = s?.regime?.fear_greed ?? 0;
  const fgTone = fg <= 25 ? "text-neg" : fg >= 75 ? "text-pos" : "text-accent";
  const conf = s?.decision?.confidence ?? "";
  const confTone = conf === "HIGH" ? "text-pos" : conf === "LOW" ? "text-neg" : "text-accent";
  const equitySeries = (s?.equity_history ?? []).map(([, eq]) => eq);
  const pnlPct = s?.account.pnl_pct ?? 0;

  return (
    <div className="relative z-10 flex min-h-dvh flex-col">
      {/* HEADER */}
      <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur supports-[backdrop-filter]:bg-bg/70">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-3 sm:px-8">
          <div className="flex items-center gap-3">
            <Wordmark />
            <span className="rounded-md border border-line px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-muted">
              console
            </span>
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <span
              className={`inline-flex items-center gap-1.5 font-mono text-[11px] ${
                online ? "text-pos" : "text-neg"
              }`}
              title={online ? "Agent reachable" : `No agent at ${CONTROL_API}`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${online ? "bg-pos animate-pulse-soft" : "bg-neg"}`}
              />
              {online ? "agent online" : "agent offline"}
            </span>
            {unlocked ? (
              <button
                type="button"
                onClick={logout}
                title="Disconnect"
                className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-pos/50 bg-pos/10 px-2.5 py-1.5 font-mono text-[11px] text-pos transition-colors hover:border-pos"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-pos" />
                {address ? `${address.slice(0, 6)}…${address.slice(-4)}` : "operator"} · operator
              </button>
            ) : (
              <button
                type="button"
                onClick={connect}
                disabled={authing || online === false}
                className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-accent/60 bg-accent/10 px-3 py-1.5 text-[12px] font-semibold text-accent transition-colors hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <svg aria-hidden viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="5" y="11" width="14" height="9" rx="2" />
                  <path d="M8 11V7a4 4 0 1 1 8 0v4" />
                </svg>
                {authing ? "Signing…" : "Connect wallet"}
              </button>
            )}
          </div>
        </div>
        {/* status strip */}
        <div className="border-t border-line/60 bg-surface/50">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 font-mono text-[11px] text-muted sm:px-8">
            {s ? (
              <>
                <ModeBadge mode={s.mode} />
                <span className="font-semibold text-fg">{s.market || "—"}</span>
                <span>
                  status <span className="text-fg">{s.status}</span>
                </span>
                <span>
                  {s.autopilot ? (
                    <span className="font-bold text-pos">● AUTO</span>
                  ) : (
                    <span className="font-bold text-accent">○ MANUAL</span>
                  )}
                </span>
                <span className="hidden sm:inline">venue {s.venue}</span>
                <span className="hidden sm:inline">chain {s.chain_id}</span>
              </>
            ) : (
              <span>waiting for agent…</span>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 pb-40 pt-5 sm:px-8">
        {online === false && (
          <div className="mb-5 rounded-xl border border-line bg-surface p-5 text-sm">
            <p className="font-display font-semibold lowercase text-fg">
              this console lives on the operator&apos;s machine.
            </p>
            <p className="mt-1.5 text-muted">
              it drives the agent over a local control API ({CONTROL_API}) — nothing to see
              here unless you run the agent yourself. the public, verifiable trading record
              is on the{" "}
              <a href="/" className="link-quiet text-accent">homepage</a>.
            </p>
            <p className="mt-2 text-[12px] text-faint">
              operator? start the engine with{" "}
              <code className="rounded bg-bg px-1.5 py-0.5 font-mono text-[12px] text-fg">
                python -m gridora.runner --mode paper --auto --serve
              </code>
            </p>
          </div>
        )}

        {/* PANELS */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {/* Account */}
          <Panel title="Account">
            <div className="flex items-end justify-between gap-3">
              <div>
                <div className={`font-mono text-3xl font-bold tabular-nums tracking-tight ${tone(pnlPct)}`}>
                  ${fmtUsd(s?.account.equity ?? 0)}
                </div>
                <div className={`mt-0.5 font-mono text-sm tabular-nums ${tone(pnlPct)}`}>
                  {fmtPct(pnlPct)}{" "}
                  <span className="text-muted">from ${fmtUsd(s?.account.start_equity ?? 0)}</span>
                </div>
              </div>
              {pnlPct <= -10 && (
                <span className="rounded-md bg-neg/15 px-2 py-1 font-mono text-[10px] font-bold uppercase text-neg">
                  ⚠ drawdown
                </span>
              )}
            </div>
            <div className={`mt-2 h-14 ${tone(pnlPct)}`}>
              {equitySeries.length > 1 ? (
                <Sparkline data={equitySeries} className="h-full w-full" />
              ) : (
                <div className="flex h-full items-center font-mono text-[11px] text-faint">
                  collecting equity samples…
                </div>
              )}
            </div>
            <div className="mt-2 border-t border-line/70 pt-2">
              <Row k="closed P/L">
                <span className={tone(s?.account.cum_realized ?? 0)}>
                  {(s?.account.cum_realized ?? 0) >= 0 ? "+" : ""}
                  {(s?.account.cum_realized ?? 0).toFixed(3)}
                </span>
              </Row>
              <Row k="open P/L">
                <span className={tone((s?.grid.realized ?? 0) + (s?.grid.unrealized ?? 0))}>
                  {((s?.grid.realized ?? 0) + (s?.grid.unrealized ?? 0) >= 0 ? "+" : "") +
                    ((s?.grid.realized ?? 0) + (s?.grid.unrealized ?? 0)).toFixed(3)}
                </span>
              </Row>
              <Row k="trades · win">
                {s?.account.episodes ?? 0} · {(s?.account.win_rate ?? 0).toFixed(0)}%
              </Row>
            </div>
          </Panel>

          {/* Regime */}
          <Panel title="Regime · CoinMarketCap">
            {s?.regime ? (
              <>
                <div className="flex items-baseline justify-between">
                  <span className="font-mono text-xl font-bold text-fg">{s.regime.label}</span>
                  {s.regime.stale && (
                    <span className="rounded bg-neg/15 px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase text-neg">
                      stale feed
                    </span>
                  )}
                </div>
                <div className="mt-3">
                  <div className="flex items-baseline justify-between text-[13px]">
                    <span className="text-muted">fear &amp; greed</span>
                    <span className={`font-mono font-bold tabular-nums ${fgTone}`}>
                      {s.regime.fear_greed}/100
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line/70">
                    <div
                      className={`h-full rounded-full transition-[width] duration-500 ${
                        fg <= 25 ? "bg-neg" : fg >= 75 ? "bg-pos" : "bg-accent"
                      }`}
                      style={{ width: `${clamp(fg, 2, 100)}%` }}
                    />
                  </div>
                </div>
                <div className="mt-3 border-t border-line/70 pt-2">
                  <Row k="momentum">
                    <span className={tone(s.regime.momentum)}>
                      {s.regime.momentum >= 0 ? "+" : ""}
                      {s.regime.momentum.toFixed(2)}
                    </span>
                  </Row>
                  <Row k="funding">{s.regime.funding_bps} bps</Row>
                </div>
              </>
            ) : (
              <p className="font-mono text-[12px] text-faint">awaiting first regime read…</p>
            )}
          </Panel>

          {/* Strategy router */}
          <Panel title="Strategy router · Claude">
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(s?.strategies ?? {}).map(([key, enabled], i) => {
                const active = key === s?.active_strategy;
                return (
                  <span
                    key={key}
                    className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${
                      active
                        ? "border-pos/60 bg-pos/15 font-bold text-pos"
                        : enabled
                          ? "border-line text-muted"
                          : "border-line/50 text-faint line-through"
                    }`}
                  >
                    <span className="opacity-50">{i + 1}·</span>
                    {STRAT_LABEL[key] ?? key}
                  </span>
                );
              })}
            </div>
            {s?.decision ? (
              <div className="mt-3 border-t border-line/70 pt-2">
                <div className="flex items-center justify-between font-mono text-[12px]">
                  <span className="text-fg">
                    {s.decision.source.startsWith("claude") ? "🤖" : "⚙"} {s.decision.source} →{" "}
                    <b>{STRAT_LABEL[s.decision.strategy_key] ?? s.decision.strategy_key}</b>
                  </span>
                  <span className={`font-bold ${confTone}`}>{s.decision.confidence}</span>
                </div>
                <p className="mt-1.5 text-[12px] italic leading-relaxed text-muted">
                  “{s.decision.reasoning}”
                </p>
                {(s.decision.cost_usd > 0 || s.decision.latency_ms > 0) && (
                  <p className="mt-1 font-mono text-[10px] text-faint">
                    ${s.decision.cost_usd.toFixed(4)} · {s.decision.latency_ms}ms
                  </p>
                )}
              </div>
            ) : (
              <p className="mt-3 font-mono text-[12px] text-faint">no decision yet…</p>
            )}
          </Panel>

          {/* Live grid */}
          <Panel title="Live grid">
            {hasGrid && s ? (
              <>
                {/* band rail: level ticks + avg-entry marker */}
                <div className="relative mt-1 h-9">
                  <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-accent/15" />
                  {Array.from({ length: Math.min(s.grid.levels, 24) }, (_, i) => (
                    <span
                      key={i}
                      className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-accent/60"
                      style={{ left: `${(i / (Math.min(s.grid.levels, 24) - 1)) * 100}%` }}
                    />
                  ))}
                  {parseFloat(s.grid.net_base) !== 0 && hi > lo && (
                    <span
                      title={`avg entry ${fmtPx(s.grid.avg_entry)}`}
                      className="absolute top-1/2 h-5 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-fg"
                      style={{
                        left: `${clamp(((parseFloat(s.grid.avg_entry) - lo) / (hi - lo)) * 100, 0, 100)}%`,
                      }}
                    />
                  )}
                </div>
                <div className="flex justify-between font-mono text-[11px] tabular-nums text-muted">
                  <span>{fmtPx(s.grid.band[0])}</span>
                  <span className="text-accent">±{(halfBand * 100).toFixed(1)}%</span>
                  <span>{fmtPx(s.grid.band[1])}</span>
                </div>
                <div className="mt-3 border-t border-line/70 pt-2">
                  <Row k="levels · bias">
                    {s.grid.levels} ·{" "}
                    {s.grid.bias > 0 ? "long +1" : s.grid.bias < 0 ? "short −1" : "sym 0"}
                  </Row>
                  <Row k="net position">
                    <span className={tone(parseFloat(s.grid.net_base))}>
                      {parseFloat(s.grid.net_base) >= 0 ? "+" : ""}
                      {parseFloat(s.grid.net_base).toPrecision(4)} base
                    </span>
                  </Row>
                  <Row k="avg entry">{fmtPx(s.grid.avg_entry)}</Row>
                  <Row k="resting · fills">
                    {s.grid.resting_orders} · {s.grid.fills}
                  </Row>
                </div>
              </>
            ) : (
              <p className="font-mono text-[12px] text-faint">
                flat — no live grid. {unlocked ? "Pick a strategy below." : ""}
              </p>
            )}
          </Panel>

          {/* On-chain proofs */}
          <Panel title="On-chain proofs · BSC">
            <Row k="identity">{shortTx(s?.onchain.identity_tx ?? "")}</Row>
            <Row k="commit">{shortTx(s?.onchain.commit_tx ?? "")}</Row>
            <Row k="attest">{shortTx(s?.onchain.attest_tx ?? "")}</Row>
            <Row k="journal">{s?.onchain.journal_count ?? 0} settled</Row>
            <p className="mt-3 border-t border-line/70 pt-2 text-[11px] leading-relaxed text-muted">
              Config hash committed <em>before</em> trading, outcome attested after —
              audit it on the{" "}
              <a href="/verifier" className="link-quiet text-accent">
                public verifier
              </a>
              .
            </p>
          </Panel>

          {/* History */}
          <Panel title="Trade history">
            {s && s.episode_history.length > 0 ? (
              <div className="max-h-44 space-y-0.5 overflow-y-auto font-mono text-[12px] tabular-nums">
                {[...s.episode_history].reverse().map(([idx, strat, bps, realized]) => (
                  <div key={idx} className="flex items-baseline justify-between gap-2">
                    <span className="text-faint">#{idx}</span>
                    <span className="flex-1 text-muted">{STRAT_LABEL[strat] ?? strat}</span>
                    <span className={tone(bps)}>
                      {bps >= 0 ? "+" : ""}
                      {bps}bps
                    </span>
                    <span className={`w-16 text-right ${tone(realized)}`}>
                      {realized >= 0 ? "+" : ""}
                      {realized.toFixed(3)}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="font-mono text-[12px] text-faint">no settled trades yet…</p>
            )}
          </Panel>

          {/* Autopilot — the momentum picker's own read model (public endpoints).
              Degrades to a one-line notice on backends that don't serve it yet. */}
          <Panel title="Autopilot" className="md:col-span-2 xl:col-span-3">
            {apOk === false ? (
              <p className="font-mono text-[12px] text-faint">
                autopilot API not available — this backend doesn&apos;t serve{" "}
                <code className="font-mono">/api/autopilot</code> yet.
              </p>
            ) : !ap ? (
              <p className="font-mono text-[12px] text-faint">awaiting autopilot state…</p>
            ) : (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {/* status + position */}
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    {ap.running ? (
                      ap.paper !== undefined ? (
                        <span className="rounded-md border border-accent/50 bg-accent/15 px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wide text-accent">
                          ● running · paper
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 rounded-md bg-neg px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wide text-white">
                          <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-white" />
                          running · live
                        </span>
                      )
                    ) : (
                      <span className="rounded-md border border-line bg-surface px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wide text-muted">
                        ○ stopped · stale
                      </span>
                    )}
                    {ap.pending && (
                      <span className="font-mono text-[11px] text-muted">
                        pending {ap.pending.sym} ×{ap.pending.count}
                      </span>
                    )}
                    <span className="ml-auto font-mono text-[10px] text-faint">
                      upd {fmtDur(Date.now() / 1000 - toSec(ap.updated))} ago
                    </span>
                  </div>
                  {ap.pos ? (
                    <div className="mt-2 border-t border-line/70 pt-2">
                      <Row k="position">
                        <span className="font-semibold">{ap.pos.sym}</span>
                      </Row>
                      <Row k="qty · cost">
                        {fmtQty(ap.pos.qty)} · ${fmtUsd(ap.pos.cost)}
                      </Row>
                      <Row k="entry (eff)">{fmtPx(String(ap.pos.eff))}</Row>
                      <Row k="held for">{fmtDur(Date.now() / 1000 - toSec(ap.pos.ts))}</Row>
                      <Row k="peak">{ap.peak !== null ? fmtPx(String(ap.peak)) : "—"}</Row>
                    </div>
                  ) : (
                    <p className="mt-2 border-t border-line/70 pt-2 font-mono text-[12px] text-faint">
                      flat — no open position.
                    </p>
                  )}
                  {ap.paper?.USDT !== undefined && (
                    <Row k="cash (paper)">${fmtUsd(ap.paper.USDT)}</Row>
                  )}
                </div>

                {/* recent autopilot trades */}
                <div>
                  <div className="mb-1 flex items-baseline gap-2 font-mono text-[10px] uppercase tracking-widest text-faint">
                    <span className="w-10">time</span>
                    <span className="w-8">ev</span>
                    <span className="w-14">sym</span>
                    <span className="ml-auto">net · bps</span>
                    <span className="w-24 text-right">reason</span>
                  </div>
                  {apTrades.length > 0 ? (
                    <div className="max-h-44 space-y-0.5 overflow-y-auto font-mono text-[12px] tabular-nums">
                      {apTrades.map((t, i) => (
                        <div key={`${t.ts}-${t.sym}-${i}`} className="flex items-baseline gap-2">
                          <span className="w-10 shrink-0 text-faint">{fmtClock(t.ts)}</span>
                          <span className={`w-8 shrink-0 ${t.event === "sell" ? "text-fg" : "text-muted"}`}>
                            {t.event}
                          </span>
                          <span className="w-14 shrink-0 truncate font-semibold text-fg">{t.sym}</span>
                          <span className={`ml-auto text-right ${t.net !== undefined ? tone(t.net) : "text-faint"}`}>
                            {t.net !== undefined
                              ? `${t.net >= 0 ? "+" : "−"}$${Math.abs(t.net).toFixed(2)}`
                              : "—"}
                            {t.bps !== undefined && (
                              <span className="ml-1.5">
                                {t.bps >= 0 ? "+" : ""}
                                {t.bps}bps
                              </span>
                            )}
                          </span>
                          <span className="w-24 shrink-0 truncate text-right text-faint" title={t.reason}>
                            {t.reason ?? ""}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="font-mono text-[12px] text-faint">no autopilot trades yet…</p>
                  )}
                </div>
              </div>
            )}
          </Panel>
        </div>

        {/* LOG */}
        <section className="surface-card relative mt-4 overflow-hidden">
          <h2 className="flex items-center gap-2 border-b border-line/70 px-4 py-2 text-[10px] font-semibold uppercase tracking-[0.22em] text-muted">
            <span aria-hidden className="inline-block h-2 w-2 rounded-[2px] bg-accent/70" />
            Log
            <span className="ml-auto font-mono normal-case tracking-normal text-faint">
              tail · live
            </span>
          </h2>
          <div
            ref={logRef}
            className="card-grid h-48 overflow-y-auto px-4 py-2 font-mono text-[11.5px] leading-[1.7]"
          >
            {(s?.log ?? []).map((line, i) => (
              <div key={i} className="whitespace-pre-wrap break-all text-muted">
                <span className="select-none text-accent/60">❯ </span>
                <span className={/HALT|error|failed|⚠/i.test(line) ? "text-neg" : /launched|✓|attested/i.test(line) ? "text-fg" : ""}>
                  {line}
                </span>
              </div>
            ))}
            {(!s || s.log.length === 0) && <span className="text-faint">…</span>}
          </div>
        </section>
      </main>

      {/* CONTROL DECK — sticky bottom, wallet-gated */}
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-bg/90 backdrop-blur">
        <div className="mx-auto max-w-7xl px-4 py-3 sm:px-8">
          {!unlocked ? (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3 text-sm text-muted">
                <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4 text-accent" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="5" y="11" width="14" height="9" rx="2" />
                  <path d="M8 11V7a4 4 0 1 1 8 0v4" />
                </svg>
                {s && !s.owner_set ? (
                  <span>
                    Controls locked — the agent&apos;s operator allowlist (<code className="font-mono text-[12px]">GRIDORA_OWNER</code>) is empty.
                  </span>
                ) : (
                  <span>
                    Watching is public. <b className="text-fg">Driving needs an allowlisted wallet.</b>
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={connect}
                disabled={authing || online === false || (s ? !s.owner_set : false)}
                className="cursor-pointer rounded-lg bg-accent px-4 py-2 text-sm font-bold text-bg transition-transform hover:scale-[1.02] disabled:cursor-not-allowed disabled:opacity-40"
              >
                {authing ? "Check your wallet…" : "Connect wallet to take control"}
              </button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {/* auto/manual */}
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(
                    { action: "autopilot", on: !s?.autopilot },
                    s?.autopilot ? "Manual — you drive." : "Autopilot ON.",
                  )
                }
                className={`cursor-pointer rounded-md border px-2.5 py-1.5 font-mono text-[12px] font-bold transition-colors disabled:opacity-50 ${
                  s?.autopilot
                    ? "border-pos/60 bg-pos/15 text-pos"
                    : "border-accent/60 bg-accent/15 text-accent"
                }`}
                title="Toggle autopilot (a)"
              >
                {s?.autopilot ? "● AUTO" : "○ MANUAL"}
              </button>

              <span className="mx-1 hidden h-6 w-px bg-line sm:block" />

              {/* strategy picks */}
              {Object.entries(s?.strategies ?? {}).map(([key, enabled], i) => (
                <button
                  key={key}
                  type="button"
                  disabled={busy || !enabled}
                  onClick={() => pick(key)}
                  title={`Switch to ${key} (${i + 1})`}
                  className={`cursor-pointer rounded-md border px-2.5 py-1.5 font-mono text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                    key === s?.active_strategy
                      ? "border-pos/60 bg-pos/15 font-bold text-pos"
                      : key === "flat"
                        ? "border-line text-muted hover:border-neg/60 hover:text-neg"
                        : "border-line text-muted hover:border-accent/60 hover:text-fg"
                  }`}
                >
                  {STRAT_LABEL[key] ?? key}
                </button>
              ))}

              <span className="mx-1 hidden h-6 w-px bg-line sm:block" />

              {/* tuners */}
              {(
                [
                  ["band", `±${(halfBand * 100).toFixed(1)}%`,
                    () => retune({ half_band: clamp(halfBand - 0.01, 0.01, 0.25) }, "Band narrower."),
                    () => retune({ half_band: clamp(halfBand + 0.01, 0.01, 0.25) }, "Band wider.")],
                  ["lvl", `${s?.grid.levels ?? 0}`,
                    () => retune({ levels: clamp((s?.grid.levels ?? 10) - 2, 4, 50) }, "Fewer levels."),
                    () => retune({ levels: clamp((s?.grid.levels ?? 10) + 2, 4, 50) }, "More levels.")],
                ] as const
              ).map(([label, value, dec, inc]) => (
                <span
                  key={label}
                  className={`inline-flex items-center overflow-hidden rounded-md border border-line font-mono text-[12px] ${hasGrid ? "" : "opacity-40"}`}
                >
                  <button type="button" disabled={busy || !hasGrid} onClick={dec} className="cursor-pointer px-2 py-1.5 text-muted transition-colors hover:bg-surface hover:text-fg disabled:cursor-not-allowed">−</button>
                  <span className="border-x border-line px-2 py-1.5 tabular-nums text-fg">
                    {label} {value}
                  </span>
                  <button type="button" disabled={busy || !hasGrid} onClick={inc} className="cursor-pointer px-2 py-1.5 text-muted transition-colors hover:bg-surface hover:text-fg disabled:cursor-not-allowed">+</button>
                </span>
              ))}
              <button
                type="button"
                disabled={busy || !hasGrid}
                onClick={() => retune({ bias: (s?.grid.bias ?? 0) >= 1 ? -1 : (s?.grid.bias ?? 0) + 1 }, "Bias cycled.")}
                title="Cycle bias (b)"
                className="cursor-pointer rounded-md border border-line px-2.5 py-1.5 font-mono text-[12px] text-muted transition-colors hover:border-accent/60 hover:text-fg disabled:cursor-not-allowed disabled:opacity-40"
              >
                bias {(s?.grid.bias ?? 0) > 0 ? "+1" : (s?.grid.bias ?? 0) < 0 ? "−1" : "0"}
              </button>

              <span className="mx-1 hidden h-6 w-px bg-line sm:block" />

              <button
                type="button"
                disabled={busy}
                onClick={() => void act({ action: "decide" }, "Decide now → Claude.")}
                title="Force a Claude decision (d)"
                className="cursor-pointer rounded-md border border-accent/60 bg-accent/10 px-2.5 py-1.5 font-mono text-[12px] font-bold text-accent transition-colors hover:bg-accent/20 disabled:opacity-50"
              >
                ⚡ decide
              </button>

              <button
                type="button"
                disabled={busy}
                onClick={kill}
                title="Flatten everything to stablecoin (k)"
                className={`ml-auto cursor-pointer rounded-md border px-3 py-1.5 font-mono text-[12px] font-bold transition-colors disabled:opacity-50 ${
                  killArmed
                    ? "animate-pulse-soft border-neg bg-neg text-white"
                    : "border-neg/60 bg-neg/10 text-neg hover:bg-neg/20"
                }`}
              >
                {killArmed ? "CONFIRM KILL → FLAT" : "◉ kill"}
              </button>

              <span className="hidden w-full items-center gap-1.5 pt-1 text-[10px] text-faint lg:flex">
                <Kbd>a</Kbd> auto · <Kbd>1-6</Kbd> strategy · <Kbd>[</Kbd>
                <Kbd>]</Kbd> band · <Kbd>,</Kbd>
                <Kbd>.</Kbd> levels · <Kbd>b</Kbd> bias · <Kbd>d</Kbd> decide · <Kbd>k</Kbd> kill —
                same keys as the terminal TUI
              </span>
            </div>
          )}
        </div>
      </div>

      {/* toast */}
      {toast && (
        <div
          role="status"
          aria-live="polite"
          className={`fixed bottom-24 right-4 z-40 max-w-sm rounded-lg border px-4 py-2.5 font-mono text-[12px] shadow-lg backdrop-blur ${
            toast.err
              ? "border-neg/50 bg-neg/10 text-neg"
              : "border-pos/50 bg-pos/10 text-pos"
          }`}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}
