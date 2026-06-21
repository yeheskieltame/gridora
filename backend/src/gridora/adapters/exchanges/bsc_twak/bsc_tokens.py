"""BNB Chain address book + helpers.

NOTE: The primary execution path does NOT need a hand-maintained token-address map —
TWAK resolves tokens by SYMBOL and routes/signs internally (`twak swap`/`automate`),
so a wrong address here can't misroute funds on the main path. These addresses are
for: the optional swap-fallback, web3 reads if ever enabled, x402 settlement, and the
verifier UI's explorer links. Verify any address on BscScan / PancakeSwap before use.

Official execution venue = PancakeSwap (via TWAK-native swap + limit orders). The
iZiSwap/iZUMi limit-order path is NOT used.
"""
from __future__ import annotations

from decimal import Decimal

# --- PancakeSwap (for the optional fallback + explorer links only) ---
PANCAKE_V2_ROUTER = {56: "0x10ED43C718714eb63d5aA57B78B54704E256024E",
                     97: "0xD99D1c33F9fC3444f8101754aBC46c52416550D1"}
PANCAKE_V2_FACTORY = {56: "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
                      97: "0x6725F303b657a9451d8BA641348b6761A6CC7a17"}

# --- x402 settlement asset: "United Stables" (U-token), EIP-3009 (from bnbagent-sdk) ---
X402_ASSET = {56: "0xcE24439F2D9C6a2289F741120FE202248B666666",
              97: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"}

# Symbol -> (address, decimals). MAINNET (56). Verify on BscScan before live.
BSC_TOKENS: dict[str, tuple[str, int]] = {
    "WBNB":  ("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 18),
    "BNB":   ("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 18),
    "USDT":  ("0x55d398326f99059fF775485246999027B3197955", 18),
    "USDC":  ("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18),
    "FDUSD": ("0xc5f0f7b66764F6ec8C8Dff7BA683102295E16409", 18),
    "DAI":   ("0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3", 18),
    "CAKE":  ("0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", 18),
    "ETH":   ("0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 18),
    "BTCB":  ("0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c", 18),
    "XRP":   ("0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE", 18),
    "ADA":   ("0x3EE2200Efb3400fAbB9AacF31297cBdD1d435D47", 18),
    "DOGE":  ("0xbA2aE424d960c26247Dd6c32edC70B295c744C43", 8),
    "LINK":  ("0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD", 18),
    "DOT":   ("0x7083609fCE4d1d8Dc0C979AAb8c869Ea2C873402", 18),
    "LTC":   ("0x4338665CBB7B2485A8855A139b75D5e34AB0DB94", 18),
    "TWT":   ("0x4B0F1812e5Df2A09796481Ff14017e6005508003", 18),
    "UNI":   ("0xBf5140A22578168FD562DCcF235E5D43A02ce9B1", 18),
}

# TESTNET (97). Sparse on purpose — testnet liquidity is thin; TWAK resolves symbols.
BSC_TESTNET_TOKENS: dict[str, tuple[str, int]] = {
    "WBNB": ("0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd", 18),
    "BNB":  ("0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd", 18),
    "BUSD": ("0xeD24FC36d5Ee211Ea25A80239Fb8C4Cfd80f12Ee", 18),
    "USDT": ("0x337610d27c682E347C9cD60BD4b3b107C9d34dDd", 18),
}


class UnknownToken(KeyError):
    pass


def tokens_for(chain_id: int) -> dict[str, tuple[str, int]]:
    return BSC_TESTNET_TOKENS if chain_id == 97 else BSC_TOKENS


def resolve_token(symbol: str, tokens: dict[str, tuple[str, int]] | None = None) -> tuple[str, int]:
    table = tokens or BSC_TOKENS
    if symbol not in table:
        raise UnknownToken(f"no BSC address for {symbol} — TWAK resolves it by symbol; "
                           f"add it to BSC_TOKENS only if you need a raw address")
    return table[symbol]


def split_market(market: str) -> tuple[str, str]:
    base, sep, quote = market.partition("/")
    if not sep:
        raise ValueError(f"market must be BASE/QUOTE, got {market!r}")
    return base, quote


def to_units(amount: Decimal, decimals: int) -> int:
    return int(amount * (Decimal(10) ** decimals))


def from_units(units: int, decimals: int) -> Decimal:
    return Decimal(units) / (Decimal(10) ** decimals)
