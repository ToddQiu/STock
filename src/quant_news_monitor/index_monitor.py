from __future__ import annotations

import copy
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as clock_time, timezone
from typing import Any, Callable
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from .models import iso_utc, utc_now
from .portfolio_chart import _request_json
from .startup_cache import SnapshotStore


INDEX_DEFINITIONS = (
    {"id": "kospi", "symbol": "^KS11", "name": "韩国综合指数", "short_name": "KOSPI",
     "english_name": "KOSPI Composite Index", "market": "KR", "timezone": "Asia/Seoul",
     "provider_currency": "KRW", "news_query": '"KOSPI" index', "public_delay": "delayed"},
    {"id": "star50", "symbol": "000688.SS", "name": "科创50", "short_name": "000688",
     "english_name": "SSE STAR 50 Index", "market": "CN", "timezone": "Asia/Shanghai",
     "provider_currency": "CNY", "news_query": '"科创50" 指数', "public_delay": "unknown"},
    {"id": "sox", "symbol": "^SOX", "name": "费城半导体指数", "short_name": "SOX",
     "english_name": "PHLX Semiconductor Index", "market": "US", "timezone": "America/New_York",
     "provider_currency": "USD", "news_query": '"PHLX Semiconductor" index', "public_delay": "delayed"},
    {"id": "nasdaq", "symbol": "^IXIC", "name": "纳斯达克综合指数", "short_name": "IXIC",
     "english_name": "NASDAQ Composite", "market": "US", "timezone": "America/New_York",
     "provider_currency": "USD", "news_query": '"Nasdaq Composite" index', "public_delay": "delayed"},
)
REFRESH_INTERVAL_SECONDS = 60
IDLE_INTERVAL_SECONDS = 300


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Quote timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _identity(definition: dict) -> dict:
    symbol = quote(definition["symbol"], safe="")
    return {key: definition[key] for key in ("id", "symbol", "name", "short_name", "english_name", "market", "timezone")} | {
        "instrument_type": "INDEX", "unit": "points", "currency": None,
        "source": "Yahoo Finance", "source_url": f"https://finance.yahoo.com/quote/{symbol}/",
        "news_url": "https://www.bing.com/news/search?" + urlencode({"q": definition["news_query"]}),
        "news_kind": "external_search", "news_query": definition["news_query"],
    }


def _empty_quote(definition: dict) -> dict:
    return {**_identity(definition), "price": None, "previous_close": None, "change": None,
            "change_pct": None, "day_high": None, "day_low": None, "quote_time": None,
            "fetched_at": None, "error": None, "restored_from_cache": False,
            "delay_status": definition["public_delay"], "delay_seconds": None,
            "session_start": None, "session_end": None}


def _fetch_index_quote(definition: dict, timeout_seconds: int) -> dict:
    encoded = quote(definition["symbol"], safe="")
    payload = _request_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=1d&includePrePost=false",
        timeout_seconds=timeout_seconds,
    )
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        if (meta["symbol"] != definition["symbol"] or meta["instrumentType"] != "INDEX"
                or meta["currency"] != definition["provider_currency"]):
            raise ValueError("Index identity mismatch (ETF proxies are not accepted)")
        stamp = _number(meta["regularMarketTime"])
        if stamp is None or stamp <= 0:
            raise ValueError("Missing quote timestamp")
        timestamp = datetime.fromtimestamp(stamp, timezone.utc)
        price = _number(meta["regularMarketPrice"])
        if price is None or price <= 0:
            raise ValueError("Missing positive finite index value")
    except (KeyError, IndexError, TypeError, OverflowError, OSError) as exc:
        raise ValueError("Invalid index response") from exc
    previous = _number(meta.get("previousClose")) or _number(meta.get("chartPreviousClose"))
    previous = previous if previous and previous > 0 else None
    delay_minutes = _number(meta.get("exchangeDataDelayedBy"))
    delay_seconds = delay_minutes * 60 if delay_minutes is not None and 0 <= delay_minutes <= 1440 else None
    delay_status = definition["public_delay"] if delay_seconds is None else "delayed" if delay_seconds else "reported_realtime"
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = _number(period.get("start")), _number(period.get("end"))
    if start is None or end is None or not 0 < start < end or end - start > 86400:
        start = end = None
    high, low = _number(meta.get("regularMarketDayHigh")), _number(meta.get("regularMarketDayLow"))
    return {**_empty_quote(definition), "price": price, "quote_time": iso_utc(timestamp),
            "previous_close": previous, "change": price - previous if previous else None,
            "change_pct": _number((price / previous - 1) * 100) if previous else None,
            "day_high": high if high and high >= price else None,
            "day_low": low if low and 0 < low <= price else None,
            "provider_currency": meta["currency"], "delay_status": delay_status,
            "delay_seconds": delay_seconds, "session_start": start, "session_end": end}


def _session_state(definition: dict, row: dict, now: datetime) -> tuple[str, str]:
    local = now.astimezone(ZoneInfo(definition["timezone"]))
    clock = local.time().replace(tzinfo=None)
    hours = {"KR": (clock_time(9), clock_time(15, 30)),
             "CN": (clock_time(9, 30), clock_time(15)),
             "US": (clock_time(9, 30), clock_time(16))}
    opening, closing = hours[definition["market"]]
    basis = "regular_hours_not_holiday_calendar"
    # shortcut: no exchange holiday calendar; uncertain weekdays remain unknown.
    if local.weekday() >= 5 or clock < opening or clock >= closing:
        return "closed", basis
    if definition["market"] == "CN" and clock_time(11, 30) <= clock < clock_time(13):
        return "break", basis
    try:
        start, end = float(row["session_start"]), float(row["session_end"])
        session_date = datetime.fromtimestamp(start, local.tzinfo).date()
        if session_date == local.date() and start < end:
            return ("open" if start <= now.timestamp() < end else "closed"), "provider_session"
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    return "unknown", basis


class IndexMonitor:
    """Four real indices, a nonblocking read snapshot and a single-flight refresh."""

    def __init__(self, *, interval_seconds: int = REFRESH_INTERVAL_SECONDS,
                 timeout_seconds: int = 10, now: Callable[[], datetime] = utc_now,
                 snapshot_store: SnapshotStore | None = None):
        self.interval_seconds = max(30, int(interval_seconds))
        self.timeout_seconds = timeout_seconds
        self.now = now
        self._snapshot_store = snapshot_store
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_refresh_at = 0.0
        self._snapshot = {"schema_version": 1, "updated_at": None, "interval_seconds": self.interval_seconds,
                          "quotes": [_empty_quote(d) for d in INDEX_DEFINITIONS]}
        saved = snapshot_store.load("indices") if snapshot_store else None
        if isinstance(saved, dict) and saved.get("schema_version") == 1 and isinstance(saved.get("quotes"), list):
            for index, definition in enumerate(INDEX_DEFINITIONS):
                for row in saved["quotes"]:
                    if not isinstance(row, dict) or row.get("symbol") != definition["symbol"]:
                        continue
                    try:
                        self._validate(row, definition, self.now())
                    except (ValueError, TypeError):
                        continue
                    restored = {key: copy.deepcopy(row[key]) for key in _empty_quote(definition) if key in row}
                    self._snapshot["quotes"][index].update(restored)
                    self._snapshot["quotes"][index].update(_identity(definition), restored_from_cache=True,
                                                          error="Cached quote awaiting source confirmation")

    @staticmethod
    def _validate(row: dict, definition: dict, now: datetime) -> None:
        if row.get("symbol") != definition["symbol"] or row.get("instrument_type") != "INDEX":
            raise ValueError("Index identity mismatch")
        if not isinstance(row.get("price"), (int, float)):
            raise ValueError("Index value must be a number")
        price = _number(row.get("price"))
        if price is None or price <= 0:
            raise ValueError("Invalid index value")
        timestamp = _timestamp(row.get("quote_time"))
        if timestamp.timestamp() <= 0 or (timestamp - now).total_seconds() > 60:
            raise ValueError("Invalid or future quote timestamp")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = copy.deepcopy(self._snapshot)
        now = self.now()
        for definition, row in zip(INDEX_DEFINITIONS, result["quotes"]):
            row["session_state"], row["session_basis"] = _session_state(definition, row, now)
            age = max(0, (now - _timestamp(row["quote_time"])).total_seconds()) if row["quote_time"] else None
            row["quote_age_seconds"] = age
            row["cached"] = bool(row["restored_from_cache"] or row["error"])
            threshold = max(1800, (_number(row.get("delay_seconds")) or 0) + 120)
            if row["price"] is None:
                row["status"] = "unavailable" if row["error"] else "waiting"
            elif row["cached"]:
                row["status"] = "stale"
            elif row["session_state"] in {"closed", "break"}:
                row["status"] = row["session_state"]
            elif age is None or age > threshold:
                row["status"] = "stale"
            else:
                row["status"] = "delayed" if row["delay_status"] == "delayed" else "available"
        usable = [row for row in result["quotes"] if row["price"] is not None]
        result["status"] = "waiting" if not usable and not any(q["error"] for q in result["quotes"]) else (
            "error" if not usable else "partial" if len(usable) < 4 or any(q["status"] == "stale" for q in usable)
            else "closed" if all(q["session_state"] in {"closed", "break"} for q in usable) else "ready")
        result["as_of"] = iso_utc(now)
        result["refresh_in_progress"] = self._refresh_lock.locked()
        result["restored_from_cache"] = any(q["restored_from_cache"] for q in result["quotes"])
        result["errors"] = [f'{q["symbol"]}: {q["error"]}' for q in result["quotes"] if q["error"]]
        return result

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        if not self._refresh_lock.acquire(blocking=False):
            return self.snapshot()
        try:
            if not force and time.monotonic() < self._next_refresh_at:
                result = self.snapshot()
                result["refresh_in_progress"] = False
                return result
            with self._lock:
                rows = copy.deepcopy(self._snapshot["quotes"])
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="index-quote") as executor:
                jobs = {executor.submit(_fetch_index_quote, definition, self.timeout_seconds): (i, definition)
                        for i, definition in enumerate(INDEX_DEFINITIONS)}
                for future in as_completed(jobs):
                    index, definition = jobs[future]
                    try:
                        row = future.result()
                        fetched_at = self.now()
                        self._validate(row, definition, fetched_at)
                        prior_time = rows[index].get("quote_time")
                        if prior_time and _timestamp(row["quote_time"]) < _timestamp(prior_time):
                            raise ValueError("Older source quote rejected; keeping last valid value")
                        rows[index] = {**row, **_identity(definition), "fetched_at": iso_utc(fetched_at),
                                       "error": None, "restored_from_cache": False}
                    except Exception as exc:
                        rows[index]["error"] = f"{type(exc).__name__}: {exc}"[:400]
            with self._lock:
                self._snapshot.update(quotes=rows, updated_at=iso_utc(self.now()))
                payload = copy.deepcopy(self._snapshot)
            if self._snapshot_store:
                self._snapshot_store.save("indices", payload)
            idle = all(_session_state(d, q, self.now())[0] in {"closed", "break"}
                       for d, q in zip(INDEX_DEFINITIONS, rows))
            self._next_refresh_at = time.monotonic() + (max(IDLE_INTERVAL_SECONDS, self.interval_seconds) if idle else self.interval_seconds)
        finally:
            self._refresh_lock.release()
        return self.snapshot()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="index-monitor", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        # Serialize final persistence with any in-flight refresh, never overwrite newer values.
        with self._refresh_lock:
            if self._snapshot_store:
                with self._lock:
                    self._snapshot_store.save("indices", copy.deepcopy(self._snapshot), force=True)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.refresh()
            if self._stop_event.wait(self.interval_seconds):
                break
