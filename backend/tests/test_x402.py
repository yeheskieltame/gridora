"""x402 client tests — TWAK-native flow (`twak x402 request`), offline via a FakeTwak."""
from gridora.adapters.payments.x402 import X402Payments


class FakeTwak:
    def __init__(self):
        self.calls = 0
        self.urls: list[str] = []
        self.max_payments: list[int] = []

    async def x402_request(self, url, max_payment, prefer_network=None):
        self.calls += 1
        self.urls.append(url)
        self.max_payments.append(max_payment)
        return {"data": {"value": 55}}


async def test_pay_and_fetch_calls_twak_x402_and_returns_resource():
    twak = FakeTwak()
    pay = X402Payments(twak, max_payment=42_000)
    data = await pay.pay_and_fetch("https://cmc.test/v3/fear-and-greed/latest", {})
    assert data["data"]["value"] == 55
    assert twak.calls == 1
    assert twak.max_payments == [42_000]


async def test_params_are_appended_to_url():
    twak = FakeTwak()
    pay = X402Payments(twak)
    await pay.pay_and_fetch("https://cmc.test/quotes", {"symbol": "CAKE"})
    assert twak.urls[0] == "https://cmc.test/quotes?symbol=CAKE"


async def test_in_cycle_cache_avoids_double_payment():
    twak = FakeTwak()
    pay = X402Payments(twak, cache_ttl_s=999)
    await pay.pay_and_fetch("https://cmc.test/x", {"symbol": "CAKE"})
    await pay.pay_and_fetch("https://cmc.test/x", {"symbol": "CAKE"})  # served from cache
    assert twak.calls == 1
    pay.clear_cycle()
    await pay.pay_and_fetch("https://cmc.test/x", {"symbol": "CAKE"})  # cache cleared -> pays again
    assert twak.calls == 2
