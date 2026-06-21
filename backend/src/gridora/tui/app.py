"""Gridora TUI — the operator/verifier cockpit (Textual).

A reader of GridoraState (agent/state.py) plus an operator console: pause the
autopilot and drive the grid by hand. Shows the CoinMarketCap regime, Claude's
latest routing decision + reasoning, the live grid, PnL, and the on-chain proofs.

Keys
  d        force a Claude decision now        a   toggle AUTO / MANUAL (pause Claude)
  1-6      force a strategy directly          k   kill → flat (halt to stablecoin)
  [ / ]    narrow / widen the band            , / .   fewer / more levels
  b        cycle bias (short / sym / long)    q   quit

In MANUAL the autopilot won't switch strategies — you do (1-6, [ ], etc.). Every
manual change still passes the deterministic guardrails (bounds clamp + breaker /
allowlist / inventory) and re-commits its config hash on-chain before trading.

render_* helpers are pure (state -> markup) so they're unit-testable headless.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.widgets import Footer, Header, Static

from ..agent.state import GridoraState
from ..agent.strategies import STRATEGIES

_SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 32) -> str:
    if not values:
        return ""
    vals = values[-width:]
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return _SPARK[3] * len(vals)
    span = hi - lo
    return "".join(_SPARK[min(7, int((v - lo) / span * 7))] for v in vals)


def _sign(v: Decimal | float) -> str:
    return "green" if v > 0 else "red" if v < 0 else "white"


def _half_band_pct(lo: Decimal, hi: Decimal) -> Decimal | None:
    return ((hi - lo) / (hi + lo) * 100) if (hi + lo) > 0 else None


def render_regime(s: GridoraState) -> str:
    r = s.regime
    if r is None:
        return "[b]Regime (CoinMarketCap)[/b]\n  awaiting first read…"
    fg_color = "red" if r.fear_greed <= 25 else "green" if r.fear_greed >= 75 else "yellow"
    return (
        "[b]Regime (CoinMarketCap)[/b]\n"
        f"  label      [b]{r.label}[/b]\n"
        f"  fear&greed [{fg_color}]{r.fear_greed}[/{fg_color}]/100\n"
        f"  momentum   [{_sign(r.momentum)}]{r.momentum:+}[/{_sign(r.momentum)}]\n"
        f"  funding    {r.funding_bps} bps")


def render_strategy(s: GridoraState) -> str:
    d = s.last_decision
    items = s.strategies or {k: v.enabled for k, v in STRATEGIES.items()}
    parts = []
    for i, (k, en) in enumerate(items.items(), 1):
        label = f"[dim]{i}[/dim]·{k}"
        if k == s.active_strategy:
            parts.append(f"[b green]{label}[/b green]")
        elif not en:
            parts.append(f"[dim]{label}[/dim]")
        else:
            parts.append(label)
    mode = "[b green]AUTO[/b green]" if s.autopilot else "[b yellow]MANUAL[/b yellow]"
    out = [
        "[b]Strategy router (Claude)[/b]",
        f"  {mode}   active: [b green]{s.active_strategy or '—'}[/b green]",
        f"  {'  '.join(parts)}",
    ]
    if d is not None:
        brain = "🤖 " + d.source if d.source.startswith("claude") else "⚙ " + d.source
        cost = f"  ${d.cost_usd:.4f} · {d.latency_ms}ms" if d.cost_usd or d.latency_ms else ""
        out.append(f"  {brain} [{_conf_color(d.confidence)}]{d.confidence}[/{_conf_color(d.confidence)}]{cost}")
        out.append(f"  [italic]{d.reasoning[:120]}[/italic]")
    return "\n".join(out)


def _conf_color(c: str) -> str:
    return {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(c, "white")


def render_grid(s: GridoraState) -> str:
    lo, hi = s.band
    bias = {1: "long +1", -1: "short -1", 0: "symmetric 0"}.get(s.bias, str(s.bias))
    pct = _half_band_pct(lo, hi)
    width = f"  (±{pct:.1f}%)" if pct is not None else ""
    return (
        "[b]Live grid[/b]\n"
        f"  band   [{lo} … {hi}]{width}\n"
        f"  levels {s.levels}   bias {bias}\n"
        f"  net    [{_sign(s.net_base)}]{s.net_base:+}[/{_sign(s.net_base)}] base @ {s.avg_entry}\n"
        f"  resting {s.resting_orders}   fills {s.fills}")


def render_mode_badge(mode: str) -> str:
    """Unmistakable status badge — never confuse paper vs real money at a glance."""
    return {"paper": "[b black on yellow] PAPER · no real money [/]",
            "live": "[b white on red] LIVE · REAL MONEY [/]",
            "dry": "[b black on grey50] DRY · offline sim [/]"}.get(mode, f"[b]{mode}[/b]")


def render_account(s: GridoraState) -> str:
    """The headline view: how much you started with vs now, cumulative across episodes."""
    eq, pct = s.account_equity, s.account_pnl_pct
    tone = "green" if pct > 0 else "red" if pct < 0 else "white"
    warn = "  [b red]⚠ DRAWDOWN[/b red]" if pct <= -10 else ""   # alert near the 30% DQ
    open_pl = s.realized_pnl + s.unrealized_pnl
    spark = sparkline([e for _, e in s.equity_history])
    return (
        f"[b]Account[/b]  {render_mode_badge(s.mode)}{warn}\n"
        f"  modal    ${s.start_equity:.2f}\n"
        f"  equity   [{tone}]${eq:.2f}[/{tone}]  ([{tone}]{pct:+.2f}%[/{tone}])\n"
        f"  P/L      closed [{_sign(s.cum_realized)}]{s.cum_realized:+.3f}[/{_sign(s.cum_realized)}]"
        f" · open [{_sign(open_pl)}]{open_pl:+.3f}[/{_sign(open_pl)}]\n"
        f"  trades {s.episodes} · win {s.account_win_rate:.0f}% · maxDD {s.max_dd_bps}bps\n"
        f"  {spark}")


def render_history(s: GridoraState) -> str:
    """Recent settled episodes (newest first) — the trade history."""
    rows = list(s.episode_history)[-7:][::-1]
    if not rows:
        return "[b]Trade history[/b]\n  [dim]no settled trades yet…[/dim]"
    out = [f"[b]Trade history[/b]  [dim]{s.episodes} settled[/dim]"]
    for idx, strat, bps, realized in rows:
        tone = "green" if bps > 0 else "red" if bps < 0 else "white"
        out.append(f"  [dim]#{idx:<3}[/dim] {strat:13} [{tone}]{bps:+5}bps[/{tone}] "
                   f"[{tone}]{realized:+.3f}[/{tone}]")
    return "\n".join(out)


def render_onchain(s: GridoraState) -> str:
    def tx(t: str) -> str:
        return f"[green]{t[:18]}…[/green]" if t else "[dim]—[/dim]"
    return (
        "[b]On-chain proofs (BSC)[/b]\n"
        f"  identity {tx(s.identity_tx)}\n"
        f"  commit   {tx(s.commit_tx)}\n"
        f"  attest   {tx(s.attest_tx)}\n"
        f"  journal  {s.journal_count} settled episode(s)")


def render_log(s: GridoraState) -> str:
    lines = list(s.log_lines)[-12:]
    return "[b]Log[/b]\n" + ("\n".join(lines) if lines else "  …")


# The interactive control bar: navigate chips with ←/→, adjust the focused value
# chip (band/lvl/bias, marked ↕) with ↑/↓, activate with Enter/Space. Each item is
# (action_id, label); value chips render their live value in render_controls().
ADJUSTABLE = {"band", "lvl", "bias"}
CONTROL_ITEMS: list[tuple[str, str]] = [
    ("auto", "Auto"),
    ("range", "range"),
    ("trend_long", "t.long"),
    ("trend_short", "t.short"),
    ("tight_scalp", "scalp"),
    ("wide_defensive", "wide"),
    ("flat", "flat"),
    ("band", "band"),
    ("lvl", "lvl"),
    ("bias", "bias"),
    ("decide", "decide"),
]


def _bias_txt(b: int) -> str:
    return "+1" if b > 0 else "−1" if b < 0 else "0"


def render_controls(s: GridoraState, cursor: int) -> str:
    pct = _half_band_pct(*s.band)
    dyn = {
        "auto": "● AUTO" if s.autopilot else "○ MANUAL",
        "band": (f"band ±{pct:.1f}% ↕" if pct is not None else "band — ↕"),
        "lvl": f"lvl {s.levels} ↕",
        "bias": f"bias {_bias_txt(s.bias)} ↕",
    }
    cells = []
    for i, (aid, label) in enumerate(CONTROL_ITEMS):
        text = dyn.get(aid, label)
        if i == cursor:
            cells.append(f"[reverse b] {text} [/reverse b]")           # focused
        elif aid == s.active_strategy or (aid == "auto" and s.autopilot):
            cells.append(f"[b green]▸{text}[/b green]")                  # active
        else:
            cells.append(f" {text} ")
    hint = "[dim]←/→ pindah · ↑/↓ atur nilai · Enter/Space pakai[/dim]"
    return f"[b]Controls[/b]   {hint}\n " + " ".join(cells)


class GridoraTUI(App):
    CSS = """
    Screen { background: $surface; }
    #panels { grid-size: 3 2; grid-gutter: 1; height: 2fr; padding: 1; }
    #panels > Static { border: round $primary; padding: 0 1; }
    #log { height: 1fr; border: round $accent; padding: 0 1; margin: 0 1 1 1; }
    #controls { height: auto; border: round $secondary; padding: 0 1; margin: 0 1 1 1; }
    """
    BINDINGS = [
        # interactive navigation (primary — for when you don't want to memorize keys)
        Binding("left", "cursor_prev", "◀ Nav", show=True),
        Binding("right", "cursor_next", "Nav ▶", show=True),
        Binding("up", "value_up", "↕ Adjust", show=True),
        Binding("enter", "activate", "Apply", show=True),
        Binding("down", "value_down", "Adjust", show=False),
        Binding("space", "activate", "Apply", show=False),
        # quick toggles kept visible
        Binding("a", "toggle_auto", "Auto"),
        Binding("k", "halt", "Kill→flat"),
        Binding("q", "quit", "Quit"),
        # direct hotkeys for power users — hidden from footer, still active
        Binding("d", "force_decide", "Decide", show=False),
        Binding("b", "cycle_bias", "Bias", show=False),
        Binding("left_square_bracket", "band_narrow", "Narrower", show=False, key_display="["),
        Binding("right_square_bracket", "band_wide", "Wider", show=False, key_display="]"),
        Binding("comma", "levels_down", "−Lvl", show=False, key_display=","),
        Binding("full_stop", "levels_up", "+Lvl", show=False, key_display="."),
        Binding("1", "pick('range')", "range", show=False),
        Binding("2", "pick('trend_long')", "trend_long", show=False),
        Binding("3", "pick('trend_short')", "trend_short", show=False),
        Binding("4", "pick('tight_scalp')", "tight_scalp", show=False),
        Binding("5", "pick('wide_defensive')", "wide_defensive", show=False),
        Binding("6", "pick('flat')", "flat", show=False),
    ]

    def __init__(self, supervisor, simulate=None) -> None:
        super().__init__()
        self.supervisor = supervisor
        self.state: GridoraState = supervisor.state
        self.simulate = simulate
        self._cursor = 0
        self._last_fills = 0      # for the live fills ticker
        self._eng_id = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            with Grid(id="panels"):
                yield Static(id="regime")
                yield Static(id="strategy")
                yield Static(id="grid")
                yield Static(id="account")
                yield Static(id="onchain")
                yield Static(id="history")
            yield Static(id="log")
            yield Static(id="controls")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "Gridora"
        self.set_interval(0.4, self._refresh)
        self.run_worker(self._run_agent(), exclusive=False, name="agent")

    async def _run_agent(self) -> None:
        self.state.add_log("agent booting…")
        try:
            await self.supervisor.boot()
            if self.simulate is not None:
                await self.simulate(self.supervisor)
            else:
                await self.supervisor.run_forever(decide_interval=60.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — surface in the log, never crash the UI
            self.state.add_log(f"agent error: {e}")

    def _tick_fills(self) -> None:
        """Log new fills the moment they happen, so the operator watches trades live."""
        eng = self.supervisor.engine
        if eng is None:
            self._last_fills = 0
            return
        if id(eng) != self._eng_id:                       # new grid → re-baseline, don't log its placement
            self._eng_id, self._last_fills = id(eng), eng.fill_count
            return
        if eng.fill_count > self._last_fills:
            n = eng.fill_count - self._last_fills
            self._last_fills = eng.fill_count
            self.state.add_log(f"✓ {n} fill(s) · net {eng.net_inventory():+} base · "
                               f"realized {eng.realized:+.4f}")

    def _refresh(self) -> None:
        s = self.state
        self._tick_fills()                                # live "filled" feed into the Log panel
        eng = self.supervisor.engine
        if eng is not None:                               # keep ALL panels live, not just on decision ticks
            s.snapshot_engine(eng, getattr(eng.ex, "_mid", None))
        mode = "AUTO" if s.autopilot else "MANUAL"
        # Header doesn't render Rich markup → keep it plain. Equity + MODE up front at a glance.
        self.sub_title = (f"{s.market} · {s.mode.upper()} · ${s.account_equity:.2f} ({s.account_pnl_pct:+.1f}%) · "
                          f"{s.status} · {mode} · chain {s.chain_id} · {s.venue}")
        self.query_one("#regime", Static).update(render_regime(s))
        self.query_one("#strategy", Static).update(render_strategy(s))
        self.query_one("#grid", Static).update(render_grid(s))
        self.query_one("#account", Static).update(render_account(s))
        self.query_one("#onchain", Static).update(render_onchain(s))
        self.query_one("#history", Static).update(render_history(s))
        self.query_one("#log", Static).update(render_log(s))
        self.query_one("#controls", Static).update(render_controls(s, self._cursor))

    # ---- interactive control bar (arrows + Enter/Space) ----

    def action_cursor_next(self) -> None:
        self._cursor = (self._cursor + 1) % len(CONTROL_ITEMS)

    def action_cursor_prev(self) -> None:
        self._cursor = (self._cursor - 1) % len(CONTROL_ITEMS)

    def action_value_up(self) -> None:
        self._adjust(+1)

    def action_value_down(self) -> None:
        self._adjust(-1)

    def _adjust(self, direction: int) -> None:
        """↑/↓ on the focused value chip (band/lvl/bias). No-op on other chips."""
        aid, _ = CONTROL_ITEMS[self._cursor]
        if aid not in ADJUSTABLE:
            return
        hb, lv = self._cur_band_levels()
        if aid == "band":
            self._retune(half_band=hb + Decimal("0.01") * direction, levels=lv)
        elif aid == "lvl":
            self._retune(half_band=hb, levels=lv + 2 * direction)
        elif aid == "bias":
            self._retune(half_band=hb, levels=lv, bias=max(-1, min(1, self.state.bias + direction)))

    def action_activate(self) -> None:
        aid, _ = CONTROL_ITEMS[self._cursor]
        self._dispatch(aid)

    def _dispatch(self, aid: str) -> None:
        if aid in ADJUSTABLE:
            self._adjust(+1)  # Enter on a value chip nudges it up; ↑/↓ is the main path
        elif aid == "auto":
            self.action_toggle_auto()
        elif aid == "decide":
            self.action_force_decide()
        else:
            self.action_pick(aid)  # a strategy key (range / trend_long / … / flat)

    # ---- operator actions ----

    def action_force_decide(self) -> None:
        self.state.add_log("operator: decide now (d)")
        self.run_worker(self.supervisor.decide_and_apply(), exclusive=False)

    def action_toggle_auto(self) -> None:
        self.supervisor.set_autopilot(not self.supervisor.autopilot)

    def action_halt(self) -> None:
        self.run_worker(self.supervisor.halt("manual kill (k)"), exclusive=False)

    async def action_quit(self) -> None:
        """Graceful stop (best practice): flatten the open grid + book the outcome, THEN exit
        — never leave an unmanaged grid running. (Use `k` to kill→flat mid-run without quitting.)"""
        self.state.add_log("stopping — flatten + book, then quit…")
        try:
            await asyncio.wait_for(self.supervisor.halt("operator quit (q)"), timeout=8)
        except Exception:  # noqa: BLE001 — best-effort finalize; quit regardless
            pass
        self.exit()

    def action_pick(self, key: str) -> None:
        if not self.supervisor.autopilot or key == "flat":
            pass
        else:
            self.supervisor.set_autopilot(False)  # a manual pick implies you're driving
        self.run_worker(self.supervisor.force_strategy(key), exclusive=False)

    def action_band_wide(self) -> None:
        hb, lv = self._cur_band_levels()
        self._retune(half_band=hb + Decimal("0.01"), levels=lv)

    def action_band_narrow(self) -> None:
        hb, lv = self._cur_band_levels()
        self._retune(half_band=hb - Decimal("0.01"), levels=lv)

    def action_levels_up(self) -> None:
        hb, lv = self._cur_band_levels()
        self._retune(half_band=hb, levels=lv + 2)

    def action_levels_down(self) -> None:
        hb, lv = self._cur_band_levels()
        self._retune(half_band=hb, levels=lv - 2)

    def action_cycle_bias(self) -> None:
        hb, lv = self._cur_band_levels()
        nxt = {-1: 0, 0: 1, 1: -1}.get(self.state.bias, 0)
        self._retune(half_band=hb, levels=lv, bias=nxt)

    # ---- helpers ----

    def _cur_band_levels(self) -> tuple[Decimal, int]:
        c = self.supervisor.cfg
        if c is not None and (c.upper + c.lower) > 0:
            return (c.upper - c.lower) / (c.upper + c.lower), c.levels
        return Decimal("0.06"), 10

    def _retune(self, *, half_band: Decimal | None = None, levels: int | None = None,
                bias: int | None = None) -> None:
        key = self.state.active_strategy or ""
        if not key or key == "flat" or self.supervisor.cfg is None:
            self.state.add_log("operator: no live grid — pick a mode (1-5) first")
            return
        self.run_worker(
            self.supervisor.force_strategy(key, half_band=half_band, levels=levels, bias=bias),
            exclusive=False)
