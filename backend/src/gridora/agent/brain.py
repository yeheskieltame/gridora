"""The agentic brain — Claude (local Claude Code CLI) as the strategy router.

Each cycle: read the CMC regime + grid state, ask Claude to pick/switch/halt a strategy
+ tune band/levels. Claude routes; deterministic guardrails (bounds clamp + breaker/
allowlist/inventory) enforce. On any LLM failure it falls back to pure `classify()` —
never bricks. LLM swappable: ClaudeCodeCLI (local) | FakeLLM (offline tests/dry).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Protocol

from .playbook import TRADING_SKILL
from .strategies import STRATEGIES, Strategy, enabled_strategies

# The brain's standing knowledge = role + the trading SKILL playbook (agent/playbook.py).
_ROLE = (
    "You are Gridora's trading strategy router on BNB Chain. You do NOT place orders or move "
    "funds — a deterministic engine executes and hard risk limits (circuit breaker, inventory "
    "cap, drawdown, token allowlist) are enforced regardless of what you say. Each cycle, given "
    "the market regime + current grid state, pick the best strategy from the menu (or 'flat') "
    "and optionally tune band/levels/bias. Reply with ONE JSON object and nothing else.\n\n"
)
SYSTEM_PROMPT = _ROLE + TRADING_SKILL


@dataclass
class AgentDecision:
    strategy_key: str                       # an enabled strategy key (incl. "flat")
    half_band: Optional[Decimal] = None     # realtime band override (clamped later)
    levels: Optional[int] = None
    deploy_frac: Optional[Decimal] = None
    bias: Optional[int] = None
    reasoning: str = ""
    confidence: str = "MEDIUM"              # HIGH | MEDIUM | LOW
    source: str = "claude"                 # "claude-code:<model>" | "fallback"
    cost_usd: float = 0.0
    latency_ms: int = 0


# ---- LLM access ----

class LLMClient(Protocol):
    name: str
    async def complete(self, system: str, prompt: str) -> str: ...


class ClaudeCodeCLI:
    """Calls the LOCAL Claude Code CLI headless. No API key — uses the machine's
    Claude Code (subscription). Verified: `claude -p ... --output-format json` returns
    {"result", "is_error", "total_cost_usd", "duration_ms", ...}."""

    def __init__(self, model: str = "claude-sonnet-4-6", timeout: float = 60.0,
                 cwd: str | None = None) -> None:
        self.model = model
        self.timeout = timeout
        self.cwd = cwd or tempfile.gettempdir()  # neutral cwd: don't load the project CLAUDE.md
        self.name = f"claude-code:{model}"
        self.last_cost_usd = 0.0
        self.last_latency_ms = 0

    async def complete(self, system: str, prompt: str) -> str:
        # --strict-mcp-config with no --mcp-config => load NO MCP servers: a pure
        # reasoning call shouldn't pay the latency of the session's tools.
        args = ["claude", "-p", prompt, "--output-format", "json", "--model", self.model,
                "--strict-mcp-config"]
        if system:
            args += ["--system-prompt", system]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=self.cwd)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError as e:
            proc.kill()
            raise RuntimeError(f"claude CLI timed out after {self.timeout}s") from e
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI exited {proc.returncode}: {err.decode()[:200]}")
        raw = out.decode()
        if len(raw) > 200_000:   # bound the parse: a strategy reply is a few KB, not MB
            raise RuntimeError(f"claude CLI output too large ({len(raw)} bytes)")
        data = json.loads(raw)
        if data.get("is_error"):
            raise RuntimeError(f"claude CLI error: {data.get('result', '')[:200]}")
        self.last_cost_usd = float(data.get("total_cost_usd", 0.0) or 0.0)
        self.last_latency_ms = int(data.get("duration_ms", 0) or 0)
        return str(data.get("result", ""))


class FakeLLM:
    """Deterministic stand-in for offline tests / dry-run: maps the regime to a
    strategy with the same logic the fallback uses, but as a 'claude' reply so the
    parse path is exercised."""

    name = "fake"

    def __init__(self, force_key: str | None = None) -> None:
        self.force_key = force_key

    async def complete(self, system: str, prompt: str) -> str:
        key = self.force_key or _key_from_prompt(prompt)
        return json.dumps({
            "strategy": key,
            "reasoning": f"[fake] regime maps to {key}",
            "confidence": "MEDIUM",
        })


# ---- the brain ----

class ClaudeBrain:
    def __init__(self, llm: LLMClient, registry: dict[str, Strategy] | None = None) -> None:
        self.llm = llm
        self.registry = registry or STRATEGIES

    async def decide(self, state) -> AgentDecision:
        """Ask the LLM to route; fall back to the deterministic classifier on any
        failure. `state` is a GridoraState (agent/state.py)."""
        menu = enabled_strategies(self.registry)
        prompt = self._build_prompt(state, menu)
        try:
            raw = await self.llm.complete(SYSTEM_PROMPT, prompt)
            decision = self._parse(raw, menu)
            decision.source = self.llm.name
            decision.cost_usd = getattr(self.llm, "last_cost_usd", 0.0)
            decision.latency_ms = getattr(self.llm, "last_latency_ms", 0)
            return decision
        except Exception as e:  # noqa: BLE001 — never brick the loop on a bad LLM call
            return self._fallback(state, menu, note=str(e))

    def _build_prompt(self, state, menu: list[Strategy]) -> str:
        r = state.regime
        menu_txt = "\n".join(f"  - {s.key}: {s.description}" for s in menu)
        regime_txt = (
            f"market={state.market} F&G={r.fear_greed} momentum={r.momentum} "
            f"funding_bps={r.funding_bps} label={r.label}" if r else f"market={state.market} (no regime yet)")
        return (
            f"REGIME (CoinMarketCap):\n  {regime_txt}\n\n"
            f"CURRENT GRID STATE:\n"
            f"  active_strategy={state.active_strategy or 'none'}\n"
            f"  net_inventory={state.net_base} avg_entry={state.avg_entry}\n"
            f"  realized_pnl={state.realized_pnl} pnl_bps={state.pnl_bps} max_dd_bps={state.max_dd_bps}\n"
            f"  consecutive_losses={state.consecutive_losses}\n\n"
            f"STRATEGY MENU (pick exactly one key):\n{menu_txt}\n\n"
            f"Reply with ONLY this JSON:\n"
            f'{{"strategy": "<key>", "half_band": <0.01-0.25 or null>, '
            f'"levels": <4-50 or null>, "deploy_frac": <0.1-1.0 or null>, '
            f'"bias": <-1|0|1 or null>, '
            f'"reasoning": "<=2 sentences on why this fits the regime + risk", '
            f'"confidence": "HIGH|MEDIUM|LOW"}}'
        )

    def _parse(self, raw: str, menu: list[Strategy]) -> AgentDecision:
        obj = _extract_json(raw)
        keys = {s.key for s in menu}
        key = str(obj.get("strategy", "")).strip()
        if key not in keys:
            raise ValueError(f"strategy {key!r} not in enabled menu {sorted(keys)}")
        return AgentDecision(
            strategy_key=key,
            half_band=_opt_dec(obj.get("half_band")),
            levels=_opt_int(obj.get("levels")),
            deploy_frac=_opt_dec(obj.get("deploy_frac")),
            bias=_opt_int(obj.get("bias")),
            reasoning=str(obj.get("reasoning", "")).strip(),
            confidence=str(obj.get("confidence", "MEDIUM")).upper(),
        )

    def _fallback(self, state, menu: list[Strategy], note: str = "") -> AgentDecision:
        keys = {s.key for s in menu}
        key = _key_from_regime(state.regime) if state.regime else "range"
        if key not in keys:
            key = "range" if "range" in keys else next(iter(keys))
        return AgentDecision(
            strategy_key=key, reasoning=f"deterministic fallback (Claude unavailable: {note[:80]})",
            confidence="LOW", source="fallback")


# ---- pure helpers (testable) ----

def _extract_json(raw: str) -> dict:
    """Pull a JSON object from an LLM reply (possibly ```fenced``` or wrapped in prose).
    Try the whole reply, then each fenced block, then the LAST balanced {...} — robust to
    a stray brace in prose or two candidate blocks, unlike a naive first-{ to last-}."""
    s = raw.strip()
    candidates = [s] + [b for i, b in enumerate(s.split("```")) if i % 2 == 1]
    for b in candidates:
        b = b.strip()
        b = b[4:].strip() if b.lower().startswith("json") else b
        try:
            obj = json.loads(b)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    end = s.rfind("}")                       # fall back to the LAST balanced object
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            depth += 1 if s[i] == "}" else -1 if s[i] == "{" else 0
            if depth == 0 and s[i] == "{":
                try:
                    return json.loads(s[i:end + 1])
                except (json.JSONDecodeError, ValueError):
                    break
        end = s.rfind("}", 0, end)
    raise ValueError(f"no JSON object in LLM reply: {raw[:120]!r}")


def _key_from_regime(regime) -> str:
    """Map a regime label to a strategy key (the deterministic fallback policy)."""
    label = getattr(regime, "label", "RANGING")
    return {
        "TRENDING_UP": "trend_long",
        "TRENDING_DOWN": "trend_short",
        "SQUEEZE": "wide_defensive",
        "RANGING": "range",
    }.get(label, "range")


def _key_from_prompt(prompt: str) -> str:
    """FakeLLM: derive a key from the regime line embedded in the prompt."""
    for label, key in (("TRENDING_UP", "trend_long"), ("TRENDING_DOWN", "trend_short"),
                       ("SQUEEZE", "wide_defensive"), ("RANGING", "range")):
        if f"label={label}" in prompt:
            return key
    return "range"


def _opt_dec(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _opt_int(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
