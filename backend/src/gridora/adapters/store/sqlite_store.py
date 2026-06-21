"""Local SQLite StorePort: fills + open instances for crash recovery.

A grid is long-lived and the engine drives off fills, so a restart must be able to
replay realized PnL / inventory and re-attach to its open instances. Uses the stdlib
`sqlite3` (no extra deps); calls are short and synchronous under the async surface,
which is fine for a single-agent process.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import Sequence

from ...domain.models import Fill, GridConfig, RiskPreset, Side, Spacing, Venue

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    instance_id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    regime_json TEXT,
    state       TEXT NOT NULL DEFAULT 'OPEN'
);
CREATE TABLE IF NOT EXISTS fills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id  TEXT NOT NULL,
    market       TEXT NOT NULL,
    side         TEXT NOT NULL,
    price        TEXT NOT NULL,
    qty          TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    level        INTEGER NOT NULL,
    ts           INTEGER NOT NULL,
    tx_hash      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fills_instance ON fills(instance_id);
"""


class SqliteStore:
    def __init__(self, path: str = "gridora.db") -> None:
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    async def record_fill(self, fill: Fill) -> None:
        self._db.execute(
            "INSERT INTO fills(instance_id,market,side,price,qty,external_id,level,ts,tx_hash)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (fill.instance_id, fill.market, fill.side.value, str(fill.price), str(fill.qty),
             fill.external_id, fill.level, fill.ts, fill.tx_hash),
        )
        self._db.commit()

    async def save_instance(self, cfg: GridConfig, regime_json: str = "") -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO instances(instance_id,config_json,regime_json,state)"
            " VALUES(?,?,?,'OPEN')",
            (cfg.instance_id, json.dumps(_cfg_to_dict(cfg)), regime_json),
        )
        self._db.commit()

    async def mark_closed(self, instance_id: str) -> None:
        self._db.execute("UPDATE instances SET state='CLOSED' WHERE instance_id=?", (instance_id,))
        self._db.commit()

    async def load_open_instances(self) -> Sequence[GridConfig]:
        rows = self._db.execute(
            "SELECT config_json FROM instances WHERE state='OPEN'").fetchall()
        return [_cfg_from_dict(json.loads(r[0])) for r in rows]

    async def load_fills(self, instance_id: str) -> list[Fill]:
        rows = self._db.execute(
            "SELECT market,side,price,qty,external_id,level,ts,tx_hash FROM fills"
            " WHERE instance_id=? ORDER BY id", (instance_id,)).fetchall()
        return [
            Fill(instance_id=instance_id, market=r[0], side=Side(r[1]), price=Decimal(r[2]),
                 qty=Decimal(r[3]), external_id=r[4], level=r[5], ts=r[6], tx_hash=r[7])
            for r in rows
        ]

    def close(self) -> None:
        self._db.close()


def _cfg_to_dict(cfg: GridConfig) -> dict:
    return {
        "instance_id": cfg.instance_id, "market": cfg.market, "lower": str(cfg.lower),
        "upper": str(cfg.upper), "levels": cfg.levels, "order_size": str(cfg.order_size),
        "quote_per_grid": str(cfg.quote_per_grid), "spacing": cfg.spacing.value,
        "max_levels": cfg.max_levels, "preset": cfg.preset.value, "venue": cfg.venue.value,
        "policy_version": cfg.policy_version, "bias": cfg.bias,
    }


def _cfg_from_dict(d: dict) -> GridConfig:
    return GridConfig(
        instance_id=d["instance_id"], market=d["market"], lower=Decimal(d["lower"]),
        upper=Decimal(d["upper"]), levels=d["levels"], order_size=Decimal(d["order_size"]),
        quote_per_grid=Decimal(d["quote_per_grid"]), spacing=Spacing(d["spacing"]),
        max_levels=d["max_levels"], preset=RiskPreset(d["preset"]), venue=Venue(d["venue"]),
        policy_version=d["policy_version"], bias=d["bias"],
    )
