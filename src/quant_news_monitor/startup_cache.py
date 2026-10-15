from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any


class SnapshotStore:
    """One replaceable, non-authoritative snapshot per monitor in the existing DB."""

    KEYS = frozenset({"assets", "portfolio", "korea", "adr", "charts", "indices"})
    MAX_BYTES = 4_000_000

    def __init__(self, path: str | Path, *, interval_seconds: float = 15):
        self.path = Path(path)
        self.interval_seconds = interval_seconds
        self._lock = threading.RLock()
        self._saved_at: dict[str, float] = {}

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS startup_snapshots (key TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL)")
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def load(self, key: str) -> dict[str, Any] | None:
        if key not in self.KEYS:
            return None
        try:
            with self._lock, closing(self._connect()) as connection:
                row = connection.execute("SELECT version, payload FROM startup_snapshots WHERE key=?", (key,)).fetchone()
            if not row or row[0] != 1 or not isinstance(row[1], str) or len(row[1].encode("utf-8")) > self.MAX_BYTES:
                return None
            value = json.loads(row[1])
            return value if isinstance(value, dict) else None
        except (OSError, sqlite3.Error, ValueError, TypeError):
            logging.getLogger(__name__).warning("Startup snapshot could not be read: %s", key)
            return None

    def save(self, key: str, payload: dict[str, Any], *, force: bool = False) -> bool:
        if key not in self.KEYS:
            return False
        try:
            with self._lock:
                now = time.monotonic()
                if not force and now - self._saved_at.get(key, -float("inf")) < self.interval_seconds:
                    return False
                encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, default=lambda value: value.isoformat())
                if len(encoded.encode("utf-8")) > self.MAX_BYTES:
                    return False
                with closing(self._connect()) as connection, connection:
                    connection.execute("INSERT OR REPLACE INTO startup_snapshots VALUES (?, 1, ?)", (key, encoded))
                self._saved_at[key] = now
                return True
        except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
            logging.getLogger(__name__).warning("Startup snapshot could not be saved: %s", key)
            return False


def restore_quotes(store: SnapshotStore | None, key: str) -> dict[str, Any] | None:
    value = store.load(key) if store else None
    if not value or not isinstance(value.get("quotes"), list) or not value["quotes"]:
        return None
    if not all(isinstance(quote, dict) and quote.get("symbol") for quote in value["quotes"]):
        return None
    if not all(isinstance(quote.get("price"), (int, float)) and math.isfinite(quote["price"]) and quote["price"] > 0 for quote in value["quotes"]):
        return None
    rates = value.get("fx_rates") or {}
    value["fx_rates"] = {key: rate for key, rate in rates.items() if isinstance(rate, (int, float)) and math.isfinite(rate) and rate > 0} if isinstance(rates, dict) else {}
    value.update(status="stale", restored_from_cache=True, errors=["上次行情快照，等待数据源重新确认"])
    for quote in value["quotes"]:
        quote.update(status="stale", error="上次行情快照，等待重新确认")
    return value
