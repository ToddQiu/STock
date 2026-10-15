from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .config import load_config
from .config_store import ConfigStore
from .models import iso_utc, utc_now
from .news_transport import news_http, retry_delay
from .social_discovery import COMMON_ALIASES


INTERVAL = 6 * 3600
LIQUID_THRESHOLD = 100_000_000
COLUMNS = ["name", "description", "close", "volume", "average_volume_30d_calc",
           "exchange", "sector", "industry", "earnings_release_next_date", "earnings_release_date"]
SCANNER_URL = "https://scanner.tradingview.com/america/scan"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
NY = ZoneInfo("America/New_York")


def symbol_key(value: str) -> str:
    return str(value).upper().strip().replace("-", ".")


def number(value, *, nonnegative=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and (not nonnegative or result >= 0) else None
    except (TypeError, ValueError, OverflowError):
        return None


def event_date(value):
    timestamp = number(value, nonnegative=True)
    if timestamp is None:
        return None
    try:
        result = datetime.fromtimestamp(timestamp, timezone.utc).date()
        return result.isoformat() if 2000 <= result.year <= 2100 else None
    except (ValueError, OverflowError, OSError):
        return None


def parse_directory(payload: dict) -> list[dict]:
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows or int(payload.get("totalCount", 0)) > len(rows):
        raise ValueError("美股公司目录为空或未完整返回，保留上一份目录")
    items = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Nasdaq 财报记录格式异常")
        if not isinstance(row, dict) or not isinstance(row.get("d"), list) or len(row["d"]) != len(COLUMNS):
            raise ValueError("公司目录字段不完整")
        data = dict(zip(COLUMNS, row["d"]))
        symbol = str(data["name"] or "").upper()
        name = str(data["description"] or "")
        if (data["exchange"] not in {"NASDAQ", "NYSE", "AMEX"}
                or not re.fullmatch(r"[A-Z]{1,6}(?:[.-][A-Z])?", symbol)
                or re.search(r"\b(preferred|warrants?|debentures?|notes due|units?|rights)\b", name, re.I)):
            continue
        price, volume, average = (number(data[key], nonnegative=True) for key in ("close", "volume", "average_volume_30d_calc"))
        if price == 0:
            price = None
        dollar = price * volume if price is not None and volume is not None else None
        avg_dollar = price * average if price is not None and average is not None else None
        items[symbol_key(symbol)] = {
            "symbol": symbol, "name": name, "market": "US", "exchange": data["exchange"],
            "sector": data["sector"], "industry": data["industry"], "price": price,
            "dollar_volume": number(dollar, nonnegative=True),
            "average_dollar_volume": number(avg_dollar, nonnegative=True),
            "earnings_date": event_date(data["earnings_release_next_date"]),
            "previous_earnings_date": event_date(data["earnings_release_date"]),
            "date_status": "estimated", "session": "unknown", "eps_forecast": None,
            "fiscal_quarter": None, "date_conflict": False,
            "sources": [{"name": "TradingView", "date": event_date(data["earnings_release_next_date"]),
                         "url": f"https://www.tradingview.com/symbols/{data['exchange']}-{quote(symbol, safe='')}/financials-earnings/"}],
        }
    if not items:
        raise ValueError("没有可识别的美股公司")
    return list(items.values())


def enrich_calendar(items: list[dict], payload: dict, day: date) -> None:
    data = payload.get("data")
    if data is None and (payload.get("status") or {}).get("rCode") == 200:
        return
    if not isinstance(data, dict) or (payload.get("status") or {}).get("rCode") != 200:
        raise ValueError("Nasdaq 日历响应不可用")
    try:
        source_day = datetime.strptime(data["asOf"], "%a, %b %d, %Y").date()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Nasdaq 日历缺少日期，不能归属到请求日期") from exc
    if source_day != day:
        raise ValueError("Nasdaq 返回日期与请求日期不一致")
    rows = data.get("rows") or []
    if not isinstance(rows, list):
        raise ValueError("Nasdaq 日历行格式异常")
    by_symbol = {symbol_key(item["symbol"]): item for item in items}
    for row in rows:
        item = by_symbol.get(symbol_key(row.get("symbol", "")))
        if item is None:
            continue
        release = day.isoformat()
        prior_dates = {source["date"] for source in item["sources"] if source.get("date")}
        item["date_conflict"] = item["date_conflict"] or bool(prior_dates - {release})
        item["sources"].append({"name": "Nasdaq", "date": release,
                                "url": f"https://www.nasdaq.com/market-activity/earnings?date={release}"})
        # Retain every source date; a second Nasdaq date must not silently overwrite the first.
        if not any(source["name"] == "Nasdaq" for source in item["sources"][:-1]):
            item["earnings_date"] = release
            item["session"] = {"time-pre-market": "before_open", "time-after-hours": "after_close"}.get(row.get("time"), "unknown")
            eps = str(row.get("epsForecast") or "").replace("$", "").replace(",", "").strip()
            if eps.startswith("(") and eps.endswith(")"):
                eps = "-" + eps[1:-1]
            item["eps_forecast"] = number(eps)
            item["fiscal_quarter"] = row.get("fiscalQuarterEnding")


class EarningsCalendar:
    """One replaceable SQLite snapshot, independent of the news pool and research versions."""

    def __init__(self, database: Path, config_store: ConfigStore):
        self.database, self.config_store = Path(database), config_store
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._worker = None
        self._busy = False
        self._not_before = 0.0
        self._error = None
        self._cache = {"items": [], "collected_at": None, "warnings": []}
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database, timeout=30)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS earnings_calendar_cache (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            row = connection.execute("SELECT payload FROM earnings_calendar_cache WHERE id=1").fetchone()
        if row:
            try:
                saved = json.loads(row[0])
                if saved.get("schema_version") == 1 and isinstance(saved.get("items"), list):
                    self._cache = saved
            except (ValueError, TypeError, AttributeError):
                self._error = "财报日历缓存损坏，等待重新采集"

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="earnings-calendar", daemon=True)
            self._thread.start()

    def shutdown(self):
        self._stop.set()
        for thread in (self._thread, self._worker):
            if thread is not None:
                thread.join(timeout=2)

    def _loop(self):
        while not self._stop.is_set():
            runtime = self.runtime()
            if runtime["stale"]:
                self.refresh()
            self._stop.wait(60)

    def trigger_refresh(self):
        with self._lock:
            if self._busy or self._stop.is_set() or time.monotonic() < self._not_before:
                return False
            self._busy = True
            self._worker = threading.Thread(target=self._refresh_reserved, name="earnings-calendar-refresh", daemon=True)
            self._worker.start()
            return True

    def refresh(self):
        with self._lock:
            if self._busy or self._stop.is_set() or time.monotonic() < self._not_before:
                return False
            self._busy = True
        return self._refresh_reserved()

    def _fetch_directory(self):
        body = {"filter": [{"left": "type", "operation": "equal", "right": "stock"},
                           {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]}],
                "columns": COLUMNS, "range": [0, 10000], "sort": {"sortBy": "volume", "sortOrder": "desc"}}
        request = Request(SCANNER_URL, data=json.dumps(body).encode("utf-8"),
                          headers={**HEADERS, "Content-Type": "application/json", "Origin": "https://www.tradingview.com"})
        with urlopen(request, timeout=20) as response:
            content = response.read(6_000_001)
        if len(content) > 6_000_000:
            raise ValueError("公司目录响应过大")
        return parse_directory(json.loads(content))

    def _refresh_reserved(self):
        try:
            items = self._fetch_directory()
            collected_at = iso_utc(utc_now())
            today = utc_now().astimezone(NY).date()
            warnings, covered_days = [], []
            for index in range(14):
                if self._stop.is_set():
                    return False
                day = today + timedelta(days=index)
                if day.weekday() >= 5:
                    continue
                try:
                    payload = json.loads(news_http.fetch(
                        f"https://api.nasdaq.com/api/calendar/earnings?date={day.isoformat()}",
                        timeout=8, ttl=300, headers={**HEADERS, "Origin": "https://www.nasdaq.com"}))
                    enrich_calendar(items, payload, day)
                    covered_days.append(day.isoformat())
                except (OSError, ValueError, HTTPException, TypeError) as exc:
                    warnings.append(f"Nasdaq 盘前盘后补充暂停：{exc}")
                    break
                if self._stop.wait(0.3):
                    return False
            snapshot = {"schema_version": 1, "items": items, "collected_at": collected_at,
                        "enrichment_checked_at": iso_utc(utc_now()), "warnings": warnings,
                        "nasdaq_covered_days": covered_days}
            with closing(sqlite3.connect(self.database, timeout=30)) as connection, connection:
                connection.execute("INSERT OR REPLACE INTO earnings_calendar_cache VALUES (1, ?)",
                                   (json.dumps(snapshot, ensure_ascii=False, allow_nan=False),))
            with self._lock:
                self._cache, self._error, self._not_before = snapshot, None, time.monotonic() + 300
            return True
        except (OSError, ValueError, TypeError, HTTPException, sqlite3.Error) as exc:
            delay = retry_delay(exc.headers) if isinstance(exc, HTTPError) else 900
            with self._lock:
                self._error = f"财报预告采集失败，保留旧目录：{exc}"
                self._not_before = time.monotonic() + max(300, delay)
            return False
        finally:
            with self._lock:
                self._busy = False

    def runtime(self):
        with self._lock:
            collected = self._cache.get("collected_at")
            try:
                age = (utc_now() - datetime.fromisoformat(collected.replace("Z", "+00:00"))).total_seconds()
            except (TypeError, ValueError, AttributeError):
                age = float("inf")
            return {"refresh_in_progress": self._busy, "collected_at": collected,
                    "stale": age < -60 or age >= INTERVAL or bool(self._error), "error": self._error,
                    "warnings": list(self._cache.get("warnings", [])), "interval_seconds": INTERVAL,
                    "retry_after_seconds": max(0, math.ceil(self._not_before - time.monotonic()))}

    def query(self, *, search="", days="30", scope="liquid", sort="date", include_unknown=False,
              offset=0, limit=100, today=None):
        if str(days) not in {"7", "30", "90", "all"} or scope not in {"liquid", "all", "watchlist", "portfolio"} or sort not in {"date", "dollar", "name"}:
            raise ValueError("财报日历筛选无效")
        offset, limit = int(offset), int(limit)
        if offset < 0 or not 1 <= limit <= 10000:
            raise ValueError("分页参数无效")
        today = today or utc_now().astimezone(NY).date()
        with self._lock:
            items = copy.deepcopy(self._cache["items"])
        raw = self.config_store.read()
        watch = {symbol_key(item["symbol"]) for item in raw.get("watchlist", []) if item.get("market") == "US"}
        held = {symbol_key(item["symbol"]) for item in (raw.get("portfolio") or {}).get("positions", []) if item.get("market") == "US"}
        popular = {symbol_key(symbol) for symbol in COMMON_ALIASES} | watch | held
        for item in items:
            key = symbol_key(item["symbol"])
            item.update(watched=key in watch, held=key in held, popular=key in popular)
            item["aliases"] = list(COMMON_ALIASES.get(item["symbol"], ()))
            item["aliases"].extend(str(entry.get("name") or "") for entry in raw.get("watchlist", []) if entry.get("market") == "US" and symbol_key(entry["symbol"]) == key)
            item["liquid"] = max(item.get("dollar_volume") or 0, item.get("average_dollar_volume") or 0) >= LIQUID_THRESHOLD
            release = item.get("earnings_date")
            item["days_until"] = (date.fromisoformat(release) - today).days if release else None
            item["date_status"] = "conflict" if item["date_conflict"] else "unknown" if release is None else "expired" if item["days_until"] < 0 else "estimated"
        liquid = [item for item in items if item["liquid"] or item["popular"]]
        unknown = sum(item["days_until"] is None or item["days_until"] < 0 for item in liquid)
        matches = []
        term = search.strip().casefold()
        for item in items:
            if scope == "liquid" and not (item["liquid"] or item["popular"]):
                continue
            if scope == "watchlist" and not item["watched"] or scope == "portfolio" and not item["held"]:
                continue
            if term and term not in (" ".join(str(item.get(key) or "") for key in ("symbol", "name", "sector", "industry")) + " " + " ".join(item["aliases"])).casefold():
                continue
            until = item["days_until"]
            if until is None or until < 0:
                if not include_unknown:
                    continue
            elif str(days) != "all" and until >= int(days):
                continue
            matches.append(item)
        if sort == "dollar":
            matches.sort(key=lambda item: (-(item["dollar_volume"] or 0), item["symbol"]))
        elif sort == "name":
            matches.sort(key=lambda item: (item["name"].casefold(), item["symbol"]))
        else:
            matches.sort(key=lambda item: (item["days_until"] if item["days_until"] is not None and item["days_until"] >= 0 else 100000,
                                           -(item["dollar_volume"] or 0), item["symbol"]))
        offset = min(offset, ((max(1, len(matches)) - 1) // limit) * limit)
        return {"items": matches[offset:offset + limit], "total": len(matches), "offset": offset, "limit": limit,
                "today": today.isoformat(), "timezone": "America/New_York", "runtime": self.runtime(),
                "coverage": {"companies": len(items), "liquid_popular": len(liquid), "unknown_dates": unknown,
                             "threshold_usd": LIQUID_THRESHOLD},
                "method": "热门核心公司、持仓/股票池，以及最新价格×最新成交量或30日均量至少1亿美元的公司；日期为公开来源预计，非公司确认。"}

    def annotations(self, *, today=None):
        # Full coverage, independent of the calendar page's filters and pagination.
        result = self.query(days="all", scope="all", include_unknown=True, limit=10000, today=today)
        fields = ("market", "symbol", "earnings_date", "session", "date_status")
        items = [{**{key: item.get(key) for key in fields},
                  "sources": [{"name": source.get("name"), "date": source.get("date")} for source in item.get("sources", [])]}
                 for item in result["items"]]
        return {"items": items, "today": result["today"], "timezone": result["timezone"],
                "runtime": result["runtime"], "coverage": result["coverage"]}


def main():
    parser = argparse.ArgumentParser(description="美股财报预告目录采集（不启动服务器）")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    calendar = EarningsCalendar(load_config(args.config).storage.database, ConfigStore(Path(args.config)))
    success = calendar.refresh() if args.refresh else True
    result = calendar.query()
    print(json.dumps({key: result[key] for key in ("coverage", "total", "runtime")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
