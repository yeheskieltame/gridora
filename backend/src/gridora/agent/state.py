"""GridoraState — one central state object the brain reads and the TUI renders.

Modeled on BridgeAgent's AgentState. Single-threaded (asyncio), so no lock needed.
The supervisor keeps it in sync with the live GridEngine + the brain's decisions; the
TUI is a pure reader of this object.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Deque, Optional, Tuple

from ..domain.models import RegimeFingerprint
from .brain import AgentDecision


@dataclass
class GridoraState:
    market: str = ""
    mode: str = "dry"                 # dry | live
    venue: str = "FAKE"
    chain_id: int = 97
    agent_address: str = ""

    # regime (CoinMarketCap)
    regime: Optional[RegimeFingerprint] = None

    # strategy routing
    active_strategy: str = ""
    strategies: dict = field(default_factory=dict)   # key -> enabled (for the TUI menu)
    last_decision: Optional[AgentDecision] = None
    decisions: Deque[AgentDecision] = field(default_factory=lambda: deque(maxlen=50))

    # live grid
    band: Tuple[Decimal, Decimal] = (Decimal(0), Decimal(0))
    levels: int = 0
    bias: int = 0
    net_base: Decimal = Decimal(0)
    avg_entry: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    pnl_bps: int = 0
    max_dd_bps: int = 0
    fills: int = 0
    resting_orders: int = 0
    closes: int = 0
    wins: int = 0
    consecutive_losses: int = 0

    # equity curve: (ts, equity_quote) samples for the TUI sparkline
    equity_history: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=512))

    # account-level, cumulative across ALL settled episodes (the "modal → sekarang" view;
    # the per-grid fields above only describe the CURRENTLY running episode)
    start_equity: Decimal = Decimal(0)               # starting capital (margin)
    cum_realized: Decimal = Decimal(0)               # booked PnL of all closed episodes
    episodes: int = 0                                # settled episodes (= journal_count)
    episode_wins: int = 0
    # (idx, strategy, pnl_bps, realized_quote) of recent settled episodes, oldest→newest
    episode_history: Deque[Tuple[int, str, int, float]] = field(default_factory=lambda: deque(maxlen=40))

    # on-chain proofs (the verifier surface)
    identity_tx: str = ""
    commit_tx: str = ""
    attest_tx: str = ""
    journal_count: int = 0

    # status + log
    autopilot: bool = True            # False = operator drives (Claude won't auto-switch)
    is_running: bool = False
    status: str = "idle"
    log_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=200))

    def add_log(self, message: str) -> None:
        self.log_lines.append(f"[{time.strftime('%H:%M:%S')}] {message}")

    def record_decision(self, d: AgentDecision) -> None:
        self.last_decision = d
        self.decisions.append(d)
        self.active_strategy = d.strategy_key
        conf = f" ({d.confidence})" if d.confidence else ""
        tag = "🤖" if d.source.startswith("claude") else "⚙"
        self.add_log(f"{tag} {d.source} → {d.strategy_key}{conf}: {d.reasoning[:90]}")

    def note_close(self, realized_delta: Decimal) -> None:
        """Track win/loss streaks so the brain can see when a mode is bleeding."""
        self.closes += 1
        if realized_delta > 0:
            self.wins += 1
            self.consecutive_losses = 0
        elif realized_delta < 0:
            self.consecutive_losses += 1

    def record_episode(self, strategy: str, pnl_bps: int, realized: Decimal) -> None:
        """Roll a settled episode into the cumulative account view + history list, then
        reset the current-episode PnL fields (the engine that produced it is gone)."""
        self.cum_realized += realized
        self.episodes += 1
        if realized > 0:
            self.episode_wins += 1
        self.episode_history.append((self.episodes, strategy, int(pnl_bps), float(realized)))
        self.realized_pnl = Decimal(0)
        self.unrealized_pnl = Decimal(0)

    @property
    def account_equity(self) -> Decimal:
        """Current account value: starting capital + all booked PnL + the open episode's
        running PnL (realized-so-far + unrealized)."""
        return self.start_equity + self.cum_realized + self.realized_pnl + self.unrealized_pnl

    @property
    def account_pnl_pct(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return float((self.account_equity - self.start_equity) / self.start_equity * 100)

    @property
    def account_win_rate(self) -> float:
        return (self.episode_wins / self.episodes * 100) if self.episodes else 0.0

    def snapshot_engine(self, engine, mark: Optional[Decimal] = None) -> None:
        """Pull a consistent read of the live engine into state (for the brain + TUI)."""
        self.band = (engine.lower, engine.upper)
        self.levels = engine.cfg.levels
        self.bias = engine.bias
        self.net_base = engine.net_inventory()
        self.avg_entry = engine.pos_avg
        self.realized_pnl = engine.realized
        self.fills = engine.fill_count
        self.resting_orders = len(engine._resting)
        self.closes = engine.closes
        self.wins = engine.wins
        if mark is not None:
            self.unrealized_pnl = engine.unrealized(mark)
        out = engine.outcome()
        self.pnl_bps = out.pnl_bps
        self.max_dd_bps = out.max_drawdown_bps
        self.equity_history.append((time.time(), float(self.account_equity)))

    @property
    def win_rate(self) -> float:
        return (self.wins / self.closes * 100) if self.closes else 0.0
