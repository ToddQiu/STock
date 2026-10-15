from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from .models import WatchItem, iso_utc, utc_now
from .news_transport import news_http
from .stock_mentions import AMBIGUOUS_TICKERS, GENERIC_ASSET_NAMES


DIRECTORIES = ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
               "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt")
COMMON_ALIASES = {"GOOG": ("Google",), "GOOGL": ("Google",), "NVDA": ("Nvidia",),
                  "MSFT": ("Microsoft",), "PLTR": ("Palantir",), "AMD": ("AMD",),
                  "AMZN": ("Amazon",), "TSLA": ("Tesla",), "COIN": ("Coinbase",),
                  "AAPL": ("Apple",), "META": ("Meta Platforms", "Facebook"), "RIVN": ("Rivian",)}
COMMON_ALIASES.update({"ADBE": ("Adobe",), "INTC": ("Intel",), "AVGO": ("Broadcom",), "MU": ("Micron",),
                       "ORCL": ("Oracle",), "NFLX": ("Netflix",), "SPOT": ("Spotify",), "UBER": ("Uber",),
                       "HOOD": ("Robinhood",), "SNOW": ("Snowflake",), "CRM": ("Salesforce",),
                       "SHOP": ("Shopify",), "BABA": ("Alibaba",), "BIDU": ("Baidu",), "PDD": ("Pinduoduo",),
                       "QCOM": ("Qualcomm",), "TSM": ("TSMC",), "ARM": ("Arm Holdings",), "ASML": ("ASML",),
                       "DELL": ("Dell",), "IBM": ("IBM",), "SONY": ("Sony",), "NOK": ("Nokia",)})
GENERIC_COMPANIES = {"target", "on", "global", "world", "block", "gap", "unity", "apple hospitality reit"}


def parse_directory(payload: bytes) -> list[dict]:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), delimiter="|")
    if not reader.fieldnames or "Security Name" not in reader.fieldnames:
        raise ValueError("证券目录格式无效")
    rows = []
    for raw in reader:
        symbol = raw.get("Symbol") or raw.get("ACT Symbol") or ""
        if (not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", symbol) or raw.get("Test Issue") != "N"
                or raw.get("ETF") != "N"):
            continue
        security_name = raw.get("Security Name") or ""
        if re.search(r"preferred|preference|warrants?|\bnotes\b|\bdebentures\b|\bunits\b|\brights\b", security_name, re.I):
            continue
        name = security_name.split(" - ")[0].strip()
        name = re.split(r"\s+(?:Common Stock|Class [A-Z] Common|Ordinary Shares|American Depositary)", name, flags=re.I)[0]
        alias = re.sub(r",?\s+(?:Inc\.?|Corporation|Corp\.?|Ltd\.?|Limited|plc)$", "", name, flags=re.I).strip()
        aliases = [name, *COMMON_ALIASES.get(symbol, ())]
        if len(re.findall(r"[a-z0-9]+", alias, re.I)) >= 2 and alias.casefold() not in GENERIC_COMPANIES:
            aliases.append(alias)
        rows.append({"symbol": symbol, "name": name, "aliases": list(dict.fromkeys(aliases))})
    if not rows:
        raise ValueError("证券目录为空")
    return rows


class SocialDiscoveryCatalog:
    """A broad verified symbol directory, independent of the user's stock pool."""

    def __init__(self, root: Path):
        self.path = root / "data" / "social_discovery.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._next_attempt = 0.0
        self._state = {"items": [], "updated_at": None, "error": None}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS catalog (id INTEGER PRIMARY KEY, payload TEXT)")
            row = db.execute("SELECT payload FROM catalog WHERE id=1").fetchone()
            if row:
                self._state = json.loads(row[0])
        if self._state.get("version") != 2:
            self._state = {"items": [], "updated_at": None, "error": None}
        self._index()

    def _index(self):
        watches, phrases = {}, defaultdict(set)
        for item in self._state["items"]:
            symbol = item["symbol"]
            trusted = {alias.casefold() for alias in COMMON_ALIASES.get(symbol, ())}
            aliases = [alias for alias in item["aliases"] if len(re.findall(r"[a-z0-9]+", alias, re.I)) >= 2 or alias.casefold() in trusted]
            name = item["name"] if item["name"] in aliases else f"{item['name']} ({symbol})"
            watches[symbol] = WatchItem("US", symbol, name, item["name"], tuple(aliases))
            for alias in aliases:
                tokens = tuple(re.findall(r"[a-z0-9]+", alias.casefold()))
                if 1 <= len(tokens) <= 8 and len(alias) >= 4 and alias.casefold() not in GENERIC_COMPANIES:
                    phrases[tokens].add(symbol)
        self._watches, self._phrases = watches, phrases

    def refresh(self):
        with self._lock:
            if time.monotonic() < self._next_attempt:
                return
            from .models import parse_datetime
            updated = self._state.get("updated_at")
            if updated and (utc_now() - parse_datetime(updated)).total_seconds() < 86400:
                return
            self._next_attempt = time.monotonic() + 900
        try:
            rows = {}
            for url in DIRECTORIES:
                payload = news_http.fetch(url, timeout=15, ttl=86400)
                rows.update((item["symbol"], item) for item in parse_directory(payload))
            result = {"version": 2, "items": list(rows.values()), "updated_at": iso_utc(utc_now()), "error": None}
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("INSERT OR REPLACE INTO catalog VALUES (1, ?)", (json.dumps(result),))
            with self._lock:
                self._state = result
                self._index()
        except Exception:
            with self._lock:
                self._state["error"] = "证券目录刷新失败，保留已核验缓存；新代码暂不确认"

    def status(self):
        with self._lock:
            return {"symbol_count": len(self._watches), "updated_at": self._state["updated_at"],
                    "error": self._state.get("error"), "source": "Nasdaq Trader 证券目录",
                    "source_url": "https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs",
                    "scope": "美股上市公司目录 · 公开社区样本；非 A/H/K 全市场覆盖"}

    def candidates(self, texts: list[str], *, limit=200) -> tuple[WatchItem, ...]:
        with self._lock:
            watches, phrases = self._watches, self._phrases
        counts = Counter()
        for text in texts:
            symbols = set(re.findall(r"\$([A-Z]{1,5}(?:\.[A-Z])?)\b", text, flags=re.I))
            explicit_codes = re.findall(r"(?:NASDAQ|NYSE|AMEX):\s*([A-Z]{1,5})\b|\(([A-Z]{2,5})\)|\b([A-Z]{2,5})(?=\s+(?:stock|shares|earnings)\b)", text)
            symbols.update({code for groups in explicit_codes for code in groups if code} - AMBIGUOUS_TICKERS - GENERIC_ASSET_NAMES - {"AI", "API", "CEO", "IPO", "USA", "GDP", "DD"})
            tokens = re.findall(r"[a-z0-9]+", text.casefold())
            for index in range(len(tokens)):
                for size in range(1, min(8, len(tokens) - index) + 1):
                    matched = phrases.get(tuple(tokens[index:index + size]), ())
                    if len(matched) == 1:
                        symbols.update(matched)
            counts.update({symbol.upper() for symbol in symbols if symbol.upper() in watches})
        return tuple(watches[symbol] for symbol, _ in counts.most_common(limit))
