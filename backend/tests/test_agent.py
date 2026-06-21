"""Agentic-layer tests — strategies, the Claude brain (via FakeLLM + fallback), and
the supervisor's launch/switch/halt control loop. Offline (no real LLM, no net)."""
from decimal import Decimal

import pytest

from gridora.adapters.chain.memory_chain import MemoryChain
from gridora.adapters.exchanges.fake import FakeExchange
from gridora.adapters.signals.fake import FakeSignals
from gridora.agent.brain import ClaudeBrain, FakeLLM, _extract_json, _key_from_regime
from gridora.agent.state import GridoraState
from gridora.agent.strategies import STRATEGIES, build_config, with_overrides
from gridora.agent.supervisor import AgentSupervisor
from gridora.app.safety import Allowlist, CircuitBreaker, ProfitGuard
from gridora.domain.models import RegimeFingerprint, Venue

ALLOW = Allowlist(symbols=frozenset({"CAKE", "USDT"}))


# ---- strategies ----

def test_build_config_from_strategy():
    s = STRATEGIES["range"]
    cfg = build_config(s, "CAKE/USDT-1", "CAKE/USDT", Decimal("2.5"), Decimal("100"))
    assert cfg.levels == s.levels
    assert cfg.lower < Decimal("2.5") < cfg.upper
    assert cfg.order_size > 0
    assert cfg.policy_version == "v0:range"


def test_with_overrides_clamps_to_bounds():
    s = STRATEGIES["range"]
    o = with_overrides(s, half_band=Decimal("999"), levels=1, deploy_frac=Decimal("5"))
    assert o.half_band == Decimal("0.25")   # clamped to HALF_BAND_MAX
    assert o.levels == 4                      # clamped to LEVELS_MIN
    assert o.deploy_frac == Decimal("1.0")    # clamped to DEPLOY_MAX


def test_flat_strategy_has_no_config():
    with pytest.raises(ValueError):
        build_config(STRATEGIES["flat"], "x", "CAKE/USDT", Decimal("2.5"), Decimal("100"))


# ---- brain ----

async def test_supervisor_auto_selects_focus_token():
    # With auto_select, boot() picks the focus token from the allowlist (universe selector)
    # instead of trading whatever was hardcoded — and launches a grid on it.
    allow = Allowlist(symbols=frozenset({"USDT", "ETH", "CAKE", "LINK", "UNI", "AAVE", "ZEC", "LTC"}))
    ex = FakeExchange({"mid": "100", "tick": "0.01", "step": "0.0001", "min_order_size": "0.001", "equity": "1000"})
    state = GridoraState(market="CAKE/USDT", mode="dry", chain_id=97, agent_address="0xt")
    sup = AgentSupervisor(ex, FakeSignals(), MemoryChain(), ClaudeBrain(FakeLLM()), state,
                          CircuitBreaker(), ProfitGuard(), allow, Decimal(1000),
                          venue=Venue.FAKE, auto_select=True)
    await sup.boot()
    base, _, quote = state.market.partition("/")
    assert quote == "USDT" and base in allow.symbols and base != "USDT"  # a valid eligible focus pair
    assert sup.engine is not None  # booted a grid on the focus token


async def test_supervisor_auto_rotates_via_exchange_factory():
    # auto_select + an exchange factory → boot picks the focus token AND builds its exchange
    # via the factory (the wiring that lets the agent rotate across the whole allowlist).
    allow = Allowlist(symbols=frozenset({"USDT", "ETH", "CAKE", "LINK", "UNI", "AAVE", "ZEC", "LTC"}))
    made: list[str] = []

    def exfac(mkt):
        made.append(mkt)
        return FakeExchange({"mid": "100", "tick": "0.01", "step": "0.0001", "min_order_size": "0.001", "equity": "1000"})

    state = GridoraState(market="CAKE/USDT", mode="paper", chain_id=97, agent_address="0xt")
    sup = AgentSupervisor(exfac("CAKE/USDT"), FakeSignals(), MemoryChain(), ClaudeBrain(FakeLLM()),
                          state, CircuitBreaker(), ProfitGuard(), allow, Decimal(1000),
                          venue=Venue.FAKE, auto_select=True, exchange_for=exfac)
    await sup.boot()
    base, _, quote = state.market.partition("/")
    assert quote == "USDT" and base in allow.symbols and base != "USDT"
    assert state.market in made          # the focus market's exchange was built by the factory
    assert sup.engine is not None


def test_extract_json_handles_fences_and_prose():
    assert _extract_json('```json\n{"strategy":"range"}\n```')["strategy"] == "range"
    assert _extract_json('{"strategy":"range"}')["strategy"] == "range"
    assert _extract_json('Here is my call:\n{"strategy":"trend_long"} done')["strategy"] == "trend_long"
    with pytest.raises(ValueError):
        _extract_json("no json here")


def test_key_from_regime_mapping():
    assert _key_from_regime(RegimeFingerprint("X", label="TRENDING_UP")) == "trend_long"
    assert _key_from_regime(RegimeFingerprint("X", label="TRENDING_DOWN")) == "trend_short"
    assert _key_from_regime(RegimeFingerprint("X", label="SQUEEZE")) == "wide_defensive"
    assert _key_from_regime(RegimeFingerprint("X", label="RANGING")) == "range"


async def test_brain_decides_via_llm():
    state = GridoraState(market="CAKE/USDT")
    state.regime = RegimeFingerprint("CAKE/USDT", momentum=Decimal("0.5"), label="TRENDING_UP", bias=1)
    brain = ClaudeBrain(FakeLLM())  # FakeLLM reads label=TRENDING_UP from the prompt
    d = await brain.decide(state)
    assert d.strategy_key == "trend_long"
    assert d.source == "fake"


async def test_brain_falls_back_when_llm_fails():
    class BadLLM:
        name = "bad"
        async def complete(self, system, prompt):
            raise RuntimeError("claude unavailable")

    state = GridoraState(market="CAKE/USDT")
    state.regime = RegimeFingerprint("CAKE/USDT", label="RANGING")
    d = await ClaudeBrain(BadLLM()).decide(state)
    assert d.source == "fallback"
    assert d.strategy_key == "range"  # deterministic mapping still routes


async def test_brain_rejects_offmenu_strategy_then_falls_back():
    state = GridoraState(market="CAKE/USDT")
    state.regime = RegimeFingerprint("CAKE/USDT", label="RANGING")
    d = await ClaudeBrain(FakeLLM(force_key="not_a_strategy")).decide(state)
    assert d.source == "fallback"  # off-menu key rejected by _parse -> fallback


# ---- supervisor ----

def _supervisor(llm, market="CAKE/USDT", mid="2.5"):
    ex = FakeExchange({"mid": mid, "tick": "0.0001", "step": "0.0001", "min_order_size": "0.001"})
    chain = MemoryChain()
    state = GridoraState(market=market, mode="dry", venue="FAKE")
    sup = AgentSupervisor(
        ex, FakeSignals(fear_greed=52, momentum="0.0"), chain, ClaudeBrain(llm), state,
        CircuitBreaker(), ProfitGuard(), ALLOW, Decimal("1000"), venue=Venue.FAKE)
    return sup, ex, chain, state


async def test_supervisor_boot_launches_strategy_and_commits():
    sup, ex, chain, state = _supervisor(FakeLLM(force_key="range"))
    await sup.boot()
    assert state.active_strategy == "range"
    assert state.is_running
    assert state.commit_tx.startswith("0x")
    assert sup.engine is not None


async def test_supervisor_switches_strategy_and_attests_old():
    llm = FakeLLM(force_key="range")
    sup, ex, chain, state = _supervisor(llm)
    await sup.boot()
    first_iid = sup.cfg.instance_id
    llm.force_key = "trend_long"           # market "changed" — brain switches
    await sup.decide_and_apply()
    assert state.active_strategy == "trend_long"
    assert first_iid in chain._attestations  # old episode booked + attested on switch
    assert state.attest_tx.startswith("0x")
    assert sup.cfg.policy_version == "v0:trend_long"


async def test_supervisor_halts_to_flat():
    llm = FakeLLM(force_key="range")
    sup, ex, chain, state = _supervisor(llm)
    await sup.boot()
    # Fill some buys so there's an actual position to flatten on halt.
    for f in await ex.move_price("CAKE/USDT", Decimal("2.40")):
        await sup.engine.handle_fill(f)
    assert sup.engine.net_inventory() > 0
    llm.force_key = "flat"                  # brain decides it's too dangerous
    await sup.decide_and_apply()
    assert not state.is_running
    assert state.active_strategy == "flat"
    assert sup.engine is None
    assert "CAKE/USDT" in ex.flatten_calls   # flattened to stablecoin
