from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Protocol

from .models import NewsItem, WatchItem, parse_datetime, utc_now
from .stock_mentions import matches_stock, SOCIAL_COMPANY_NAMES
from .news_transport import news_http


# Publisher feeds keep article URLs intact; no search redirect or paid API is required.
PUBLIC_POLICY_FEEDS = (
    ("BBC News", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"),
    ("CNBC", "https://www.cnbc.com/id/10000113/device/rss/rss.html"),
)


class NewsSource(Protocol):
    name: str
    is_network: bool

    def collect(self, watchlist: tuple[WatchItem, ...], max_items: int) -> list[NewsItem]: ...


class InboxSource:
    name = "local-inbox"
    is_network = False

    def __init__(self, path: Path):
        self.path = path

    def collect(self, watchlist: tuple[WatchItem, ...], max_items: int) -> list[NewsItem]:
        del watchlist, max_items
        if not self.path.exists():
            return []

        items: list[NewsItem] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    items.append(
                        NewsItem(
                            market=str(raw["market"]).upper(),
                            symbol=str(raw["symbol"]).upper(),
                            title=str(raw["title"]).strip(),
                            summary=str(raw.get("summary", "")).strip(),
                            source=str(raw.get("source", "local-inbox")),
                            url=str(raw.get("url", "")),
                            published_at=parse_datetime(raw.get("published_at")),
                        )
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"{self.path} 第 {line_number} 行格式错误: {exc}") from exc
        return items


class GoogleNewsRssSource:
    name = "google-news-rss"
    is_network = True

    def __init__(self, timeout_seconds: int = 12, *, cache_seconds: int = 15, chinese: bool = False):
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self.chinese = chinese
        if chinese:
            self.name = "google-news-zh-rss"
        self.errors: list[str] = []

    def collect(self, watchlist: tuple[WatchItem, ...], max_items: int) -> list[NewsItem]:
        def collect_watch(watch: WatchItem) -> list[NewsItem]:
            if self.chinese and watch.market not in {"US", "KR"}:
                return []
            locale = _google_locale("CN") if self.chinese else _google_locale(watch.market)
            params = urllib.parse.urlencode({"q": _expanded_watch_query(watch), **locale})
            feed_items = _fetch_rss(
                f"https://news.google.com/rss/search?{params}",
                self.name,
                self.timeout_seconds,
                cache_seconds=self.cache_seconds,
            )
            relevant = _recent_matching_items(feed_items,watch)
            return [_to_news_item(raw, watch) for raw in relevant[:max_items]]

        items, self.errors = _collect_watchlist(watchlist, collect_watch)
        if self.errors and not items:
            raise RuntimeError("；".join(self.errors[:3]))
        return items


class YahooFinanceRssSource:
    name = "yahoo-finance-rss"
    is_network = True

    def __init__(self, timeout_seconds: int = 12, *, cache_seconds: int = 15):
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self.errors: list[str] = []

    def collect(self, watchlist: tuple[WatchItem, ...], max_items: int) -> list[NewsItem]:
        def collect_watch(watch: WatchItem) -> list[NewsItem]:
            if watch.market == "FUND":
                return []
            params = urllib.parse.urlencode(
                {
                    "s": _yahoo_symbol(watch),
                    "region": "US",
                    "lang": "en-US",
                }
            )
            feed_items = _fetch_rss(
                f"https://feeds.finance.yahoo.com/rss/2.0/headline?{params}",
                self.name,
                self.timeout_seconds,
                cache_seconds=self.cache_seconds,
            )
            relevant = _recent_matching_items(feed_items,watch)
            normalized = [_with_url_publisher(raw, self.name) for raw in relevant]
            return [_to_news_item(raw, watch) for raw in normalized[:max_items]]

        items, self.errors = _collect_watchlist(watchlist, collect_watch)
        if self.errors and not items:
            raise RuntimeError("；".join(self.errors[:3]))
        return items


class BingNewsRssSource:
    name = "bing-news-rss"
    is_network = True

    def __init__(self, timeout_seconds: int = 12, *, cache_seconds: int = 15):
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self.errors: list[str] = []

    def collect(self, watchlist: tuple[WatchItem, ...], max_items: int) -> list[NewsItem]:
        def collect_watch(watch: WatchItem) -> list[NewsItem]:
            languages = {"CN": "zh-CN", "HK": "zh-HK", "KR": "ko-KR"}
            params = urllib.parse.urlencode({
                "q": _expanded_watch_query(watch),
                "format": "rss",
                "setlang": languages.get(watch.market, "en-US"),
            })
            feed_items = _fetch_rss(
                f"https://www.bing.com/news/search?{params}",
                self.name,
                self.timeout_seconds,
                cache_seconds=self.cache_seconds,
            )
            relevant = _recent_matching_items(feed_items, watch)
            normalized = [_with_bing_url_publisher(raw, self.name) for raw in relevant]
            return [_to_news_item(raw, watch) for raw in normalized[:max_items]]

        items, self.errors = _collect_watchlist(watchlist, collect_watch)
        if self.errors and not items:
            raise RuntimeError("；".join(self.errors[:3]))
        return items


class CustomRssSource:
    is_network = True

    def __init__(self, name: str, url: str, timeout_seconds: int = 15, *, cache_seconds: int = 15):
        self.name = name
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds

    def collect(self, watchlist: tuple[WatchItem, ...], max_items: int) -> list[NewsItem]:
        raw_items = _fetch_rss(self.url, self.name, self.timeout_seconds, cache_seconds=self.cache_seconds)
        matched: list[NewsItem] = []
        counts: dict[tuple[str, str], int] = {}
        for raw in raw_items:
            haystack = f"{raw.get('title', '')} {raw.get('summary', '')}"
            for watch in watchlist:
                key = (watch.market, watch.symbol)
                if counts.get(key, 0) >= max_items:
                    continue
                if matches_stock(haystack, watch):
                    matched.append(_to_news_item(raw, watch))
                    counts[key] = counts.get(key, 0) + 1
        return matched


def _collect_watchlist(
    watchlist: tuple[WatchItem, ...],
    collector: Callable[[WatchItem], list[NewsItem]],
) -> tuple[list[NewsItem], list[str]]:
    items: list[NewsItem] = []
    errors: list[str] = []
    worker_count = min(8, max(1, len(watchlist)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(collector, watch): watch for watch in watchlist}
        for future in as_completed(futures):
            watch = futures[future]
            try:
                items.extend(future.result())
            except Exception as exc:
                errors.append(f"{watch.symbol}: {type(exc).__name__}: {exc}")
    return items, errors


def _google_locale(market: str) -> dict[str, str]:
    if market == "US":
        return {"hl": "en-US", "gl": "US", "ceid": "US:en"}
    if market == "HK":
        return {"hl": "zh-HK", "gl": "HK", "ceid": "HK:zh-Hant"}
    if market == "KR":
        return {"hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    return {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}


def _yahoo_symbol(watch: WatchItem) -> str:
    symbol = watch.symbol.upper()
    if watch.market == "US":
        return symbol.replace(".", "-")
    if watch.market == "HK":
        return symbol if symbol.endswith(".HK") else f"{symbol}.HK"
    if watch.market == "KR":
        return symbol if symbol.endswith((".KS", ".KQ")) else f"{symbol}.KS"
    if symbol.endswith((".SS", ".SZ")):
        return symbol
    suffix = "SS" if symbol.startswith(("6", "9")) else "SZ"
    return f"{symbol}.{suffix}"


def _matches_watch(raw: dict[str, str], watch: WatchItem) -> bool:
    haystack = f"{raw.get('title', '')} {raw.get('summary', '')}"
    return matches_stock(haystack, watch)


def _expanded_watch_query(watch: WatchItem) -> str:
    names = list(dict.fromkeys(name.strip().replace('"','') for name in (watch.name,*SOCIAL_COMPANY_NAMES.get((watch.market,watch.symbol),()),*watch.aliases,watch.symbol) if name.strip()))[:8]
    alternatives = " OR ".join(f'"{name}"' for name in names)
    return f"({watch.query}) OR ({alternatives})" if watch.query else alternatives


def _recent_matching_items(rows: list[dict[str,str]], watch: WatchItem) -> list[dict[str,str]]:
    now = utc_now()
    return sorted((row for row in rows if _matches_watch(row,watch) and _parse_feed_datetime(row.get("published_at"))<=now),
                  key=lambda row:_parse_feed_datetime(row.get("published_at")),reverse=True)


def _with_url_publisher(raw: dict[str, str], fallback: str) -> dict[str, str]:
    if raw.get("source") != fallback:
        return raw
    hostname = urllib.parse.urlsplit(raw.get("url", "")).hostname or fallback
    hostname = hostname.removeprefix("www.")
    publisher = "Yahoo Finance" if hostname == "finance.yahoo.com" else hostname
    return {**raw, "source": publisher}


def _with_bing_url_publisher(raw: dict[str, str], fallback: str) -> dict[str, str]:
    url = raw.get("url", "")
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname in {"bing.com", "www.bing.com"}:
        target = urllib.parse.parse_qs(parsed.query).get("url", [""])[0]
        target_parsed = urllib.parse.urlsplit(target)
        if target_parsed.scheme in {"http", "https"} and target_parsed.hostname:
            url = target
    hostname = (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.")
    source = raw.get("source") or fallback
    if source == fallback and hostname:
        source = "Yahoo Finance" if hostname == "finance.yahoo.com" else hostname
    return {**raw, "url": url, "source": source}


def _to_news_item(raw: dict[str, str], watch: WatchItem) -> NewsItem:
    return NewsItem(
        market=watch.market,
        symbol=watch.symbol,
        target_name=watch.name,
        target_aliases=watch.aliases,
        title=raw.get("title", "").strip(),
        summary=raw.get("summary", "").strip(),
        source=raw.get("source", "rss").strip(),
        url=raw.get("url", "").strip(),
        published_at=_parse_feed_datetime(raw.get("published_at")),
    )


def _fetch_rss(url: str, source_name: str, timeout_seconds: int, *, cache_seconds: int = 1) -> list[dict[str, str]]:
    payload = news_http.fetch(url,timeout=timeout_seconds,ttl=max(1, cache_seconds))

    root = ET.fromstring(payload)
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    return [_parse_entry(entry, source_name) for entry in entries]


def _parse_entry(entry: ET.Element, source_name: str) -> dict[str, str]:
    title = _first_text(entry, {"title"})
    summary = _first_text(entry, {"description", "summary", "content"})
    published = _first_text(entry, {"pubDate", "published", "updated", "date"})
    source = _first_text(entry, {"source"}) or source_name
    link = ""
    for child in entry:
        if _local_name(child.tag) == "link":
            link = child.attrib.get("href") or (child.text or "")
            if link:
                break
    return {
        "title": _clean_html(title),
        "summary": _clean_html(summary),
        "published_at": published,
        "source": _clean_html(source),
        "url": html.unescape(link.strip()),
    }


def _first_text(entry: ET.Element, names: set[str]) -> str:
    for child in entry:
        if _local_name(child.tag) in names and child.text:
            return child.text
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_html(value: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def _parse_feed_datetime(value: str | None) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return parse_datetime(value, default=datetime(1970, 1, 1, tzinfo=timezone.utc))
