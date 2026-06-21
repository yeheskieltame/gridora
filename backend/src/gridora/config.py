"""Runtime config from env. Testnet by default; refuse env<->URL mismatch.

PORTED FROM perps-agent/backend/src/perpsagent/config.py (kept dependency-light:
plain os.getenv + dataclass, no pydantic-settings). Never hardcode keys.

A tiny built-in `.env` loader runs at import so a pasted `backend/.env` "just works":
GRIDORA_* settings AND the TWAK creds (TWAK_ACCESS_ID / TWAK_HMAC_SECRET /
TWAK_WALLET_PASSWORD) land in os.environ, so the `twak` CLI subprocess spawned by
TwakClient inherits them.
Real env vars always win (setdefault), so CI / shell exports override the file.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from decimal import Decimal


def _load_dotenv() -> None:
    """Merge KEY=VALUE lines from backend/.env (and ./.env) into os.environ. No deps,
    no override of already-set env vars. Must run BEFORE Settings field defaults read."""
    here = pathlib.Path(__file__).resolve()
    candidates = [pathlib.Path.cwd() / ".env", here.parents[2] / ".env"]  # parents[2] == backend/
    seen: set[pathlib.Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.split(" #", 1)[0].strip().strip('"').strip("'")  # drop inline comment + quotes
            if key:
                os.environ.setdefault(key, val)


_load_dotenv()


@dataclass
class Settings:
    bsc_rpc_url: str = os.getenv("GRIDORA_BSC_RPC_URL", "https://bsc-testnet.publicnode.com")
    chain_id: int = int(os.getenv("GRIDORA_CHAIN_ID", "97"))  # 56 mainnet / 97 testnet
    twak_base_url: str = os.getenv("GRIDORA_TWAK_URL", "http://127.0.0.1:8787")
    cmc_base_url: str = os.getenv("GRIDORA_CMC_URL", "https://pro-api.coinmarketcap.com")
    cmc_api_key: str = os.getenv("GRIDORA_CMC_API_KEY", "")
    competition_contract: str = os.getenv(
        "GRIDORA_COMPETITION", "0x212c61b9b72c95d95bf29cf032f5e5635629aed5"
    )
    testnet: bool = os.getenv("GRIDORA_TESTNET", "true").lower() == "true"
    agent_address: str = os.getenv("GRIDORA_AGENT_ADDRESS", "")  # TWAK-controlled wallet
    agent_uri: str = os.getenv("GRIDORA_AGENT_URI", "ipfs://gridora-agent.json")  # ERC-8004 registration file

    # TWAK auth — set TWAK_ACCESS_ID / TWAK_HMAC_SECRET in .env; `twak` reads them after `twak init`.
    twak_access_id: str = os.getenv("TWAK_ACCESS_ID", "")
    twak_hmac_secret_set: bool = bool(os.getenv("TWAK_HMAC_SECRET"))  # presence only; never read the value

    # TWAK execution / x402
    swap_fallback: bool = os.getenv("GRIDORA_SWAP_FALLBACK", "false").lower() == "true"  # taker swaps vs maker limit orders
    slippage_pct: float = float(os.getenv("GRIDORA_SLIPPAGE_PCT", "0.5"))
    x402_max_payment: int = int(os.getenv("GRIDORA_X402_MAX_PAYMENT", "100000"))  # atomic units cap per data call

    # on-chain verifier addresses (optional read-only mirror; primary proof = TWAK ERC-8004)
    identity_addr: str = os.getenv("GRIDORA_IDENTITY_ADDR", "")
    journal_addr: str = os.getenv("GRIDORA_JOURNAL_ADDR", "")
    ledger_addr: str = os.getenv("GRIDORA_LEDGER_ADDR", "")

    # trading defaults / risk caps (quote/stablecoin units; 0 = auto from preset)
    preset: str = os.getenv("GRIDORA_PRESET", "BALANCED")
    quote_margin: str = os.getenv("GRIDORA_QUOTE_MARGIN", "100")
    max_drawdown: str = os.getenv("GRIDORA_MAX_DRAWDOWN", "0")
    max_inventory: str = os.getenv("GRIDORA_MAX_INVENTORY", "0")
    account_drawdown: str = os.getenv("GRIDORA_ACCOUNT_DRAWDOWN", "0")
    recenter_interval_s: float = float(os.getenv("GRIDORA_RECENTER_INTERVAL_S", "0"))

    def guard(self) -> None:
        """Refuse an obvious env<->chain mismatch before any money path runs."""
        if self.testnet and self.chain_id == 56:
            raise SystemExit("env↔chain mismatch: testnet=true but chainId=56 (mainnet)")
        if not self.testnet and self.chain_id == 97:
            raise SystemExit("env↔chain mismatch: testnet=false but chainId=97 (testnet)")
        looks_testnet = any(t in self.bsc_rpc_url.lower() for t in ("testnet", "chapel", "97"))
        if self.testnet and self.bsc_rpc_url and not looks_testnet:
            raise SystemExit(f"env↔URL mismatch: testnet=true but RPC '{self.bsc_rpc_url}' is not a testnet")

    def assert_twak_creds(self) -> None:
        """Fail fast (for live mode) if the TWAK API creds aren't present in the env."""
        missing = [k for k in ("TWAK_ACCESS_ID", "TWAK_HMAC_SECRET") if not os.getenv(k)]
        if missing:
            raise SystemExit(f"missing TWAK creds in env/.env: {', '.join(missing)} — paste them then `twak init`")

    @property
    def chain_key(self) -> str:
        """TWAK chain key for this network ('bsc' / 'bsctestnet')."""
        return "bsctestnet" if self.testnet else "bsc"

    def addresses(self) -> dict[str, str]:
        return {"identity": self.identity_addr, "journal": self.journal_addr,
                "ledger": self.ledger_addr, "competition": self.competition_contract}

    def dec(self, name: str) -> Decimal:
        return Decimal(getattr(self, name))


settings = Settings()
