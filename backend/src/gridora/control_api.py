"""Web control plane — the TUI's operator controls over HTTP for the /console cockpit.

stdlib-only (ThreadingHTTPServer in a daemon thread); every action hops onto the
agent's asyncio loop via run_coroutine_threadsafe, so the supervisor stays
single-threaded. Auth is a wallet signature (SIWE-lite):

    GET  /api/auth/nonce   -> {nonce, message}
    (an allowlisted wallet signs `message` with personal_sign)
    POST /api/auth/verify  {address, signature, nonce} -> {token}
    POST /api/control      Authorization: Bearer <token>  {action, ...}

Only wallets on the GRIDORA_OWNER allowlist (comma-separated addresses) can drive;
with it empty every control call is refused (default-deny). GET /api/state is
public — it exposes the same facts the public verifier page already shows, as are
GET /api/autopilot (the out-of-process autopilot's state file) and GET /api/trades
(the append-only trade ledger tail). Auth and control routes are rate-limited
per client IP; CORS origin comes from GRIDORA_CORS_ORIGIN (default "*").
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable
from urllib.parse import parse_qs

from eth_account import Account
from eth_account.messages import encode_defunct

from .agent.state import GridoraState

NONCE_TTL_S = 300.0
TOKEN_TTL_S = 24 * 3600.0
ACTION_TIMEOUT_S = 90.0   # live force_strategy does on-chain commit + order placement

# Out-of-process autopilot state (written atomically by scripts/autopilot.py).
AUTOPILOT_F = os.path.expanduser("~/.gridora/autopilot.json")
LEDGER_F = os.path.expanduser("~/.gridora/trades.jsonl")
AUTOPILOT_FRESH_S = 300.0        # state file older than this = autopilot not running
TRADES_LIMIT_DEFAULT = 50
TRADES_LIMIT_MAX = 200
_TAIL_BYTES_PER_TRADE = 2048     # generous per-line budget; one ledger line is way under this

# Per-IP request budgets (requests per 60 s sliding window).
RATE_AUTH_PER_MIN = 10           # /api/auth/nonce + /api/auth/verify share one bucket
RATE_CONTROL_PER_MIN = 30        # /api/control


def _message_for(nonce: str) -> str:
    return ("Gridora console — sign to prove you own this agent.\n"
            f"nonce: {nonce}")


class AuthStore:
    """Nonce + bearer-token bookkeeping. Thread-safe via one lock (requests come
    from server threads)."""

    def __init__(self, owners: str | Iterable[str]) -> None:
        # Accept "0xa,0xb" (the GRIDORA_OWNER env format) or any iterable of addresses.
        if isinstance(owners, str):
            owners = owners.replace(";", ",").split(",")
        self.owners = frozenset(a.strip().lower() for a in owners if a and a.strip())
        self._nonces: dict[str, float] = {}      # nonce -> expiry
        self._tokens: dict[str, float] = {}      # token -> expiry
        self._lock = threading.Lock()

    def issue_nonce(self) -> tuple[str, str]:
        nonce = secrets.token_hex(16)
        with self._lock:
            now = time.time()
            self._nonces = {n: t for n, t in self._nonces.items() if t > now}
            self._nonces[nonce] = now + NONCE_TTL_S
        return nonce, _message_for(nonce)

    def verify(self, address: str, signature: str, nonce: str) -> str:
        """Recover the signer of the nonce message; must equal the claimed address
        AND be on the operator allowlist. Nonce is single-use. Returns a token."""
        if not self.owners:
            raise PermissionError("GRIDORA_OWNER allowlist is empty — controls are locked")
        with self._lock:
            expiry = self._nonces.pop(nonce, 0.0)
        if expiry < time.time():
            raise PermissionError("nonce expired or unknown — fetch a fresh one")
        recovered = Account.recover_message(
            encode_defunct(text=_message_for(nonce)), signature=signature).lower()
        if recovered != address.lower() or recovered not in self.owners:
            raise PermissionError("wallet is not on the operator allowlist")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = time.time() + TOKEN_TTL_S
        return token

    def check_token(self, token: str) -> bool:
        with self._lock:
            return self._tokens.get(token, 0.0) > time.time()


class RateLimiter:
    """Per-(IP, bucket) sliding-window limiter. In-memory + thread-safe — enough to
    blunt signature brute-force and control-spam once the API faces the internet."""

    _GC_KEYS = 4096   # opportunistic sweep threshold so dead IPs don't pile up

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str, bucket: str, limit: int, window_s: float = 60.0,
              *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        key = (ip, bucket)
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if t > now - window_s]
            ok = len(hits) < limit
            if ok:
                hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > self._GC_KEYS:
                self._hits = {k: v for k, v in self._hits.items()
                              if v and v[-1] > now - window_s}
            return ok


def autopilot_json(path: str | os.PathLike = AUTOPILOT_F, *,
                   now: float | None = None) -> dict[str, Any]:
    """~/.gridora/autopilot.json (the out-of-process autopilot's state) + freshness.
    Never raises: missing or malformed file -> a flat 'not running' shape."""
    now = time.time() if now is None else now
    try:
        with open(path) as f:
            snap = json.load(f)
        if not isinstance(snap, dict):
            raise ValueError("state file is not a JSON object")
        mtime = os.stat(path).st_mtime
    except Exception:  # noqa: BLE001 — a public read endpoint must never 500
        return {"pos": None, "running": False}
    snap["updated"] = mtime
    snap["running"] = (now - mtime) < AUTOPILOT_FRESH_S
    return snap


def trades_tail(path: str | os.PathLike = LEDGER_F,
                limit: int = TRADES_LIMIT_DEFAULT) -> list[dict]:
    """Newest-first tail of the append-only trades.jsonl. Reads only a bounded tail
    of the file (never the whole ledger); skips torn/garbage lines; never raises."""
    limit = max(1, min(int(limit), TRADES_LIMIT_MAX))
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            start = max(0, f.tell() - limit * _TAIL_BYTES_PER_TRADE)
            f.seek(start)
            lines = f.read().split(b"\n")
    except OSError:
        return []
    if start > 0:
        lines = lines[1:]   # seeked mid-file — the first line is (likely) partial
    trades: list[dict] = []
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue   # blank tail line or a torn mid-append write
        if isinstance(event, dict):
            trades.append(event)
    return trades[-limit:][::-1]


def cors_headers(origin: str) -> list[tuple[str, str]]:
    """The CORS header set echoed on every response. `origin` comes from
    GRIDORA_CORS_ORIGIN — "*" keeps the historical wide-open default; a real
    origin locks the browser surface to the deployed console."""
    headers = [
        ("Access-Control-Allow-Origin", origin or "*"),
        ("Access-Control-Allow-Headers", "Content-Type, Authorization"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        # Chrome Private Network Access: an https console page may call 127.0.0.1
        ("Access-Control-Allow-Private-Network", "true"),
    ]
    if origin and origin != "*":
        headers.append(("Vary", "Origin"))   # caches must key on the echoed origin
    return headers


def state_json(s: GridoraState, owner_set: bool) -> dict[str, Any]:
    """GridoraState -> JSON-safe snapshot (the web cockpit's read model)."""
    r, d = s.regime, s.last_decision
    return {
        "market": s.market, "mode": s.mode, "venue": s.venue, "chain_id": s.chain_id,
        "agent_address": s.agent_address, "status": s.status,
        "autopilot": s.autopilot, "is_running": s.is_running,
        "active_strategy": s.active_strategy,
        "strategies": dict(s.strategies),
        "owner_set": owner_set,
        "regime": None if r is None else {
            "label": getattr(r, "label", ""),
            "fear_greed": int(getattr(r, "fear_greed", 0)),
            "momentum": float(getattr(r, "momentum", 0) or 0),
            "funding_bps": int(getattr(r, "funding_bps", 0)),
            "stale": bool(getattr(r, "stale", False)),
        },
        "decision": None if d is None else {
            "strategy_key": d.strategy_key, "reasoning": d.reasoning,
            "confidence": d.confidence, "source": d.source,
            "cost_usd": float(getattr(d, "cost_usd", 0) or 0),
            "latency_ms": int(getattr(d, "latency_ms", 0) or 0),
        },
        "grid": {
            "band": [str(s.band[0]), str(s.band[1])],
            "levels": s.levels, "bias": s.bias,
            "net_base": str(s.net_base), "avg_entry": str(s.avg_entry),
            "resting_orders": s.resting_orders, "fills": s.fills,
            "realized": float(s.realized_pnl), "unrealized": float(s.unrealized_pnl),
            "pnl_bps": s.pnl_bps, "max_dd_bps": s.max_dd_bps,
        },
        "account": {
            "start_equity": float(s.start_equity),
            "equity": float(s.account_equity),
            "pnl_pct": s.account_pnl_pct,
            "cum_realized": float(s.cum_realized),
            "episodes": s.episodes, "wins": s.episode_wins,
            "win_rate": s.account_win_rate,
        },
        "equity_history": [[round(ts, 1), eq] for ts, eq in list(s.equity_history)[-160:]],
        "episode_history": [list(e) for e in list(s.episode_history)[-24:]],
        "onchain": {
            "identity_tx": s.identity_tx, "commit_tx": s.commit_tx,
            "attest_tx": s.attest_tx, "journal_count": s.journal_count,
        },
        "log": list(s.log_lines)[-80:],
    }


async def apply_action(sup, body: dict) -> None:
    """Dispatch one authenticated control action onto the supervisor. Mirrors the
    TUI's operator actions 1:1 — guardrails (allowlist/breaker/bounds clamps) still
    hard-enforce underneath, exactly as with TUI keys."""
    action = body.get("action")
    if action == "autopilot":
        sup.set_autopilot(bool(body.get("on")))
    elif action == "decide":
        sup.state.add_log("operator: decide now (web)")
        await sup.decide_and_apply()
    elif action == "halt":
        await sup.halt("web operator kill")
    elif action == "strategy":
        key = str(body.get("key", ""))
        if key not in sup.registry:
            raise ValueError(f"unknown strategy {key!r}")
        if key != "flat" and sup.autopilot:
            sup.set_autopilot(False)   # a manual pick implies you're driving (TUI parity)
        try:
            hb = body.get("half_band")
            lv = body.get("levels")
            bi = body.get("bias")
            await sup.force_strategy(
                key,
                half_band=Decimal(str(hb)) if hb is not None else None,
                levels=int(lv) if lv is not None else None,
                bias=max(-1, min(1, int(bi))) if bi is not None else None,
            )
        except (InvalidOperation, TypeError) as e:
            raise ValueError(f"bad tuning value: {e}") from e
    else:
        raise ValueError(f"unknown action {action!r}")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # refs the handler reads (set by ControlServer)
    sup: Any = None
    aio_loop: asyncio.AbstractEventLoop | None = None
    auth: AuthStore | None = None
    limiter: RateLimiter | None = None
    cors_origin: str = "*"


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    # ---- plumbing ----

    def log_message(self, *_: Any) -> None:  # keep the agent's stdout clean
        pass

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def _cors(self) -> None:
        for name, value in cors_headers(self.server.cors_origin):
            self.send_header(name, value)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 65536:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _authed(self) -> bool:
        header = self.headers.get("Authorization") or ""
        token = header.removeprefix("Bearer ").strip()
        return bool(token) and self.server.auth.check_token(token)

    def _rate_ok(self, bucket: str, limit: int) -> bool:
        """One budget check per request; on breach send the 429 and stop the route."""
        if self.server.limiter.allow(self.client_address[0], bucket, limit):
            return True
        self._send(429, {"error": "rate limit exceeded — slow down"})
        return False

    # ---- routes ----

    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        if path == "/api/state":
            snap = state_json(self.server.sup.state, bool(self.server.auth.owners))
            self._send(200, snap)
        elif path == "/api/autopilot":
            self._send(200, autopilot_json())
        elif path == "/api/trades":
            try:
                limit = int(parse_qs(query).get("limit", [TRADES_LIMIT_DEFAULT])[0])
            except ValueError:
                limit = TRADES_LIMIT_DEFAULT
            self._send(200, {"trades": trades_tail(limit=limit)})
        elif path == "/api/auth/nonce":
            if not self._rate_ok("auth", RATE_AUTH_PER_MIN):
                return
            if not self.server.auth.owners:
                self._send(503, {"error": "GRIDORA_OWNER allowlist not set on the agent — controls locked"})
                return
            nonce, message = self.server.auth.issue_nonce()
            self._send(200, {"nonce": nonce, "message": message})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/api/auth/verify":
            if not self._rate_ok("auth", RATE_AUTH_PER_MIN):
                return
            body = self._body()
            try:
                token = self.server.auth.verify(
                    str(body.get("address", "")), str(body.get("signature", "")),
                    str(body.get("nonce", "")))
            except PermissionError as e:
                self._send(401, {"error": str(e)})
                return
            except Exception:  # noqa: BLE001 — malformed sig bytes etc.
                self._send(401, {"error": "could not verify signature"})
                return
            addr = str(body.get("address", ""))
            self.server.sup.state.add_log(
                f"web console: operator {addr[:6]}…{addr[-4:]} authenticated")
            self._send(200, {"token": token})
        elif self.path == "/api/control":
            if not self._rate_ok("control", RATE_CONTROL_PER_MIN):
                return
            if not self._authed():
                self._send(401, {"error": "connect your wallet and sign in first"})
                return
            body = self._body()
            fut = asyncio.run_coroutine_threadsafe(
                apply_action(self.server.sup, body), self.server.aio_loop)
            try:
                fut.result(timeout=ACTION_TIMEOUT_S)
            except ValueError as e:
                self._send(400, {"error": str(e)})
                return
            except TimeoutError:
                fut.cancel()
                self._send(504, {"error": "action timed out — check the agent log"})
                return
            except Exception as e:  # noqa: BLE001 — surface, never crash the server
                self._send(500, {"error": f"{type(e).__name__}: {e}"})
                return
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})


class ControlServer:
    """Owns the HTTP thread. Construct with the live supervisor + its asyncio loop."""

    def __init__(self, sup, aio_loop: asyncio.AbstractEventLoop, *,
                 host: str = "127.0.0.1", port: int = 8317,
                 owners: str | Iterable[str] = "", cors_origin: str = "*") -> None:
        self.host, self.port = host, port
        self._httpd = _Server((host, port), _Handler)
        self._httpd.sup = sup
        self._httpd.aio_loop = aio_loop
        self._httpd.auth = AuthStore(owners)
        self._httpd.limiter = RateLimiter()
        self._httpd.cors_origin = cors_origin or "*"

    def start(self) -> None:
        threading.Thread(target=self._httpd.serve_forever,
                         name="gridora-control-api", daemon=True).start()

    def stop(self) -> None:
        self._httpd.shutdown()
