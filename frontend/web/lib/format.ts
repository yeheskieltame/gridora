// Pure formatting helpers — no React, importable from server and client.

export function fmtAddress(a: `0x${string}` | null | undefined): string {
  if (!a) return "n/a";
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

export function fmtHash(h: `0x${string}`): string {
  return `${h.slice(0, 10)}…${h.slice(-6)}`;
}

export function fmtBps(bps: bigint): string {
  const pct = Number(bps) / 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

export function fmtTimestamp(unix: bigint): string {
  const d = new Date(Number(unix) * 1000);
  return d.toISOString().replace("T", " ").slice(0, 19) + "Z";
}

export function fmtRelative(unix: bigint, now: number = Date.now()): string {
  const ms = now - Number(unix) * 1000;
  const sec = Math.max(1, Math.floor(ms / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}
