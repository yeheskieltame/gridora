"""TUI tests — pure render helpers + a headless Textual mount (no real terminal)."""
from decimal import Decimal

from gridora.adapters.chain.memory_chain import MemoryChain
from gridora.adapters.exchanges.fake import FakeExchange
from gridora.adapters.signals.fake import FakeSignals
from gridora.agent.brain import AgentDecision, ClaudeBrain, FakeLLM
from gridora.agent.state import GridoraState
from gridora.agent.supervisor import AgentSupervisor
from gridora.app.safety import Allowlist, CircuitBreaker, ProfitGuard
from gridora.domain.models import RegimeFingerprint, Venue
from gridora.tui.app import (
    GridoraTUI,
    render_account,
    render_grid,
    render_onchain,
    render_regime,
    render_strategy,
    sparkline,
)


def _state() -> GridoraState:
    s = GridoraState(market="CAKE/USDT", mode="dry", venue="FAKE", chain_id=97)
    s.regime = RegimeFingerprint("CAKE/USDT", fear_greed=72, momentum=Decimal("0.4"),
                                 funding_bps=Decimal("2"), label="TRENDING_UP", bias=1)
    s.record_decision(AgentDecision(strategy_key="trend_long", reasoning="momentum up, not yet greedy",
                                    confidence="HIGH", source="claude-code:haiku-4-5", cost_usd=0.02, latency_ms=900))
    s.band = (Decimal("2.3"), Decimal("2.7"))
    s.levels = 14
    s.bias = 1
    s.net_base = Decimal("5")
    s.realized_pnl = Decimal("1.25")
    s.commit_tx = "0x" + "ab" * 20
    s.journal_count = 2
    return s


def test_render_helpers_contain_key_facts():
    s = _state()
    assert "TRENDING_UP" in render_regime(s)
    assert "72" in render_regime(s)
    assert "trend_long" in render_strategy(s)
    assert "momentum up" in render_strategy(s)
    assert "14" in render_grid(s)
    assert "0xababab" in render_onchain(s)
    assert "2 settled" in render_onchain(s)


def test_mode_badge_and_drawdown_alert():
    s = GridoraState(market="CAKE/USDT", mode="paper")
    s.start_equity = Decimal("1000")
    assert "PAPER" in render_account(s)                      # unmistakable paper badge
    s.cum_realized = Decimal("-150")                          # -15% of margin
    assert "DRAWDOWN" in render_account(s)                    # alert near the DQ line
    assert "REAL MONEY" in render_account(GridoraState(mode="live"))   # live is loud


def test_sparkline():
    assert sparkline([]) == ""
    assert len(sparkline([1, 2, 3, 4, 5])) == 5
    assert sparkline([5, 5, 5]) != ""  # flat series still renders


async def test_tui_mounts_and_renders():
    ex = FakeExchange({"mid": "2.5", "tick": "0.0001", "step": "0.0001", "min_order_size": "0.001"})
    state = GridoraState(market="CAKE/USDT", mode="dry", venue="FAKE")
    sup = AgentSupervisor(
        ex, FakeSignals(fear_greed=52), MemoryChain(), ClaudeBrain(FakeLLM(force_key="range")),
        state, CircuitBreaker(), ProfitGuard(), Allowlist(symbols=frozenset({"CAKE", "USDT"})),
        Decimal("1000"), venue=Venue.FAKE)

    async def noop_sim(_sup):
        return

    app = GridoraTUI(sup, simulate=noop_sim)
    async with app.run_test() as pilot:
        await pilot.pause(0.3)   # let on_mount boot the agent + the refresh timer tick
        from textual.widgets import Static
        app.query_one("#regime", Static)   # panel composed (raises if missing)
        app.query_one("#strategy", Static)
        app.query_one("#log", Static)
        # on_mount's worker booted the agent: brain ran + launched the 'range' grid
        assert state.active_strategy == "range"
        assert state.commit_tx.startswith("0x")
        assert render_regime(state).startswith("[b]Regime")
