from __future__ import annotations

import copy
import json
import math
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from .models import iso_utc, utc_now
from .startup_cache import SnapshotStore


LOCAL_SYMBOL = "000660.KS"
ADR_SYMBOL = "SKHY"
FX_SYMBOL = "KRW=X"
ORDINARY_SHARES_PER_ADR = 0.1
REFRESH_INTERVAL_SECONDS = 15


def calculate_adr_premium(
    local_price_krw: float,
    adr_price_usd: float,
    krw_per_usd: float,
    *,
    ordinary_shares_per_adr: float = ORDINARY_SHARES_PER_ADR,
) -> tuple[float, float]:
    if any(not math.isfinite(value) or value <= 0 for value in (local_price_krw, adr_price_usd, krw_per_usd, ordinary_shares_per_adr)):
        raise ValueError("ADR 溢价计算所需报价必须大于零")
    local_equivalent_usd = (
        local_price_krw * ordinary_shares_per_adr / krw_per_usd
    )
    premium_pct = (adr_price_usd / local_equivalent_usd - 1.0) * 100.0
    return round(local_equivalent_usd, 4), round(premium_pct, 4)


class HynixAdrMonitor:
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
        self._snapshot = _empty_snapshot()
        self._snapshot_store = snapshot_store
        saved = snapshot_store.load("adr") if snapshot_store else None
        if saved and saved.get("available") and isinstance(saved.get("premium_pct"), (int, float)):
            self._snapshot = {**saved, "status": "stale", "restored_from_cache": True, "error": "上次溢价快照，等待新报价确认"}

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="hynix-adr-premium",
                daemon=True,
            )
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        if self._snapshot_store:
            self._snapshot_store.save("adr", self.snapshot(), force=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def refresh(self) -> dict[str, Any]:
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                local_future = executor.submit(
                    _fetch_yahoo_quote, LOCAL_SYMBOL, self.timeout_seconds
                )
                adr_future = executor.submit(
                    _fetch_yahoo_quote, ADR_SYMBOL, self.timeout_seconds
                )
                fx_future = executor.submit(
                    _fetch_yahoo_quote, FX_SYMBOL, self.timeout_seconds
                )
                local = local_future.result()
                adr = adr_future.result()
                fx = fx_future.result()

            local_equivalent_usd, premium_pct = calculate_adr_premium(
                local["price"], adr["price"], fx["price"]
            )
            direction = "premium" if premium_pct > 0.05 else "discount" if premium_pct < -0.05 else "flat"
            updated = self.now()
            snapshot = {
                "available": True,
                "status": "live",
                "local_symbol": LOCAL_SYMBOL,
                "adr_symbol": ADR_SYMBOL,
                "fx_symbol": FX_SYMBOL,
                "ordinary_shares_per_adr": ORDINARY_SHARES_PER_ADR,
                "local_price_krw": local["price"],
                "adr_price_usd": adr["price"],
                "krw_per_usd": fx["price"],
                "local_equivalent_usd": local_equivalent_usd,
                "premium_pct": premium_pct,
                "direction": direction,
                "updated_at": iso_utc(updated),
                "local_quote_time": local["quote_time"],
                "adr_quote_time": adr["quote_time"],
                "fx_quote_time": fx["quote_time"],
                "source": "Yahoo Finance",
                "source_urls": {
                    "local": "https://finance.yahoo.com/quote/000660.KS/",
                    "adr": "https://finance.yahoo.com/quote/SKHY/",
                    "fx": "https://finance.yahoo.com/quote/KRW=X/",
                },
                "note": "指示性溢价；韩美市场交易时段不同，不能视为可直接套利价格。",
                "error": None,
            }
        except Exception as exc:
            with self._lock:
                previous = copy.deepcopy(self._snapshot)
            snapshot = {
                **previous,
                "status": "stale" if previous.get("available") else "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        with self._lock:
            self._snapshot = snapshot
            if self._snapshot_store:
                self._snapshot_store.save("adr", snapshot)
            return copy.deepcopy(snapshot)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.refresh()
            if self._stop_event.wait(self.interval_seconds):
                break


def _fetch_yahoo_quote(symbol: str, timeout_seconds: int) -> dict[str, Any]:
    from .us_sessions import us_quote_fields

    encoded = urllib.parse.quote(symbol, safe="")
    request = urllib.request.Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1m&range=1d&includePrePost=true",
        headers={"User-Agent": "Mozilla/5.0 QuantNewsMonitor/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read(1_000_001)
    if len(payload) > 1_000_000:
        raise ValueError(f"{symbol} 行情响应过大")
    result = (json.loads(payload).get("chart", {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        raise ValueError(f"{symbol} 暂无行情")
    meta = result.get("meta") or {}
    price = float(meta.get("regularMarketPrice") or 0.0)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"{symbol} 暂无有效价格")
    timestamp = int(meta.get("regularMarketTime") or 0)
    quote_time = (
        iso_utc(datetime.fromtimestamp(timestamp, tz=timezone.utc))
        if timestamp
        else None
    )
    previous_close = float(
        meta.get("chartPreviousClose")
        or meta.get("previousClose")
        or 0.0
    )
    change_pct = (
        (price / previous_close - 1.0) * 100.0
        if math.isfinite(previous_close) and previous_close > 0
        else None
    )
    timestamps = result.get("timestamp") or []
    quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_data.get("close") or []
    highs = [float(value) for value in (quote_data.get("high") or []) if value is not None]
    lows = [float(value) for value in (quote_data.get("low") or []) if value is not None]
    chart = [
        [int(point_time), float(close)]
        for point_time, close in zip(timestamps, closes)
        if close is not None
    ]
    return {
        "symbol": symbol,
        "price": price,
        "currency": str(meta.get("currency") or "KRW"),
        "quote_time": quote_time,
        "previous_close": previous_close or None,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "day_high": max(highs) if highs else price,
        "day_low": min(lows) if lows else price,
        "chart": chart[-240:],
        **us_quote_fields(result),
    }


def _empty_snapshot() -> dict[str, Any]:
    return {
        "available": False,
        "status": "waiting",
        "local_symbol": LOCAL_SYMBOL,
        "adr_symbol": ADR_SYMBOL,
        "fx_symbol": FX_SYMBOL,
        "ordinary_shares_per_adr": ORDINARY_SHARES_PER_ADR,
        "local_price_krw": None,
        "adr_price_usd": None,
        "krw_per_usd": None,
        "local_equivalent_usd": None,
        "premium_pct": None,
        "direction": "flat",
        "updated_at": None,
        "error": None,
        "note": "正在获取 SKHY、000660.KS 与美元兑韩元报价。",
    }
