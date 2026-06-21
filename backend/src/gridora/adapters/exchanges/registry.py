"""Venue registry. Add a venue = one import + one entry. Engine never changes.

⭐ PORT FROM: perps-agent/.../adapters/exchanges/registry.py
"""
from __future__ import annotations

from .bsc_twak.adapter import BscTwakExchange
from .fake import FakeExchange

REGISTRY = {
    "BSC_TWAK": BscTwakExchange,
    "FAKE": FakeExchange,
}
