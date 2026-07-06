"""Shared test guards. Tests must never touch the network: strip TWAK_REST_URL so a
developer's .env (auto-loaded by gridora.config into os.environ) can't silently route
TwakClient calls to a live `twak serve --rest` server mid-suite."""
import os

import pytest


@pytest.fixture(autouse=True)
def _no_live_twak_rest(monkeypatch):
    if os.environ.get("TWAK_REST_URL"):
        monkeypatch.delenv("TWAK_REST_URL")
