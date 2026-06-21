"""The eligible BEP-20 tokens (BNB Hack — a fixed list of 149 BEP-20 tokens listed on
CoinMarketCap). Trades outside this set don't count toward scoring, so this is the
guard input for app/safety.py:Allowlist — checked BEFORE every on-chain commit.

IMPORTANT — sourcing: the EXACT 149-symbol list lives in the hackathon brief
(dorahacks.io/hackathon/bnbhack-twt-cmc — a JS-gated page that can't be auto-fetched).
The set below is a CURATED HIGH-CONFIDENCE subset (top liquid BSC/CMC names + the
eligible stables). A too-PERMISSIVE list is HARMFUL (the agent could trade a pair that
scores 0%), so we intentionally err small rather than guess the tail of the 149.

➜ Before live, drop the official list in via `GRIDORA_ALLOWLIST_FILE` (a JSON array of
symbols); `load_allowlist()` reads it and OVERRIDES this curated default. That is the
authoritative path to the exact 149 — keep this in-code set conservative.
"""
from __future__ import annotations

import json
import os
import pathlib

# The authoritative official list, bundled at backend/allowlist.149.json (verified exact
# match to the 149-token competition list). Default source unless overridden.
_BUNDLED = pathlib.Path(__file__).resolve().parents[4] / "allowlist.149.json"

# Curated high-confidence subset. NOT the full 149 — load that from the brief (above).
_CURATED: frozenset[str] = frozenset({
    # stablecoins eligible as the quote leg
    "USDT", "USDC", "USD1", "FDUSD", "DAI", "TUSD",
    # majors / L1s with Binance-Peg BEP-20 listings
    "BNB", "WBNB", "BTCB", "ETH", "XRP", "TRX", "DOGE", "ADA", "LINK", "TON",
    "LTC", "AVAX", "SHIB", "DOT", "UNI", "ATOM", "FIL", "INJ", "FET",
    "SOL", "NEAR", "APT", "SUI", "POL", "AAVE", "MKR", "RUNE", "EGLD", "ALGO",
    "VET", "ICP", "GRT", "CRV", "SNX", "GALA", "CHZ", "ENJ", "STX",
    # BSC ecosystem / DeFi / memes commonly eligible
    "CAKE", "BTT", "FLOKI", "LDO", "PENDLE", "AXS", "TWT", "COMP", "APE", "SFP",
    "1INCH", "SUSHI", "KAVA", "ZIL", "BONK", "PENGU", "PEPE", "WIF", "ENA", "ONDO",
})

ELIGIBLE_TOKENS: frozenset[str] = _CURATED


def load_allowlist(path: str | None = None) -> frozenset[str]:
    """Return the eligible-token set. Resolution: explicit `path` → $GRIDORA_ALLOWLIST_FILE
    → the bundled official `allowlist.149.json` → the curated in-code fallback (only if the
    file is missing). Defaults to the OFFICIAL list so the agent never trades the wrong
    universe just because an env var wasn't set."""
    candidate = path or os.getenv("GRIDORA_ALLOWLIST_FILE", "") or str(_BUNDLED)
    if candidate and os.path.exists(candidate):
        with open(candidate) as f:
            symbols = json.load(f)
        return frozenset(str(s).upper() for s in symbols)
    return ELIGIBLE_TOKENS
