from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable

from .adr_monitor import _fetch_yahoo_quote
from .models import iso_utc, utc_now
from .startup_cache import SnapshotStore, restore_quotes


KOREA_STOCKS = (
    ("005930.KS", "三星电子", "Samsung Electronics"),
    ("000660.KS", "SK 海力士", "SK hynix"),
)
REFRESH_INTERVAL_SECONDS = 5
IDLE_INTERVAL_SECONDS = 60
KOREA_TIMEZONE = timezone(timedelta(hours=9), name="KST")
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(15, 30)


def is_korea_trading_session(value: datetime) -> bool:
    local = value.astimezone(KOREA_TIMEZONE)
    return local.weekday() < 5 and MARKET_OPEN <= local.time().replace(tzinfo=None) <= MARKET_CLOSE


def _quote_is_fresh(quote: dict, now: datetime) -> bool:
    try:
        timestamp = datetime.fromisoformat(str(quote.get("quote_time") or "").replace("Z", "+00:00"))
        return timestamp.tzinfo is not None and -60 <= (now-timestamp).total_seconds() <= 120
    except (ValueError, TypeError):
        return False


class KoreaStockMonitor:
    def __init__(
        self,
        *,
        interval_seconds: int = REFRESH_INTERVAL_SECONDS,
        timeout_seconds: int = 10,
        now: Callable[[], datetime] = utc_now,
        snapshot_store: SnapshotStore | None = None,
    ):
        self.interval_seconds = max(5, int(interval_seconds))
        self.timeout_seconds = timeout_seconds
        self.now = now
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot_store = snapshot_store
        self._snapshot = restore_quotes(snapshot_store, "korea") or _empty_snapshot(self.interval_seconds)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="korea-stock-quotes",
                daemon=True,
            )
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        if self._snapshot_store:
            self._snapshot_store.save("korea", self.snapshot(), force=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = copy.deepcopy(self._snapshot)
        now = self.now()
        snapshot["market_open"] = is_korea_trading_session(now)
        if not snapshot["market_open"] and snapshot.get("quotes"):
            snapshot["status"] = "closed"
        elif snapshot["market_open"] and snapshot.get("quotes"):
            for quote in snapshot["quotes"]:
                if not _quote_is_fresh(quote, now):
                    quote["status"] = "stale"
            snapshot["status"] = "live" if all(q["status"] == "live" for q in snapshot["quotes"]) and len(snapshot["quotes"]) == len(KOREA_STOCKS) else "partial"
        return snapshot

    def refresh(self) -> dict[str, Any]:
        refreshed_at = self.now()
        market_open = is_korea_trading_session(refreshed_at)
        with self._lock:
            previous = copy.deepcopy(self._snapshot)
        previous_quotes = {
            str(item.get("symbol")): item for item in previous.get("quotes", [])
        }
        quotes: list[dict[str, Any]] = []
        errors: list[str] = []
        stock_names = {symbol: (name, english_name) for symbol, name, english_name in KOREA_STOCKS}

        with ThreadPoolExecutor(max_workers=len(KOREA_STOCKS)) as executor:
            futures = {
                executor.submit(_fetch_yahoo_quote, symbol, self.timeout_seconds): symbol
                for symbol, _, _ in KOREA_STOCKS
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    quote = future.result()
                    name, english_name = stock_names[symbol]
                    quotes.append(
                        {
                            **quote,
                            "name": name,
                            "english_name": english_name,
                            "status": "stale" if market_open and not _quote_is_fresh(quote, refreshed_at) else "live",
                            "error": None,
                            "source": "Yahoo Finance",
                            "source_url": f"https://finance.yahoo.com/quote/{symbol}/",
                        }
                    )
                except Exception as exc:
                    errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
                    stale = previous_quotes.get(symbol)
                    if stale:
                        quotes.append({**stale, "status": "stale", "error": str(exc)})

        order = {symbol: index for index, (symbol, _, _) in enumerate(KOREA_STOCKS)}
        quotes.sort(key=lambda item: order.get(str(item.get("symbol")), 99))
        live_count = sum(1 for item in quotes if item.get("status") == "live")
        status = "live" if live_count == len(KOREA_STOCKS) else "partial" if quotes else "error"
        if quotes and not market_open:
            status = "closed"
        snapshot = {
            "status": status,
            "market_open": market_open,
            "updated_at": iso_utc(refreshed_at),
            "interval_seconds": self.interval_seconds,
            "source": "Yahoo Finance",
            "quotes": quotes,
            "errors": errors,
        }
        with self._lock:
            self._snapshot = snapshot
            if self._snapshot_store:
                self._snapshot_store.save("korea", snapshot)
            return copy.deepcopy(snapshot)

    def _loop(self) -> None:
        initial_refresh = True
        while not self._stop_event.is_set():
            market_open = is_korea_trading_session(self.now())
            if initial_refresh or market_open:
                self.refresh()
                initial_refresh = False
            wait_seconds = self.interval_seconds if market_open else IDLE_INTERVAL_SECONDS
            if self._stop_event.wait(wait_seconds):
                break


def _empty_snapshot(interval_seconds: int) -> dict[str, Any]:
    return {
        "status": "waiting",
        "market_open": is_korea_trading_session(utc_now()),
        "updated_at": None,
        "interval_seconds": interval_seconds,
        "source": "Yahoo Finance",
        "quotes": [],
        "errors": [],
    }
