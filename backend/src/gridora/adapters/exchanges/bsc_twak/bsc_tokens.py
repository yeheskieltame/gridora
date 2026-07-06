"""BNB Chain address book + helpers.

CRITICAL (verified live 2026-06-22): TWAK's SYMBOL resolver is UNSAFE on BSC. Most
allowlist symbols return TOKEN_NOT_FOUND, and several (DOGE/LTC/AVAX/SOL/TRX) silently
resolve to BNB — i.e. "buy DOGE" by symbol would actually buy BNB. So the execution
adapter passes the verified CONTRACT ADDRESS from this map (a raw address makes TWAK
trade EXACTLY that token), and `resolve_token` REFUSES anything not listed — we never
risk a wrong-token swap. This map is therefore the source of truth for what is tradeable,
not a side-table. Every address below is confirmed via a TWAK quote-only by-address that
returned the correct token symbol at priceImpact 0. Verify on BscScan before adding more.

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
    # Ranging grid candidates — added 2026-06-22, each TRIPLE-verified: TWAK quote-by-address
    # returns the right symbol at priceImpact 0 to $200, implied price within ~3% of the real
    # CoinGecko price (not a thin/bridged depeg), and the contract's own symbol()/decimals().
    # PENGU + FIL are the backtest grid winners; the rest are verified-tradeable alternates.
    "PENGU": ("0x6418c0dd099a9fda397c766304cdd918233e8847", 18),
    "FIL":   ("0x0d8ce2a99bb6e3b7db580ed848240e4a0f9ae153", 18),  # Binance-Peg Filecoin
    "ASTER": ("0x000ae314e2a2172a039b26378814c252734f556a", 18),
    "AXS":   ("0x715d400f88c167884bbcc41c5fea407ed4d2f8a0", 18),
    "WLFI":  ("0x47474747477b199288bf72a1d702f7fe0fb1deea", 18),
    "BONK":  ("0xa697e272a73744b343528c3bc4702f2565b2f422", 5),   # BSC BONK is 5-decimal
    "SLX":   ("0x02bcc4c181b83a8c0a342bc003389cbecb4bc54d", 6),   # Solstice, 6-decimal
    "RAVE":  ("0x97693439ea2f0ecdeb9135881e49f354656a911c", 18),  # RaveDAO — clean range; verified 2026-06-23
    "ZRO":   ("0x6985884c4392d348587b19cb9eaaf157f13271cd", 18),  # LayerZero — oversold-bounce; verified 2026-06-24 (TWAK quote symbol ZRO, +0.7% vs CMC)
    "AVAX":  ("0x1ce0c2827e2ef14d5c4f29a091d735a204794041", 18),  # Binance-Peg AVAX — oversold-bounce; verified 2026-06-24 (TWAK quote symbol AVAX, +1.1% vs CMC, orderbook bid-heavy 2.37x)
    "LAB":   ("0x7ec43Cf65F1663F820427C62A5780b8f2E25593A", 18),  # LAB — crash-resilient momentum; verified 2026-06-25 (TWAK quote symbol LAB, +0.3% vs CMC). WARNING: thin BSC DEX liq ($0.7M); small size only
    "AAVE":  ("0xfb6115445bff7b52feb98650c87f44907e58f802", 18),  # Binance-Peg AAVE — blue-chip momentum; verified 2026-06-26 (TWAK quote symbol AAVE, ~+1% vs CMC, priceImpact 0 same price $14 vs $28 — LiquidMesh aggregates 30 thin BSC pools, fine at small size)
    "TOSHI": ("0x6a2608Dabe09bc1128EEC7275B92DFB939D5Db3f", 18),  # Toshi (BSC meme) — US-session momentum; verified 2026-06-28 (TWAK quote by addr, eff $0.0001274 ≈ Gate +1.1%, pancake liq $407k). Extended meme — small size + tight SL only
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
