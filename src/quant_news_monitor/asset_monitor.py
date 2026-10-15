from __future__ import annotations

import copy
import json
import math
import re
import statistics
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .analyzers import Analyzer, select_analyzer
from .config import load_config
from .market_scanner import (
    _child_text,
    _direct_news_url,
    _feed_datetime,
    _looks_like_article,
    _source_from_url,
)
from .models import NewsItem, iso_utc, news_event_key, utc_now
from .sources import _fetch_rss, _parse_feed_datetime
from .system_notifier import WindowsSystemNotifier
from .research_ledger import ResearchLedger, RecordingAnalyzer
from .news_transport import news_http
from .startup_cache import SnapshotStore


PRICE_INTERVAL_SECONDS = 1
OIL_PRICE_INTERVAL_SECONDS = 60
SLOW_ASSET_INTERVALS = {"OIL": OIL_PRICE_INTERVAL_SECONDS, "US5Y": 60}
NEWS_INTERVAL_SECONDS = 15
HISTORY_INTERVAL_SECONDS = 5 * 60
NEWS_LOOKBACK_HOURS = 72
POLICY_NEWS_LOOKBACK_HOURS = 14 * 24
MAX_LIVE_GOLD_AGE_SECONDS = 180
MAX_NEWS_PER_ASSET = 30
ASSET_ANALYSIS_VERSION = 6


@dataclass(frozen=True)
class PriceBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class AssetMarketData:
    key: str
    symbol: str
    name: str
    subtitle: str
    price: float
    currency: str
    price_time: datetime
    change_24h: float
    high_24h: float
    low_24h: float
    volume_24h: float
    source: str
    source_url: str
    delayed: bool
    bars: tuple[PriceBar, ...]
    contract_name: str = ""
    session_start: int | None = None
    session_end: int | None = None
    previous_close: float | None = None


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capturing = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.casefold() == "script" and attributes.get("id") == "__NEXT_DATA__":
            self._capturing = True

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capturing:
            self._capturing = False

    @property
    def payload(self) -> str:
        return "".join(self._parts).strip()


ASSET_DEFINITIONS = {
    "GOLD": {
        "symbol": "XAU-USD",
        "name": "黄金",
        "subtitle": "XAU / USD 现货",
        "terms": ("gold", "xau", "bullion", "黄金", "金价", "黃金", "金價"),
        "queries": (
            ('黄金 OR 金价 OR XAU', "zh-cn", NEWS_LOOKBACK_HOURS),
            ('"gold price" OR bullion OR XAU', "en-us", NEWS_LOOKBACK_HOURS),
            ('泰国 黄金 (进口税 OR 黄金税 OR 关税)', "zh-cn", POLICY_NEWS_LOOKBACK_HOURS),
            ('Thailand gold (tax OR "import duty" OR tariff)', "en-us", POLICY_NEWS_LOOKBACK_HOURS),
        ),
    },
    "BTC": {
        "symbol": "BTC-USD",
        "name": "Bitcoin",
        "subtitle": "BTC / USD",
        "terms": ("bitcoin", "btc", "比特币", "比特幣"),
        "queries": (
            ('比特币 OR 比特幣 OR BTC', "zh-cn", NEWS_LOOKBACK_HOURS),
            ('Bitcoin OR BTC', "en-us", NEWS_LOOKBACK_HOURS),
        ),
    },
    "OIL": {
        "symbol": "CL=F",
        "name": "WTI 原油",
        "subtitle": "WTI 近月期货 / 美元每桶",
        "terms": ("crude oil", "oil price", "oil prices", "wti", "brent", "opec", "petroleum", "原油", "油价", "油價", "石油"),
        "queries": (
            ('原油 OR 油价 OR OPEC OR 原油库存', "zh-cn", NEWS_LOOKBACK_HOURS),
            ('"crude oil" OR "WTI" OR "Brent" OR "OPEC"', "en-us", NEWS_LOOKBACK_HOURS),
        ),
    },
    "US5Y": {
        "symbol": "^FVX",
        "name": "美债（五年期）",
        "subtitle": "5年期美债收益率 / 年化 %",
        "terms": ("5-year", "five-year", "5 year", "treasury", "treasuries", "bond yields", "5年期", "五年期", "美债", "美債", "美国国债", "美國公債"),
        "queries": (
            ('"5-year Treasury" OR "five-year Treasury" OR "Treasury yields"', "en-us", NEWS_LOOKBACK_HOURS),
            ('五年期美债 OR 5年期美债 OR 美国国债收益率', "zh-cn", NEWS_LOOKBACK_HOURS),
        ),
    },
}

ASSET_ANOMALY_RULES: dict[str, dict[str, float]] = {
    "GOLD": {
        "5m": 0.35,
        "15m": 0.60,
        "24h": 1.50,
        "volatility_floor": 0.25,
        "volatility_multiple": 3.0,
    },
    "BTC": {
        "5m": 0.80,
        "15m": 1.25,
        "24h": 4.00,
        "volatility_floor": 0.55,
        "volatility_multiple": 3.0,
    },
}

ASSET_QUOTE_JUMP_RULES: dict[str, dict[str, float]] = {
    "GOLD": {"fast": 0.08, "minute": 0.18},
    "BTC": {"fast": 0.20, "minute": 0.45},
}
ASSET_ALERT_COOLDOWN_SECONDS = 15 * 60


DIRECT_NEWS_FEEDS: dict[str, tuple[tuple[str, str, str, float], ...]] = {
    "US5Y": (
        ("Yahoo Finance RSS", "", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EFVX&region=US&lang=en-US", 0.92),
        ("FXStreet 新闻", "FXStreet", "https://www.fxstreet.com/rss/news", 1.00),
        ("FXStreet 分析", "FXStreet", "https://www.fxstreet.com/rss/analysis", 0.92),
    ),
    "OIL": (
        ("Yahoo Finance RSS", "", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=CL%3DF&region=US&lang=en-US", 0.92),
        ("EIA Today in Energy", "EIA", "https://www.eia.gov/rss/todayinenergy.xml", 1.10),
        ("FXStreet 新闻", "FXStreet", "https://www.fxstreet.com/rss/news", 1.00),
    ),
    "GOLD": (
        (
            "Yahoo Finance RSS",
            "",
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC%3DF&region=US&lang=en-US",
            0.92,
        ),
        ("FXStreet 新闻", "FXStreet", "https://www.fxstreet.com/rss/news", 1.00),
        ("FXStreet 分析", "FXStreet", "https://www.fxstreet.com/rss/analysis", 0.92),
        ("Gold-Eagle", "Gold-Eagle", "https://www.gold-eagle.com/rss.xml", 0.82),
    ),
    "BTC": (
        (
            "Yahoo Finance RSS",
            "",
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US",
            0.92,
        ),
        ("CoinDesk RSS", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", 1.00),
        ("Cointelegraph RSS", "Cointelegraph", "https://cointelegraph.com/rss", 0.92),
        ("Decrypt RSS", "Decrypt", "https://decrypt.co/feed", 0.92),
        (
            "Bitcoin Magazine RSS",
            "Bitcoin Magazine",
            "https://bitcoinmagazine.com/.rss/full/",
            0.88,
        ),
        ("Blockworks RSS", "Blockworks", "https://blockworks.co/feed", 0.95),
        ("FXStreet 新闻", "FXStreet", "https://www.fxstreet.com/rss/news", 1.00),
    ),
}


EVENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tax_policy", ("gold tax", "import tax", "import duty", "capital gains tax", "tariff", "tax", "duty", "黄金税", "进口税", "关税", "征税", "黃金稅", "進口稅", "關稅", "徵稅", "ภาษี")),
    ("macro", ("federal reserve", "interest rate", "inflation", "yield", "dollar", "美联储", "利率", "通胀", "收益率", "美元")),
    ("fund_flows", ("etf", "inflow", "outflow", "central bank", "institutional", "央行", "资金流入", "资金流出", "机构")),
    ("regulation", ("regulation", "sec", "ban", "crackdown", "approval", "监管", "禁令", "打击", "获批")),
    ("market_structure", ("liquidation", "options", "leverage", "technical", "support", "resistance", "爆仓", "清算", "杠杆", "技术面", "支撑", "阻力")),
    ("geopolitics", ("war", "conflict", "sanction", "geopolitical", "战争", "冲突", "制裁", "地缘")),
    ("supply_demand", ("demand", "supply", "mining", "purchase", "reserve", "opec", "inventories", "inventory", "production", "需求", "供应", "矿业", "购买", "储备", "库存", "庫存", "减产", "增产")),
)

GENERAL_POSITIVE = (
    "rally", "surge", "jumps", "climbs", "record high", "breakout", "inflow",
    "approval", "adoption", "buying", "demand rises", "rate cut", "weaker dollar",
    "上涨", "大涨", "走高", "新高", "突破", "流入", "获批", "增持", "购买",
    "降息", "美元走弱", "需求上升",
)
GENERAL_NEGATIVE = (
    "falls", "drops", "slumps", "selloff", "outflow", "liquidation", "hack", "ban",
    "crackdown", "rate hike", "stronger dollar", "yield rises", "下跌", "大跌", "走低",
    "抛售", "流出", "爆仓", "黑客", "禁令", "打击", "加息", "美元走强", "收益率上升",
)


class CrossAssetMonitor:
    def __init__(
        self,
        config_path: str | Path,
        *,
        price_interval_seconds: int = PRICE_INTERVAL_SECONDS,
        news_interval_seconds: int = NEWS_INTERVAL_SECONDS,
        timeout_seconds: int = 15,
        system_notifications_enabled: bool = True,
        system_notifier: WindowsSystemNotifier | None = None,
        ledger: ResearchLedger | None = None,
        snapshot_store: SnapshotStore | None = None,
    ):
        self.config_path = Path(config_path).resolve()
        self.ledger = ledger
        self.price_interval_seconds = max(1, price_interval_seconds)
        self.news_interval_seconds = max(1, news_interval_seconds)
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._news_thread: threading.Thread | None = None
        self._slow_threads: dict[str, threading.Thread] = {}
        self._slow_refreshing: set[str] = set()
        self._next_slow_at = {key: 0.0 for key in SLOW_ASSET_INTERVALS}
        self._slow_errors: dict[str, list[str]] = {key: [] for key in SLOW_ASSET_INTERVALS}
        self._price_refreshing = False
        self._news_refreshing = False
        self._force_prices = True
        self._force_news = True
        self._next_price_at = 0.0
        self._next_news_at = 0.0
        self._next_history_at = 0.0
        self._market_data: dict[str, AssetMarketData] = {}
        self._news: list[dict[str, Any]] = []
        self._probabilities: list[dict[str, Any]] = []
        self._probability_context: dict[str, Any] = {}
        self._last_updated_at: str | None = None
        self._news_updated_at: str | None = None
        self._price_errors: list[str] = []
        self._news_errors: list[str] = []
        self._analyzer_message = "等待首次分析"
        self._system_notifier = system_notifier or WindowsSystemNotifier()
        self._system_notifications_enabled = bool(system_notifications_enabled)
        self._system_notification_cooldowns: dict[str, tuple[float, float]] = {}
        self._last_system_notification_at: str | None = None
        self._system_notification_error: str | None = None
        self._snapshot_store = snapshot_store
        self._next_persist_at = 0.0
        self._restored_keys: set[str] = set()
        self._restore_snapshot()

    def _restore_snapshot(self) -> None:
        saved = self._snapshot_store.load("assets") if self._snapshot_store else None
        if not saved:
            return
        markets = saved.get("markets") or {}
        if not isinstance(markets, dict):
            return
        for key, raw in markets.items():
            if key not in ASSET_DEFINITIONS or not isinstance(raw, dict):
                continue
            try:
                fields = dict(raw)
                fields["price_time"] = datetime.fromisoformat(fields["price_time"])
                fields["bars"] = tuple(PriceBar(**bar) for bar in fields["bars"])
                market = AssetMarketData(**fields)
                numbers = [market.price, market.change_24h, market.high_24h, market.low_24h, market.volume_24h]
                numbers.extend(value for bar in market.bars for value in (bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume))
                if market.key != key or market.price_time.tzinfo is None or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in numbers) or market.price <= 0:
                    continue
                self._market_data[key] = market
                self._restored_keys.add(key)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        news = saved.get("news")
        if isinstance(news, list) and all(isinstance(item, dict) for item in news):
            self._news = news[:MAX_NEWS_PER_ASSET * len(ASSET_DEFINITIONS)]
        self._last_updated_at = saved.get("last_updated_at")
        self._news_updated_at = saved.get("news_updated_at")

    def _persist_snapshot(self, *, force: bool = False) -> None:
        if self._snapshot_store is None:
            return
        with self._lock:
            if not force and time.monotonic() < self._next_persist_at:
                return
            self._next_persist_at = time.monotonic() + self._snapshot_store.interval_seconds
            payload = {
                "markets": {key: asdict(data) for key, data in self._market_data.items()},
                "news": copy.deepcopy(self._news),
                "last_updated_at": self._last_updated_at,
                "news_updated_at": self._news_updated_at,
            }
        self._snapshot_store.save("assets", payload, force=force)

    def set_news_interval(self, seconds: int) -> None:
        with self._lock:
            self.news_interval_seconds = max(1,min(300,int(seconds)))
            self._next_news_at = min(self._next_news_at,time.monotonic()+self.news_interval_seconds)
        self._wake_event.set()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop,
                name="cross-asset-monitor",
                daemon=True,
            )
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self._persist_snapshot(force=True)

    def trigger_refresh(self, *, include_news: bool = True) -> bool:
        with self._lock:
            self._force_prices = True
            for key in self._next_slow_at:
                self._next_slow_at[key] = 0.0
            if include_news:
                self._force_news = True
            already_refreshing = self._price_refreshing or self._news_refreshing
        self._wake_event.set()
        return not already_refreshing

    def set_system_notifications_enabled(self, enabled: bool) -> bool:
        with self._lock:
            self._system_notifications_enabled = bool(enabled)
            if not enabled:
                self._system_notification_cooldowns.clear()
            return self._system_notifications_enabled

    def test_system_notification(self) -> tuple[bool, str | None]:
        sent, error = self._system_notifier.notify(
            "黄金 / BTC 系统提醒测试",
            "STock 已启用 Windows 系统弹窗；正式提醒只监测黄金和 Bitcoin。",
        )
        with self._lock:
            self._system_notification_error = error
            if sent:
                self._last_system_notification_at = iso_utc(utc_now())
        return sent, error

    def snapshot(self, *, for_research: bool = False) -> dict[str, Any]:
        with self._lock:
            assets = [_serialize_market_data(value) for value in self._market_data.values()]
            for asset in assets:
                if asset["key"] in self._restored_keys:
                    asset.update(quote_status="stale", quote_note="上次快照，等待新报价确认", restored_from_cache=True)
                    asset["anomaly"] = {"triggered": False}
            assets.sort(key=lambda item: list(ASSET_DEFINITIONS).index(item["key"]))
            news_channels = sorted(
                {
                    str(channel)
                    for item in self._news
                    for channel in (item.get("channels") or [item.get("source")])
                    if channel
                }
            )
            result = {
                "generated_at": iso_utc(utc_now()),
                "assets": assets,
                "probabilities": copy.deepcopy(self._probabilities),
                "news": copy.deepcopy(self._news),
                "runtime": {
                    "refresh_in_progress": bool(self._price_refreshing or self._news_refreshing or self._slow_refreshing),
                    "last_updated_at": self._last_updated_at,
                    "restored_from_cache": bool(self._restored_keys),
                    "restored_assets": sorted(self._restored_keys),
                    "news_updated_at": self._news_updated_at,
                    "price_interval_seconds": self.price_interval_seconds,
                    "oil_price_interval_seconds": OIL_PRICE_INTERVAL_SECONDS,
                    "treasury_price_interval_seconds": SLOW_ASSET_INTERVALS["US5Y"],
                    "slow_refresh_in_progress": sorted(self._slow_refreshing),
                    "news_interval_seconds": self.news_interval_seconds,
                    "errors": [*self._price_errors, *(error for errors in self._slow_errors.values() for error in errors), *self._news_errors],
                    "slow_errors": copy.deepcopy(self._slow_errors),
                    "analyzer": self._analyzer_message,
                    "news_channel_count": len(news_channels),
                    "news_channels": news_channels,
                    "news_story_count": len(self._news),
                    "news_max_per_asset": MAX_NEWS_PER_ASSET,
                    "search_interval_seconds": 60,
                    "system_notifications_enabled": self._system_notifications_enabled,
                    "system_notifications_available": self._system_notifier.available,
                    "system_notification_last_sent_at": self._last_system_notification_at,
                    "system_notification_error": self._system_notification_error,
                    "probability_method": "多因子启发式信号；新闻采用跨渠道去重、时效与来源质量加权；黄金技术走势使用 COMEX 分钟线校准到 XAU/USD 即时现货；原油与美债为延迟行情，不生成15分钟预测；美债方向指收益率，通常与债价相反；尚未经过历史概率校准",
                },
            }
            if for_research:
                result["research_inputs"] = copy.deepcopy(self._probability_context)
                if self._restored_keys:
                    for key in self._restored_keys:
                        result["research_inputs"].get("markets", {}).pop(key, None)
                        result["research_inputs"].get("bars", {}).pop(key, None)
                    result["research_inputs"]["base_signals"] = [row for row in result["research_inputs"].get("base_signals", []) if row.get("asset") not in self._restored_keys]
            # Quotes can expire while a refresh is blocked; recheck on every read.
            for row in result["probabilities"]:
                data = self._market_data.get(row["asset"])
                minutes = {"15m": 15, "1h": 60, "24h": 1440}.get(row["horizon"], 15)
                reason = "上次快照，等待新报价确认" if row["asset"] in self._restored_keys else _analysis_unavailable(data, minutes)
                if reason:
                    row.update({key: None for key in ("up_probability", "down_probability", "confidence", "probability_change")})
                    row.update(available=False, signal="待确认", drivers=[reason])
            return result

    def _capture_probability_inputs(self) -> None:
        self._probability_context = {
            "markets":{key:_serialize_market_data(data) for key,data in self._market_data.items()},
            "bars":{key:[[b.timestamp,b.open,b.high,b.low,b.close,b.volume] for b in data.bars] for key,data in self._market_data.items()},
            "news":copy.deepcopy(self._news), "base_signals":copy.deepcopy(self._probabilities),
        }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self._refresh_once(background_news=True)
            except Exception as exc:
                with self._lock:
                    self._price_errors = [f"刷新失败: {type(exc).__name__}: {exc}"]
                    self._price_refreshing = False
            with self._lock:
                scheduled = [
                    due_at
                    for due_at in (self._next_price_at, self._next_news_at)
                    if due_at > 0
                ]
            if scheduled:
                wait_seconds = max(0.05, min(scheduled) - time.monotonic())
            else:
                elapsed = time.monotonic() - started
                wait_seconds = max(
                    0.05,
                    min(self.price_interval_seconds, self.news_interval_seconds) - elapsed,
                )
            self._wake_event.wait(wait_seconds)
            self._wake_event.clear()

    def _refresh_once(self, *, background_news: bool = False) -> None:
        # Independent workers keep delayed feeds from blocking fast gold/BTC quotes.
        self._start_slow_refreshes_if_due()
        with self._lock:
            if self._price_refreshing:
                return
            self._price_refreshing = True
            cycle_started = time.monotonic()
            refresh_prices = (
                self._force_prices
                or cycle_started >= self._next_price_at
                or not self._market_data
            )
            refresh_news = not self._news_refreshing and (
                self._force_news
                or cycle_started >= self._next_news_at
                or (not self._news and self._next_news_at <= 0)
            )
            refresh_history = (
                refresh_prices
                and (cycle_started >= self._next_history_at or not self._market_data)
            )
            previous_market_data = dict(self._market_data)
            self._force_prices = False
            if refresh_news:
                self._force_news = False

        price_errors: list[str] = []
        news_errors: list[str] = []
        refreshed: dict[str, AssetMarketData] = {}
        if refresh_prices:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {
                    executor.submit(
                        _fetch_gold_market_data,
                        self.timeout_seconds,
                        previous_market_data.get("GOLD"),
                        refresh_history,
                    ): "GOLD",
                    executor.submit(
                        _fetch_btc_market_data,
                        self.timeout_seconds,
                        previous_market_data.get("BTC"),
                        refresh_history,
                    ): "BTC",
                }
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        refreshed[key] = future.result()
                    except Exception as exc:
                        price_errors.append(f"{key} 行情: {type(exc).__name__}: {exc}")

        news = None
        analyzer_message = None
        if refresh_news and background_news:
            with self._lock:
                cached_news = copy.deepcopy(self._news)
                self._news_refreshing = True
                self._next_news_at = max(
                    cycle_started + self.news_interval_seconds,
                    time.monotonic() + 0.1,
                )
                self._news_thread = threading.Thread(
                    target=self._refresh_news_background,
                    args=(cached_news,),
                    name="cross-asset-news",
                    daemon=True,
                )
                self._news_thread.start()
        elif refresh_news:
            try:
                with self._lock:
                    cached_news = copy.deepcopy(self._news)
                config = load_config(self.config_path)
                analyzer, analyzer_message = select_analyzer(
                    config.analysis.mode,
                    config.analysis.model,
                    watchlist=config.watchlist,
                )
                news, collected_errors = _collect_and_analyze_news(
                    RecordingAnalyzer(analyzer,self.ledger) if self.ledger else analyzer,
                    self.timeout_seconds,
                    cached_news,
                )
                news_errors.extend(f"关联新闻: {message}" for message in collected_errors)
            except Exception as exc:
                news_errors.append(f"关联新闻: {type(exc).__name__}: {exc}")

        with self._lock:
            self._market_data.update(refreshed)
            first_live_keys = self._restored_keys.intersection(refreshed)
            self._restored_keys.difference_update(refreshed)
            cycle_finished = time.monotonic()
            if refresh_prices:
                self._next_price_at = max(
                    cycle_started + self.price_interval_seconds,
                    cycle_finished + 0.05,
                )
                self._price_errors = price_errors
                if refresh_history and {"GOLD", "BTC"}.issubset(refreshed):
                    self._next_history_at = cycle_started + HISTORY_INTERVAL_SECONDS
            if news is not None:
                self._news = news
                self._news_updated_at = iso_utc(utc_now())
            if refresh_news:
                if not background_news:
                    self._next_news_at = max(
                        cycle_started + self.news_interval_seconds,
                        cycle_finished + 0.1,
                    )
                    self._news_errors = news_errors
            if analyzer_message:
                self._analyzer_message = analyzer_message
            previous = {
                (str(item["asset"]), str(item["horizon"])): float(item["up_probability"])
                for item in self._probabilities
                if item.get("up_probability") is not None
            }
            try:
                self._probabilities = _build_probabilities(
                    self._market_data,
                    self._news,
                    previous,
                )
                self._capture_probability_inputs()
            except Exception as exc:
                price_errors.append(f"概率计算: {type(exc).__name__}: {exc}")
                self._price_errors = price_errors
            if refreshed:
                self._last_updated_at = iso_utc(utc_now())
            self._price_refreshing = False

        if refreshed:
            self._emit_system_alerts(previous_market_data, {key: value for key, value in refreshed.items() if key not in first_live_keys})
        self._persist_snapshot()

    def _start_slow_refreshes_if_due(self) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            for key, fetcher in (("OIL", _fetch_oil_market_data), ("US5Y", _fetch_treasury_market_data)):
                if key in self._slow_refreshing or time.monotonic() < self._next_slow_at[key]:
                    continue
                self._slow_refreshing.add(key)
                self._next_slow_at[key] = time.monotonic() + SLOW_ASSET_INTERVALS[key]
                worker = threading.Thread(target=self._refresh_slow_background, args=(key, fetcher), name=f"{key.lower()}-quotes", daemon=True)
                self._slow_threads[key] = worker
                worker.start()

    def _refresh_slow_background(self, key: str, fetcher: Any) -> None:
        errors = []
        try:
            market = fetcher(self.timeout_seconds)
            with self._lock:
                if self._stop_event.is_set():
                    return
                self._market_data[key] = market
                self._restored_keys.discard(key)
                previous = {(row["asset"], row["horizon"]): row["up_probability"]
                            for row in self._probabilities if row.get("up_probability") is not None}
                self._probabilities = _build_probabilities(self._market_data, self._news, previous)
                self._capture_probability_inputs()
                self._last_updated_at = iso_utc(utc_now())
        except Exception as exc:
            errors.append(f"{key} 行情: {type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._slow_errors[key] = errors
                self._slow_refreshing.discard(key)
                self._next_slow_at[key] = time.monotonic() + SLOW_ASSET_INTERVALS[key]
            self._persist_snapshot()

    def _emit_system_alerts(
        self,
        previous_market_data: dict[str, AssetMarketData],
        refreshed: dict[str, AssetMarketData],
    ) -> None:
        with self._lock:
            if not self._system_notifications_enabled or not self._system_notifier.available:
                return

        now = time.monotonic()
        for key, current in refreshed.items():
            if key not in ASSET_QUOTE_JUMP_RULES:
                continue
            candidate = _asset_alert_candidate(current, previous_market_data.get(key))
            if candidate is None:
                continue
            cooldown_key = f"{key}:{candidate['direction']}"
            with self._lock:
                last = self._system_notification_cooldowns.get(cooldown_key)
                if (
                    last
                    and now - last[0] < ASSET_ALERT_COOLDOWN_SECONDS
                    and float(candidate["severity"]) < last[1] * 1.5
                ):
                    continue

            asset_name = "黄金" if key == "GOLD" else "BTC"
            title = f"{asset_name}{candidate['label']}"
            body = (
                f"{candidate['horizon_label']} {float(candidate['return_pct']):+.2f}% | "
                f"{current.price:,.2f} {current.currency}。{candidate['reason']}"
            )
            sent, error = self._system_notifier.notify(title, body)
            with self._lock:
                self._system_notification_error = error
                if sent:
                    self._system_notification_cooldowns[cooldown_key] = (
                        now,
                        float(candidate["severity"]),
                    )
                    self._last_system_notification_at = iso_utc(utc_now())

    def _refresh_news_background(self, cached_news: list[dict[str, Any]]) -> None:
        news = None
        analyzer_message = None
        errors: list[str] = []
        try:
            config = load_config(self.config_path)
            analyzer, analyzer_message = select_analyzer(
                config.analysis.mode,
                config.analysis.model,
                watchlist=config.watchlist,
            )
            news, collected_errors = _collect_and_analyze_news(
                RecordingAnalyzer(analyzer,self.ledger) if self.ledger else analyzer,
                self.timeout_seconds,
                cached_news,
            )
            errors.extend(f"关联新闻: {message}" for message in collected_errors)
        except Exception as exc:
            errors.append(f"关联新闻: {type(exc).__name__}: {exc}")

        with self._lock:
            try:
                if news is not None:
                    self._news = news
                    self._news_updated_at = iso_utc(utc_now())
                if analyzer_message:
                    self._analyzer_message = analyzer_message
                previous = {
                    (str(item["asset"]), str(item["horizon"])): float(item["up_probability"])
                    for item in self._probabilities
                    if item.get("up_probability") is not None
                }
                self._probabilities = _build_probabilities(
                    self._market_data,
                    self._news,
                    previous,
                )
                self._capture_probability_inputs()
            except Exception as exc:
                errors.append(f"概率计算: {type(exc).__name__}: {exc}")
            finally:
                self._news_errors = errors
                self._news_refreshing = False
                self._news_thread = None
        self._persist_snapshot()


def _fetch_btc_market_data(
    timeout_seconds: int,
    previous: AssetMarketData | None = None,
    refresh_history: bool = True,
) -> AssetMarketData:
    headers = {
        "User-Agent": "QuantNewsMonitor/0.1 (+local research tool)",
        "Cache-Control": "no-cache",
        "Accept": "application/json",
    }
    ticker = _read_json(
        "https://api.exchange.coinbase.com/products/BTC-USD/ticker",
        headers,
        timeout_seconds,
    )
    if not isinstance(ticker, dict):
        raise ValueError("Coinbase 即时报价格式异常")
    price = float(ticker["price"])
    price_time = _parse_iso_datetime(str(ticker.get("time", "")), strict=True)
    if not math.isfinite(price) or price <= 0 or not -60 <= (utc_now() - price_time).total_seconds() <= MAX_LIVE_GOLD_AGE_SECONDS:
        raise ValueError("BTC 报价无效或已过期")
    if previous is not None and not refresh_history:
        bars = _merge_live_price(previous.bars, price, price_time)
        return _market_data_from_bars(
            key="BTC",
            symbol="BTC-USD",
            name="Bitcoin",
            subtitle="BTC / USD",
            price=price,
            currency="USD",
            price_time=price_time,
            bars=bars,
            source="Coinbase Exchange",
            source_url="https://www.coinbase.com/advanced-trade/spot/BTC-USD",
            delayed=False,
        )

    candles = _read_json(
        "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=300",
        headers,
        timeout_seconds,
    )
    if not isinstance(candles, list):
        raise ValueError("Coinbase 返回格式异常")
    bars = sorted(
        (
            PriceBar(
                timestamp=int(row[0]),
                low=float(row[1]),
                high=float(row[2]),
                open=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in candles
            if isinstance(row, list) and len(row) >= 6
        ),
        key=lambda bar: bar.timestamp,
    )
    if len(bars) < 20:
        raise ValueError("BTC 分钟线不足")
    bars = _merge_live_price(bars, price, price_time)
    return _market_data_from_bars(
        key="BTC",
        symbol="BTC-USD",
        name="Bitcoin",
        subtitle="BTC / USD",
        price=price,
        currency="USD",
        price_time=price_time,
        bars=bars,
        source="Coinbase Exchange",
        source_url="https://www.coinbase.com/advanced-trade/spot/BTC-USD",
        delayed=False,
    )


def _fetch_gold_market_data(
    timeout_seconds: int,
    previous: AssetMarketData | None = None,
    refresh_history: bool = True,
) -> AssetMarketData:
    headers = {
        "User-Agent": "QuantNewsMonitor/0.1 (+local research tool)",
        "Cache-Control": "no-cache",
        "Accept": "application/json",
    }
    live_quote = _read_json(
        "https://api.gold-api.com/price/XAU",
        headers,
        timeout_seconds,
    )
    if not isinstance(live_quote, dict):
        raise ValueError("黄金即时现货返回格式异常")
    try:
        price = float(live_quote["price"])
        price_time = _parse_iso_datetime(str(live_quote["updatedAt"]), strict=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("黄金即时现货缺少有效报价") from exc
    quote_age = (utc_now() - price_time).total_seconds()
    if not math.isfinite(price) or price <= 0 or quote_age < -60 or quote_age > MAX_LIVE_GOLD_AGE_SECONDS:
        raise ValueError("黄金即时现货报价已过期")

    if previous is not None and not refresh_history:
        bars = _merge_live_price(previous.bars, price, price_time)
        return _market_data_from_bars(
            key="GOLD",
            symbol="XAU-USD",
            name="黄金",
            subtitle="XAU / USD 现货",
            price=price,
            currency=str(live_quote.get("currency") or "USD"),
            price_time=price_time,
            bars=bars,
            source="Gold API 即时现货",
            source_url="https://gold-api.com/",
            delayed=False,
        )

    # Gold API 提供即时现货，COMEX 分钟线只用于补足技术走势的历史形状。
    symbol = urllib.parse.quote("GC=F", safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=5d"
    parsed = _read_json(url, {"User-Agent": "Mozilla/5.0"}, timeout_seconds)
    try:
        result = parsed["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("黄金行情返回格式异常") from exc
    bars: list[PriceBar] = []
    for index, timestamp in enumerate(timestamps):
        values = (
            quote["open"][index],
            quote["high"][index],
            quote["low"][index],
            quote["close"][index],
        )
        if any(value is None for value in values):
            continue
        bars.append(
            PriceBar(
                timestamp=int(timestamp),
                open=float(values[0]),
                high=float(values[1]),
                low=float(values[2]),
                close=float(values[3]),
                volume=float(quote.get("volume", [0] * len(timestamps))[index] or 0.0),
            )
        )
    if len(bars) < 20:
        raise ValueError("黄金分钟线不足")
    if bars[-1].close <= 0:
        raise ValueError("黄金分钟线价格异常")
    scale = price / bars[-1].close
    bars = [
        PriceBar(
            timestamp=bar.timestamp,
            open=bar.open * scale,
            high=bar.high * scale,
            low=bar.low * scale,
            close=bar.close * scale,
            volume=bar.volume,
        )
        for bar in bars
    ]
    bars = _merge_live_price(bars, price, price_time)
    return _market_data_from_bars(
        key="GOLD",
        symbol="XAU-USD",
        name="黄金",
        subtitle="XAU / USD 现货",
        price=price,
        currency=str(live_quote.get("currency") or "USD"),
        price_time=price_time,
        bars=bars,
        source="Gold API 即时现货",
        source_url="https://gold-api.com/",
        delayed=False,
    )


def _fetch_oil_market_data(timeout_seconds: int) -> AssetMarketData:
    return _fetch_delayed_market_data("OIL", timeout_seconds)


def _fetch_treasury_market_data(timeout_seconds: int) -> AssetMarketData:
    return _fetch_delayed_market_data("US5Y", timeout_seconds)


def _fetch_delayed_market_data(key: str, timeout_seconds: int) -> AssetMarketData:
    definition = ASSET_DEFINITIONS[key]
    symbol = urllib.parse.quote(definition["symbol"], safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=5d"
    payload = _read_json(url, {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout_seconds)
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        expected_type = "INDEX" if key == "US5Y" else "FUTURE"
        if meta["symbol"] != definition["symbol"] or meta["instrumentType"] != expected_type or meta["currency"] != "USD":
            raise ValueError("标的或计价单位不符")
        price = float(meta["regularMarketPrice"])
        price_time = datetime.fromtimestamp(int(meta["regularMarketTime"]), timezone.utc)
        timestamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{definition['name']}缺少有效报价和时间") from exc
    if not math.isfinite(price) or price <= 0 or price_time > utc_now() + timedelta(seconds=60):
        raise ValueError(f"{definition['name']}报价或报价时间异常")

    by_time: dict[int, PriceBar] = {}
    for index, timestamp in enumerate(timestamps):
        try:
            stamp = int(timestamp)
            values = [float(quote[field][index]) for field in ("open", "high", "low", "close")]
            volumes = quote.get("volume") or []
            volume = float(volumes[index] or 0) if index < len(volumes) else 0.0
            if stamp <= 0 or stamp > price_time.timestamp() or not all(math.isfinite(v) and v > 0 for v in values):
                continue
            if values[1] < max(values[0], values[3]) or values[2] > min(values[0], values[3]):
                continue
            if not math.isfinite(volume) or volume < 0:
                volume = 0.0
            # Yahoo may append a last-trade row within the final 5-minute bucket.
            bucket = stamp // 300 * 300
            prior = by_time.get(bucket)
            by_time[bucket] = PriceBar(bucket, prior.open if prior else values[0],
                                      max(prior.high, values[1]) if prior else values[1],
                                      min(prior.low, values[2]) if prior else values[2],
                                      values[3], max(prior.volume, volume) if prior else volume)
        except (IndexError, KeyError, TypeError, ValueError, OverflowError):
            continue
    bars = sorted(by_time.values(), key=lambda bar: bar.timestamp)
    if not bars:
        raise ValueError(f"{definition['name']}分钟线暂不可用")
    market = _market_data_from_bars(
        key=key, symbol=definition["symbol"], name=definition["name"], subtitle=definition["subtitle"],
        price=price, currency="%" if key == "US5Y" else "USD", price_time=price_time, bars=bars,
        source="Yahoo Finance / Cboe 收益率指标（延迟）" if key == "US5Y" else "Yahoo Finance / NYMEX（延迟）",
        source_url=f"https://finance.yahoo.com/quote/{symbol}/", delayed=True,
    )
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    try:
        session_start, session_end = int(period["start"]), int(period["end"])
        if session_end <= session_start:
            session_start = session_end = None
    except (KeyError, TypeError, ValueError, OverflowError):
        session_start = session_end = None
    try:
        previous_close = float(meta["previousClose"])
        if not math.isfinite(previous_close) or previous_close <= 0:
            previous_close = None
    except (KeyError, TypeError, ValueError):
        previous_close = None
    return replace(market, contract_name=str(meta.get("shortName") or definition["name"]),
                   session_start=session_start, session_end=session_end, previous_close=previous_close)


def _delayed_quote_status(data: AssetMarketData) -> str:
    age = (utc_now() - data.price_time).total_seconds()
    now = utc_now().timestamp()
    if age < -60:
        return "stale"
    if data.session_start is not None and data.session_end is not None:
        if not data.session_start <= now < data.session_end:
            return "closed"
    if age > 1800:
        return "stale"
    return "delayed"


def _delayed_analysis_unavailable(data: AssetMarketData | None, minutes: int) -> str:
    if data is None:
        return "行情尚未就绪"
    status = _delayed_quote_status(data)
    if status == "closed":
        return "休市，仅展示上一有效报价，暂不生成当前预测"
    if status == "stale":
        return "报价已过期，等待新行情"
    if data.session_start is None or data.session_end is None:
        return "交易时段未确认，暂不生成当前预测"
    if minutes <= 15 and data.delayed:
        return "延迟行情不支持15分钟预测"
    if len(data.bars) < 21 or data.price_time.timestamp() - data.bars[-1].timestamp > 600:
        return "分钟线样本不足或已过期"
    target = int(data.price_time.timestamp()) - minutes * 60
    reference = _bar_at_or_before(data.bars, target)
    if reference is None or target - reference.timestamp > 600:
        return "该周期存在行情缺口，暂不生成研究信号"
    return ""


def _merge_live_price(
    bars: list[PriceBar] | tuple[PriceBar, ...],
    price: float,
    price_time: datetime,
) -> list[PriceBar]:
    merged = list(bars)
    bucket = int(price_time.timestamp()) // 300 * 300
    if not merged:
        return [PriceBar(bucket, price, price, price, price, 0.0)]

    latest = merged[-1]
    if latest.timestamp > bucket and latest.timestamp - bucket < 300:
        latest = PriceBar(
            timestamp=bucket,
            open=latest.open,
            high=latest.high,
            low=latest.low,
            close=latest.close,
            volume=latest.volume,
        )
        merged[-1] = latest
    if latest.timestamp == bucket:
        merged[-1] = PriceBar(
            timestamp=bucket,
            open=latest.open,
            high=max(latest.high, price),
            low=min(latest.low, price),
            close=price,
            volume=latest.volume,
        )
    elif latest.timestamp < bucket:
        merged.append(PriceBar(bucket, price, price, price, price, 0.0))
    else:
        merged[-1] = PriceBar(
            timestamp=latest.timestamp,
            open=latest.open,
            high=max(latest.high, price),
            low=min(latest.low, price),
            close=price,
            volume=latest.volume,
        )
    return merged[-300:]


def _market_data_from_bars(
    *,
    key: str,
    symbol: str,
    name: str,
    subtitle: str,
    price: float,
    currency: str,
    price_time: datetime,
    bars: list[PriceBar],
    source: str,
    source_url: str,
    delayed: bool,
) -> AssetMarketData:
    latest_timestamp = bars[-1].timestamp
    cutoff = latest_timestamp - 24 * 3600
    recent = [bar for bar in bars if bar.timestamp >= cutoff]
    if not recent:
        recent = bars[-1:]
    reference = _bar_at_or_before(bars, cutoff) or bars[0]
    change_24h = _percent_change(price, reference.close)
    return AssetMarketData(
        key=key,
        symbol=symbol,
        name=name,
        subtitle=subtitle,
        price=price,
        currency=currency,
        price_time=price_time,
        change_24h=change_24h,
        high_24h=max(bar.high for bar in recent),
        low_24h=min(bar.low for bar in recent),
        volume_24h=sum(bar.volume for bar in recent),
        source=source,
        source_url=source_url,
        delayed=delayed,
        bars=tuple(bars[-300:]),
    )


def _collect_and_analyze_news(
    analyzer: Analyzer,
    timeout_seconds: int,
    cached_news: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    cached_news = cached_news or []
    analyzer_name = str(getattr(analyzer, "analysis_id", analyzer.name))
    cached_by_key = {
        (str(item.get("asset", "")), str(item.get("url", ""))): item
        for item in cached_news
        if item.get("asset") and item.get("url")
    }
    raw_by_asset: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    failed_assets: set[str] = set()
    warnings_by_asset = {key:[] for key in ASSET_DEFINITIONS}
    with ThreadPoolExecutor(max_workers=len(ASSET_DEFINITIONS)) as executor:
        futures = {
            executor.submit(_fetch_asset_news, key, timeout_seconds,warnings=warnings_by_asset[key]): key
            for key in ASSET_DEFINITIONS
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                raw_by_asset[key] = future.result()
            except Exception as exc:
                failed_assets.add(key)
                errors.append(f"{key}: {type(exc).__name__}: {exc}")
    for warnings in warnings_by_asset.values(): errors.extend(warnings)
    if errors and not raw_by_asset:
        raise RuntimeError("；".join(errors))

    analyzed: list[dict[str, Any]] = []
    for asset, articles in raw_by_asset.items():
        for article in articles[:MAX_NEWS_PER_ASSET]:
            item = NewsItem(
                market="GLOBAL",
                symbol=asset,
                target_name=ASSET_DEFINITIONS[asset]["name"],
                target_aliases=ASSET_DEFINITIONS[asset]["terms"],
                title=str(article["title"]),
                summary=str(article["summary"]),
                source=str(article["source"]),
                url=str(article["url"]),
                published_at=article["published_at"],
            )
            cached = cached_by_key.get((asset, item.url))
            version = analyzer.ledger.observe_news(item) if isinstance(analyzer,RecordingAnalyzer) else None
            if (
                cached is not None
                and str(cached.get("title", "")) == item.title
                and str(cached.get("raw_summary", "")) == item.summary
                and str(cached.get("published_at", "")) == iso_utc(item.published_at)
                and str(cached.get("analyzer", "")) == analyzer_name
                and cached.get("relevance_reason","直接提及")==article.get("relevance_reason","直接提及")
                and int(cached.get("analysis_version", 0)) == ASSET_ANALYSIS_VERSION
            ):
                cached_item = copy.deepcopy(cached)
                if version:
                    cached_item["input_version_id"] = version["id"]
                for key in (
                    "channels",
                    "channel_count",
                    "source_count",
                    "source_weight",
                    "relevance_weight",
                    "relevance_reason",
                ):
                    if article.get(key) is not None:
                        cached_item[key] = copy.deepcopy(article[key])
                analyzed.append(cached_item)
                continue
            try:
                analysis = analyzer.analyze(item)
            except Exception as exc:
                errors.append(f"{asset} 新闻分析失败：{type(exc).__name__}")
                continue
            text = str(analysis.metadata.get("evidence_text", f"{item.title} {item.summary}")).casefold()
            lexicon_direction = _news_direction(text, asset)
            model_score = float(analysis.score)
            impact_score = _clamp(
                model_score * 0.65 + lexicon_direction * 0.35,
                -1.0,
                1.0,
            )
            if abs(model_score) < 0.03 and lexicon_direction:
                impact_score = lexicon_direction * 0.55
            if asset == "OIL" and analysis.analyzer.startswith("rules"):
                # Inventory falls and output cuts support oil prices, unlike generic "falls".
                impact_score = lexicon_direction * 0.55
            if asset == "US5Y":
                # Yield headlines need an explicit bond-price direction, not stock sentiment.
                impact_score = lexicon_direction * 0.55
            indirect = article.get("relevance_reason")=="宏观间接关联"
            probability_eligible = not indirect or not analysis.analyzer.startswith("rules")
            if asset == "US5Y":
                probability_eligible = not indirect
            if indirect:
                impact_score = model_score if probability_eligible else 0.0
            if analysis.metadata.get("direction_eligible") is False:
                impact_score, probability_eligible = 0.0, False
            elif analysis.metadata.get("allow_direction_fallback") is False:
                impact_score = model_score
            category = _asset_event_category(text)
            tone = "偏多" if impact_score > 0.08 else "偏空" if impact_score < -0.08 else "中性"
            summary = (
                analysis.summary
                if not analysis.analyzer.startswith("rules")
                else f"识别为{_asset_event_label(category)}，对{ASSET_DEFINITIONS[asset]['name']}的即时影响{tone}；需结合原文确认。"
            )
            if not probability_eligible:
                summary = f"宏观观察项；对{ASSET_DEFINITIONS[asset]['name']}的传导方向待确认，暂不计入方向因子。"
            elif asset == "OIL" and analysis.analyzer.startswith("rules"):
                summary = _oil_news_reason(text, impact_score)
            elif asset == "US5Y":
                summary = ("收益率下行通常利多债券价格" if impact_score > .08 else
                           "收益率上行通常利空债券价格" if impact_score < -.08 else
                           "收益率与债券价格方向待确认") + "；此处情绪针对债价，不代表持有到期回报。"
            analyzed.append(
                {
                    "asset": asset,
                    "title": item.title,
                    "summary": summary,
                    "raw_summary":item.summary,
                    "input_version_id":version["id"] if version else None,
                    "source": item.source,
                    "url": item.url,
                    "published_at": iso_utc(item.published_at),
                    "event_type": category,
                    "impact_score": round(impact_score, 4),
                    "probability_eligible":probability_eligible,
                    "direction": "bullish" if impact_score > 0.08 else "bearish" if impact_score < -0.08 else "neutral",
                    "confidence": round(float(analysis.confidence), 4),
                    "analyzer": analysis.analyzer,
                    "analysis_version": ASSET_ANALYSIS_VERSION,
                    "model_metadata":analysis.metadata,
                    "event_key":news_event_key("GLOBAL", asset, item.title, item.summary, iso_utc(item.published_at)),
                    "channels": list(article.get("channels") or []),
                    "channel_count": int(article.get("channel_count", 1)),
                    "source_count": int(article.get("source_count", 1)),
                    "source_weight": round(float(article.get("source_weight", 0.9)), 4),
                    "relevance_weight": round(float(article.get("relevance_weight", 1.0)), 4),
                    "relevance_reason":article.get("relevance_reason","直接提及"),
                    "direction_basis": "债券价格" if asset == "US5Y" else "标的价格",
                }
            )
    for asset in failed_assets:
        analyzed.extend(
            copy.deepcopy(item)
            for item in cached_news
            if item.get("asset") == asset
        )
    analyzed.sort(key=lambda item: str(item["published_at"]), reverse=True)
    return analyzed, errors


def _fetch_asset_news(asset: str, timeout_seconds: int, *, warnings: list[str] | None = None) -> list[dict[str, Any]]:
    definition = ASSET_DEFINITIONS[asset]
    articles: list[dict[str, Any]] = []
    source_errors: list[Exception] = []
    jobs: list[tuple[str, Any, tuple[Any, ...]]] = [
        (
            "Bing News",
            _fetch_bing_asset_news,
            (query, language, int(lookback_hours), timeout_seconds),
        )
        for query, language, lookback_hours in definition["queries"]
    ]
    jobs.extend(
        (feed[0], _fetch_direct_feed_news, (asset, feed, timeout_seconds))
        for feed in DIRECT_NEWS_FEEDS.get(asset, ())
    )
    if asset == "GOLD":
        jobs.append(("The Nation", _fetch_nation_gold_policy_news, (timeout_seconds,)))

    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as executor:
        futures = {
            executor.submit(function, *arguments): label
            for label, function, arguments in jobs
        }
        for future in as_completed(futures):
            try:
                articles.extend(future.result())
            except Exception as exc:
                source_errors.append(RuntimeError(f"{futures[future]}: {exc}"))
    if warnings is not None:
        warnings.extend(f"{asset} / {error}" for error in source_errors)
    terms = definition["terms"]
    now = utc_now()
    articles = [item for item in articles if item["published_at"] <= now and
                (_contains_asset_term(f"{item['title']} {item.get('summary','')}",terms) or item.get("relevance_reason")=="宏观间接关联")]
    if asset == "US5Y":
        for item in articles:
            if not re.search(r"\b(?:5[ -]year|five[ -]year)\b|5年期|五年期", f"{item['title']} {item.get('summary', '')}", re.I):
                item.update(relevance_reason="宏观间接关联", relevance_weight=0.55)
    if not articles and source_errors:
        raise RuntimeError("新闻服务未返回数据") from source_errors[-1]
    deduplicated = _deduplicate_asset_articles(articles)
    return _select_asset_articles(deduplicated, MAX_NEWS_PER_ASSET)


def _fetch_bing_asset_news(
    query: str,
    language: str,
    lookback_hours: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"q": query, "format": "rss", "setlang": language})
    raw = news_http.fetch(f"https://www.bing.com/news/search?{params}",timeout=timeout_seconds,
                         ttl=300 if lookback_hours>NEWS_LOOKBACK_HOURS else 60,headers={"User-Agent":"Mozilla/5.0"})

    root = ET.fromstring(raw)
    cutoff = utc_now() - timedelta(hours=lookback_hours)
    priority = 1 if lookback_hours > NEWS_LOOKBACK_HOURS else 0
    articles: list[dict[str, Any]] = []
    for entry in root.findall(".//item")[:60]:
        title = _child_text(entry, "title")
        summary = _strip_html(_child_text(entry, "description"))
        published_at = _feed_datetime(_child_text(entry, "pubdate"))
        url = _clean_article_url(_direct_news_url(_child_text(entry, "link")))
        if not title or published_at < cutoff or not url or not _looks_like_article(url, title):
            continue
        source = _child_text(entry, "source") or _source_from_url(url)
        articles.append(
            {
                "title": title,
                "summary": summary,
                "source": source,
                "url": url,
                "published_at": published_at,
                "channel": "Bing News",
                "source_weight": _publisher_weight(url, 0.90),
                "relevance_weight": 1.0,
                "priority": priority,
            }
        )
    return articles


def _fetch_direct_feed_news(
    asset: str,
    feed: tuple[str, str, str, float],
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    channel, fixed_publisher, url, base_weight = feed
    rows = _fetch_rss(url, channel, timeout_seconds)
    cutoff = utc_now() - timedelta(hours=NEWS_LOOKBACK_HOURS)
    terms = ASSET_DEFINITIONS[asset]["terms"]
    articles: list[dict[str, Any]] = []
    for row in rows[:80]:
        title = str(row.get("title") or "").strip()
        summary = str(row.get("summary") or "").strip()
        title_relevant = _contains_asset_term(title, terms)
        summary_relevant = _contains_asset_term(summary, terms)
        macro_relevant = channel in {"FXStreet 新闻","FXStreet 分析"} and _sensitive_macro_event(title)
        if not title or not (title_relevant or summary_relevant or macro_relevant):
            continue
        published_at = _parse_feed_datetime(str(row.get("published_at") or ""))
        article_url = _clean_article_url(_direct_news_url(str(row.get("url") or "")))
        if published_at < cutoff or published_at>utc_now() or not article_url:
            continue
        raw_source = str(row.get("source") or "").strip()
        source = fixed_publisher or (
            raw_source
            if raw_source and raw_source != channel
            else _source_from_url(article_url)
        )
        relevance_weight = 1.0 if title_relevant else 0.72 if summary_relevant else 0.55
        if asset == "GOLD" and _looks_like_gold_company_news(title):
            relevance_weight = min(relevance_weight, 0.62)
        articles.append(
            {
                "title": title,
                "summary": summary,
                "source": source,
                "url": article_url,
                "published_at": published_at,
                "channel": channel,
                "source_weight": _publisher_weight(article_url, base_weight),
                "relevance_weight": relevance_weight,
                "relevance_reason": "直接提及" if title_relevant or summary_relevant else "宏观间接关联",
                "priority": 0,
            }
        )
    return articles


def _sensitive_macro_event(title: str) -> bool:
    return bool(re.search(r"\b(?:FOMC|Federal Reserve|Fed|Powell|US CPI|US PCE|nonfarm payrolls|Treasury yields|US Dollar Index)\b|美联储|美聯儲|非农|非農|美国通胀|美國通脹|美国国债收益率",title,re.I))


def _contains_asset_term(text: str, terms: tuple[str, ...]) -> bool:
    folded = text.casefold()
    for term in terms:
        normalized = term.casefold()
        if normalized.isascii() and normalized.replace("-", "").isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", folded):
                return True
        elif normalized in folded:
            return True
    return False


def _looks_like_gold_company_news(title: str) -> bool:
    folded = title.casefold()
    company_terms = (
        "minerals", "resources", "mining company", "gold mine", "gold project",
        "drill", "intercept", "deposit", "exploration", "矿业公司", "金矿项目", "勘探",
    )
    market_terms = (
        "gold price", "xau", "bullion", "dollar", "yield", "federal reserve", "金价", "黄金价格",
    )
    return any(term in folded for term in company_terms) and not any(
        term in folded for term in market_terms
    )


def _clean_article_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    tracking = {"oc", ".tsrc", "ref", "ref_src", "source", "campaign"}
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in tracking
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), "")
    )


def _publisher_weight(url: str, base_weight: float) -> float:
    hostname = (urllib.parse.urlsplit(url).hostname or "").casefold().removeprefix("www.")
    if hostname.endswith(("federalreserve.gov", "sec.gov", "cftc.gov", "gold.org")):
        return 1.15
    if hostname.endswith(("reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com")):
        return max(base_weight, 1.05)
    return base_weight


def _deduplicate_asset_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for raw in sorted(articles, key=lambda item: item["published_at"], reverse=True):
        article = copy.deepcopy(raw)
        channel = str(article.get("channel") or article.get("source") or "新闻来源")
        source = str(article.get("source") or "新闻来源")
        article["channels"] = sorted(set(article.get("channels") or [channel]))
        article["sources"] = sorted(set(article.get("sources") or [source]))
        match = next((item for item in grouped if _same_news_story(item, article)), None)
        if match is None:
            article["channel_count"] = len(article["channels"])
            article["source_count"] = len(article["sources"])
            grouped.append(article)
            continue

        channels = sorted(set(match["channels"]) | set(article["channels"]))
        sources = sorted(set(match["sources"]) | set(article["sources"]))
        source_weight = max(
            float(match.get("source_weight", 0.9)),
            float(article.get("source_weight", 0.9)),
        )
        relevance_weight = max(
            float(match.get("relevance_weight", 1.0)),
            float(article.get("relevance_weight", 1.0)),
        )
        priority = max(int(match.get("priority", 0)), int(article.get("priority", 0)))
        if article["published_at"]>=match["published_at"] and _article_quality(article) > _article_quality(match):
            replacement = article
            match.clear()
            match.update(replacement)
        match["channels"] = channels
        match["sources"] = sources
        match["channel_count"] = len(channels)
        match["source_count"] = len(sources)
        match["source_weight"] = source_weight
        match["relevance_weight"] = relevance_weight
        match["priority"] = priority
    return sorted(grouped, key=lambda item: item["published_at"], reverse=True)


def _same_news_story(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_url = _canonical_url_key(str(first.get("url", "")))
    second_url = _canonical_url_key(str(second.get("url", "")))
    if first_url and first_url == second_url:
        return True
    if abs((first["published_at"]-second["published_at"]).total_seconds())>6*3600:
        return False
    if set(re.findall(r"\d+(?:[.,]\d+)*",str(first.get("title","")))) != set(re.findall(r"\d+(?:[.,]\d+)*",str(second.get("title","")))):
        return False
    if _headline_actions(str(first.get("title",""))) != _headline_actions(str(second.get("title",""))):
        return False
    first_title = _normalized_story_title(str(first.get("title", "")))
    second_title = _normalized_story_title(str(second.get("title", "")))
    if not first_title or not second_title:
        return False
    if SequenceMatcher(None, first_title, second_title).ratio() >= 0.88:
        return True
    first_tokens = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", first_title))
    second_tokens = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", second_title))
    union = first_tokens | second_tokens
    return len(union) >= 5 and len(first_tokens & second_tokens) / len(union) >= 0.76


def _headline_actions(title: str) -> tuple[bool,bool]:
    return (bool(re.search(r"\b(?:rise[sd]?|rising|rall(?:y|ies)|surges?|gains?|jumps?|climbs?)\b|上涨|上漲|走高|走强",title,re.I)),
            bool(re.search(r"\b(?:falls?|drops?|slides?|slumps?|tumbles?)\b|下跌|走低|走弱",title,re.I)))


def _canonical_url_key(url: str) -> str:
    cleaned = _clean_article_url(url)
    parsed = urllib.parse.urlsplit(cleaned)
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query,keep_blank_values=True)))
    return f"{(parsed.hostname or '').casefold().removeprefix('www.')}{parsed.path.rstrip('/')}" + (f"?{query}" if query else "")


def _normalized_story_title(title: str) -> str:
    folded = title.casefold()
    folded = re.sub(r"\s+[-|]\s+[^-|]{2,50}$", "", folded)
    return re.sub(r"\W+", "", folded)


def _article_quality(article: dict[str, Any]) -> tuple[float, float, int]:
    return (
        float(article.get("source_weight") or 0.9),
        float(article.get("relevance_weight") or 1.0),
        len(str(article.get("summary", ""))),
    )


def _select_asset_articles(
    articles: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    priority = sorted(
        (item for item in articles if int(item.get("priority", 0)) > 0),
        key=lambda item: item["published_at"],
        reverse=True,
    )[:min(2,limit)]
    macro = sorted((item for item in articles if item.get("relevance_reason")=="宏观间接关联" and
                    item["published_at"]>=utc_now()-timedelta(hours=24)),key=lambda item:item["published_at"],reverse=True)[:min(4,max(0,limit-len(priority)))]
    priority.extend(item for item in macro if item not in priority)
    selected_ids = {id(item) for item in priority}
    regular = [item for item in articles if id(item) not in selected_ids]
    high_relevance = sorted(
        (item for item in regular if float(item.get("relevance_weight", 1.0)) >= 0.9),
        key=lambda item: (item["published_at"], float(item.get("source_weight", 0.9))),
        reverse=True,
    )
    lower_relevance = sorted(
        (item for item in regular if float(item.get("relevance_weight", 1.0)) < 0.9),
        key=lambda item: (item["published_at"], float(item.get("relevance_weight", 1.0))),
        reverse=True,
    )

    selected = list(priority)
    selected_channels = {
        channel
        for item in selected
        for channel in item.get("channels", [])
    }
    diversity_limit = min(6, limit)
    for item in high_relevance:
        channels = set(item.get("channels", []))
        if len(selected) >= diversity_limit or channels & selected_channels:
            continue
        selected.append(item)
        selected_ids.add(id(item))
        selected_channels.update(channels)

    for item in high_relevance + lower_relevance:
        if len(selected) >= limit:
            break
        if id(item) in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(id(item))
    return sorted(selected, key=lambda item: item["published_at"], reverse=True)


def _fetch_nation_gold_policy_news(timeout_seconds: int) -> list[dict[str, Any]]:
    search_url = "https://www.nationthailand.com/search?q=gold%20tax"
    raw = news_http.fetch(search_url,timeout=timeout_seconds,ttl=300,headers={"User-Agent":"Mozilla/5.0"})
    return _parse_nation_gold_policy_page(raw.decode("utf-8", errors="replace"))


def _parse_nation_gold_policy_page(page: str) -> list[dict[str, Any]]:
    parser = _NextDataParser()
    parser.feed(page)
    if not parser.payload:
        raise ValueError("The Nation 搜索页缺少结构化数据")
    payload = json.loads(parser.payload)
    try:
        rows = payload["props"]["pageProps"]["articles"]
    except (KeyError, TypeError) as exc:
        raise ValueError("The Nation 搜索数据格式异常") from exc
    if not isinstance(rows, list):
        raise ValueError("The Nation 搜索结果格式异常")

    cutoff = utc_now() - timedelta(hours=POLICY_NEWS_LOOKBACK_HOURS)
    articles: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        summary = str(row.get("blurb") or "").strip()
        tags = " ".join(str(tag) for tag in row.get("tags", []) if tag)
        searchable = f"{title} {summary} {tags}".casefold()
        published_raw = str(row.get("published_at") or "").strip()
        link = str(row.get("link") or "").strip()
        if (
            not title
            or not published_raw
            or not link
            or "gold" not in searchable
            or not any(term in searchable for term in ("tax", "duty", "tariff"))
        ):
            continue
        published_at = _parse_iso_datetime(published_raw)
        if published_at < cutoff:
            continue
        articles.append(
            {
                "title": title,
                "summary": summary,
                "source": "The Nation Thailand",
                "url": urllib.parse.urljoin("https://www.nationthailand.com", link),
                "published_at": published_at,
                "channel": "The Nation 搜索",
                "source_weight": 1.0,
                "relevance_weight": 1.0,
                "priority": 1,
            }
        )
    return articles


def _live_analysis_unavailable(data: AssetMarketData) -> str:
    age = (utc_now() - data.price_time).total_seconds()
    if not math.isfinite(data.price) or data.price <= 0 or age < -60:
        return "报价价格或时间异常，暂停预测与提醒"
    if age > MAX_LIVE_GOLD_AGE_SECONDS:
        return "报价超过3分钟未更新，暂停预测与提醒"
    return ""


def _analysis_unavailable(data: AssetMarketData | None, minutes: int) -> str:
    if data is None:
        return "暂无有效行情"
    return _delayed_analysis_unavailable(data, minutes) if data.key in SLOW_ASSET_INTERVALS else _live_analysis_unavailable(data)


def _build_probabilities(
    market_data: dict[str, AssetMarketData],
    news: list[dict[str, Any]],
    previous: dict[tuple[str, str], float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    horizons = (
        ("15m", "15分钟", 15, 0.62, 0.23, 0.15),
        ("1h", "1小时", 60, 0.52, 0.25, 0.23),
        ("24h", "24小时", 1440, 0.40, 0.25, 0.35),
    )
    for asset in ASSET_DEFINITIONS:
        data = market_data.get(asset)
        if data is None:
            continue
        closes = [bar.close for bar in data.bars]
        news_score, news_count = _aggregate_news(asset, news)
        if asset == "US5Y":
            news_score = -news_score
        trend = _trend_signal(closes)
        rsi = _rsi(closes)
        rsi_signal = _clamp((rsi - 50.0) / 35.0, -1.0, 1.0)
        volatility = _realized_volatility(closes)
        for horizon, horizon_label, minutes, momentum_weight, trend_weight, news_weight in horizons:
            unavailable_reason = _analysis_unavailable(data, minutes)
            if unavailable_reason:
                rows.append({
                    "asset": asset, "asset_name": data.name, "symbol": data.symbol,
                    "horizon": horizon, "horizon_label": horizon_label,
                    "up_probability": None, "down_probability": None, "confidence": None,
                    "probability_change": None, "signal": "待确认", "available": False,
                    "drivers": [unavailable_reason], "updated_at": iso_utc(data.price_time),
                })
                continue
            return_pct = _return_for_minutes(data.bars, minutes, data.price)
            if asset == "US5Y":
                reference = _bar_at_or_before(data.bars, int(data.price_time.timestamp()) - minutes * 60)
                return_pct = (data.price - reference.close) * 100
            scale = _momentum_scale(asset, horizon)
            momentum = math.tanh(return_pct / scale)
            technical = trend * 0.72 + rsi_signal * 0.28
            raw_signal = (
                momentum * momentum_weight
                + technical * trend_weight
                + news_score * news_weight
            )
            up_probability = round(50.0 + 38.0 * math.tanh(raw_signal * 1.2), 1)
            up_probability = _clamp(up_probability, 12.0, 88.0)
            down_probability = round(100.0 - up_probability, 1)
            edge = abs(up_probability - 50.0) * 2.0
            coverage = min(1.0, news_count / 6.0)
            confidence = round(min(88.0, 38.0 + edge * 0.42 + coverage * 12.0), 1)
            signal = "偏多" if up_probability >= 58 else "偏空" if up_probability <= 42 else "震荡"
            if asset == "US5Y":
                signal = "上行" if up_probability >= 58 else "下行" if up_probability <= 42 else "震荡"
            key = (asset, horizon)
            previous_up = previous.get(key)
            probability_change = round(up_probability - previous_up, 1) if previous_up is not None else 0.0
            rows.append(
                {
                    "asset": asset,
                    "asset_name": data.name,
                    "symbol": data.symbol,
                    "horizon": horizon,
                    "horizon_label": horizon_label,
                    "up_probability": up_probability,
                    "down_probability": down_probability,
                    "probability_change": probability_change,
                    "signal": signal,
                    "confidence": confidence,
                    "price_return": round(return_pct, 4),
                    "news_score": round(news_score, 4),
                    "news_count": news_count,
                    "rsi": round(rsi, 1),
                    "volatility": round(volatility, 4),
                    "drivers": _probability_drivers(return_pct, trend, news_score, news_count) if asset != "US5Y" else
                               [f"收益率{return_pct:+.2f}bp", "消息指向收益率上行" if news_score > .08 else "消息指向收益率下行" if news_score < -.08 else "消息方向待确认"],
                    "updated_at": iso_utc(data.price_time),
                    "available": True,
                    "basis_note": "基于延迟收益率指标；收益率上行通常对应债价下行" if asset == "US5Y" else "基于延迟期货行情，非实时胜率" if asset == "OIL" else "",
                    "quote_time": iso_utc(data.price_time),
                }
            )
    for row in rows:
        row["score_kind"] = "uncalibrated_research_signal"
    return rows


def build_asset_outlooks(
    assets: list[dict[str, Any]],
    news: list[dict[str, Any]],
    probabilities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    now = utc_now()
    cutoff = now - timedelta(hours=24)
    assets_by_key = {str(item.get("key")): item for item in assets}
    probabilities_by_asset = {
        str(item.get("asset")): item
        for item in probabilities
        if str(item.get("horizon")) == "24h"
    }
    outlooks: list[dict[str, Any]] = []

    for asset in ASSET_DEFINITIONS:
        market = assets_by_key.get(asset) or {}
        probability = probabilities_by_asset.get(asset) or {}
        if asset not in SLOW_ASSET_INTERVALS and (not market or not probability):
            continue
        if asset in SLOW_ASSET_INTERVALS and not market and not any(item.get("asset") == asset for item in news):
            continue

        recent_news = []
        for item in news:
            if item.get("asset") != asset:
                continue
            published_at = _parse_iso_datetime(str(item.get("published_at", "")))
            if cutoff <= published_at <= now + timedelta(minutes=5):
                recent_news.append((item, published_at))

        bullish_count = sum(1 for item, _ in recent_news if item.get("direction") == "bullish")
        bearish_count = sum(1 for item, _ in recent_news if item.get("direction") == "bearish")
        observation_count = sum(1 for item,_ in recent_news if item.get("probability_eligible") is False)
        neutral_count = len(recent_news) - bullish_count - bearish_count - observation_count
        channels = {
            str(channel)
            for item, _ in recent_news
            for channel in (item.get("channels") or [item.get("source")])
            if channel
        }

        weighted_score = 0.0
        total_weight = 0.0
        theme_weights: dict[str, float] = {}
        ranked_events: list[tuple[float, dict[str, Any]]] = []
        for item, published_at in recent_news:
            age_hours = max(0.0, (now - published_at).total_seconds() / 3600.0)
            source_weight = _clamp(float(item.get("source_weight") or 0.9), 0.6, 1.2)
            relevance_weight = _clamp(float(item.get("relevance_weight") or 1.0), 0.5, 1.0)
            source_count = max(1, int(item.get("source_count") or 1))
            corroboration = 1.0 + min(0.18, (source_count - 1) * 0.06)
            recency = math.exp(-age_hours / 18.0)
            weight = recency * source_weight * relevance_weight * corroboration
            impact = float(item.get("impact_score") or 0.0)
            if item.get("probability_eligible") is not False:
                weighted_score += impact * weight
                total_weight += weight
            event_type = str(item.get("event_type") or "market_news")
            theme_weights[event_type] = theme_weights.get(event_type, 0.0) + weight * (0.35 + abs(impact))
            ranked_events.append((weight * (0.45 + abs(impact)), item))

        news_score = weighted_score / total_weight if total_weight else 0.0
        tone = "偏多" if news_score > 0.08 else "偏空" if news_score < -0.08 else "中性"
        themes = [
            _asset_event_label(event_type)
            for event_type, _ in sorted(
                theme_weights.items(),
                key=lambda pair: pair[1],
                reverse=True,
            )[:2]
        ]
        top_events = [
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "source": str(item.get("source") or "新闻来源"),
                "direction": str(item.get("direction") or "neutral"),
                "event_type": str(item.get("event_type") or "market_news"),
            }
            for _, item in sorted(ranked_events, key=lambda pair: pair[0], reverse=True)[:3]
        ]

        asset_name = ASSET_DEFINITIONS[asset]["name"]
        if recent_news:
            theme_copy = f"，焦点集中在{'、'.join(themes)}" if themes else ""
            summary_text = (
                f"过去24小时共收录 {len(recent_news)} 条有效消息，"
                f"利多 {bullish_count} 条、利空 {bearish_count} 条、中性 {neutral_count} 条、待确认 {observation_count} 条；"
                f"综合消息面{tone}{theme_copy}。"
            )
        else:
            summary_text = f"过去24小时暂未收录新的可验证{asset_name}消息，当前判断主要依赖价格与较早事件。"

        up_probability = float(probability.get("up_probability") or 50.0)
        down_probability = float(probability.get("down_probability") or (100.0 - up_probability))
        confidence = float(probability.get("confidence") or 0.0)
        volatility = max(0.0, float(probability.get("volatility") or 0.0))
        expected_move = volatility * math.sqrt(288.0)
        floor, ceiling = {"GOLD": (0.45, 4.0), "BTC": (1.5, 12.0), "OIL": (1.0, 10.0), "US5Y": (0.25, 5.0)}[asset]
        expected_move = round(_clamp(expected_move, floor, ceiling), 2)
        price = float(market.get("price") or 0.0)
        expected_low = round(price * (1.0 - expected_move / 100.0), 4)
        expected_high = round(price * (1.0 + expected_move / 100.0), 4)
        signal = str(probability.get("signal") or "震荡")
        drivers = [str(item) for item in probability.get("drivers") or []]
        driver_copy = "、".join(drivers[:3]) if drivers else "价格与新闻信号暂未形成明显共振"
        forecast_text = (
            f"未来24小时研究方向{signal}：多向信号 {up_probability:.1f} 点，"
            f"空向信号 {down_probability:.1f} 点，信号强度 {confidence:.0f} 点（未经概率校准）。"
            f"启发式波动估计约 ±{expected_move:.2f}%，主要驱动为{driver_copy}。"
        )

        opposing_direction = "bearish" if up_probability >= 50 else "bullish"
        if asset == "US5Y":
            opposing_direction = "bullish" if up_probability >= 50 else "bearish"
        opposing_event = next(
            (item for item in top_events if item["direction"] == opposing_direction),
            None,
        )
        risk_text = (
            f"反向风险：{opposing_event['title']}"
            if opposing_event
            else (
                "反向风险：美元与实际利率快速变化、突发地缘事件"
                if asset == "GOLD"
                else "反向风险：OPEC 产量调整、库存意外、供应中断与近月合约换月"
                if asset == "OIL"
                else "反向风险：通胀意外、美联储政策、五年期国债拍卖与期限溢价变化"
                if asset == "US5Y"
                else "反向风险：杠杆清算、ETF 资金流突变与监管消息"
            )
        )

        outlooks.append(
            {
                "asset": asset,
                "asset_name": asset_name,
                "generated_at": iso_utc(now),
                "news_summary": {
                    "window_hours": 24,
                    "story_count": len(recent_news),
                    "channel_count": len(channels),
                    "bullish_count": bullish_count,
                    "bearish_count": bearish_count,
                    "neutral_count": neutral_count,
                    "observation_count":observation_count,
                    "score": round(news_score, 4),
                    "tone": tone,
                    "themes": themes,
                    "text": summary_text,
                    "key_events": top_events,
                },
                "forecast": {
                    "score_kind": "uncalibrated_research_signal",
                    "horizon": "24h",
                    "signal": signal,
                    "up_probability": round(up_probability, 1),
                    "down_probability": round(down_probability, 1),
                    "confidence": round(confidence, 1),
                    "expected_move_pct": expected_move,
                    "expected_low": expected_low,
                    "expected_high": expected_high,
                    "currency": str(market.get("currency") or "USD"),
                    "drivers": drivers,
                    "text": forecast_text,
                    "risk_text": risk_text,
                },
            }
        )
        forecast = outlooks[-1]["forecast"]
        forecast["available"] = bool(probability) and probability.get("available") is not False and probability.get("up_probability") is not None
        if not forecast["available"]:
            forecast.update({key: None for key in ("up_probability", "down_probability", "confidence", "expected_move_pct", "expected_low", "expected_high")})
            reason = "；".join(probability.get("drivers") or [f"{asset_name}行情尚未就绪"])
            forecast.update(signal="待确认", text=f"{reason}，暂不提供未来24小时方向判断。")
        if asset in SLOW_ASSET_INTERVALS:
            forecast["basis_note"] = "五年期收益率方向 / 非债券价格 / 延迟行情" if asset == "US5Y" else "WTI 近月期货 / 延迟行情 / 未校准研究信号"
            if forecast["available"]:
                forecast["text"] = "基于延迟行情的研究估计。" + forecast["text"]
            if asset == "US5Y":
                outlooks[-1]["news_summary"]["text"] += " 新闻情绪针对债券价格。"
                if forecast["available"]:
                    forecast["expected_move_bps"] = round(price * expected_move, 2)
                    forecast["text"] = (f"未来24小时收益率信号{signal}：上行信号 {up_probability:.1f} 点，下行信号 {down_probability:.1f} 点。"
                                        f"启发式波动估计约 ±{forecast['expected_move_bps']:.2f}bp；收益率与债券价格通常反向，未经概率校准。")
    return outlooks


def _serialize_market_data(data: AssetMarketData) -> dict[str, Any]:
    points = [[bar.timestamp * 1000, round(bar.close, 4)] for bar in data.bars[-96:]]
    age_seconds = max(0, int((utc_now() - data.price_time).total_seconds()))
    return {
        "key": data.key,
        "symbol": data.symbol,
        "name": data.name,
        "subtitle": data.subtitle,
        "price": round(data.price, 4),
        "currency": data.currency,
        "price_time": iso_utc(data.price_time),
        "age_seconds": age_seconds,
        "change_24h": round(data.change_24h, 4),
        "high_24h": round(data.high_24h, 4),
        "low_24h": round(data.low_24h, 4),
        "volume_24h": round(data.volume_24h, 4),
        "source": data.source,
        "source_url": data.source_url,
        "delayed": data.delayed,
        "chart": points,
        "anomaly": _asset_anomaly_metrics(data),
        "contract_name": data.contract_name,
        "unit": "年化收益率 %" if data.key == "US5Y" else "USD / 桶" if data.key == "OIL" else data.currency,
        "change_bps": round((data.price - data.previous_close) * 100, 2) if data.key == "US5Y" and data.previous_close is not None else None,
        "quote_status": _delayed_quote_status(data) if data.key in SLOW_ASSET_INTERVALS else "stale" if _live_analysis_unavailable(data) else "live",
        "quote_note": "五年期美债收益率指标，非债券价格；1bp=0.01个百分点；较前收变化按原始报价日计算。公开源延迟报价，每60秒检查。" if data.key == "US5Y" else "WTI 近月期货，非现货；公开源延迟报价，每60秒检查，换月可能影响涨跌。" if data.key == "OIL" else "",
    }


def _asset_anomaly_metrics(data: AssetMarketData) -> dict[str, Any]:
    if data.key not in ASSET_ANOMALY_RULES:
        return {"triggered": False, "direction": "neutral", "label": "未启用提醒", "reason": "系统提醒仅覆盖黄金和 BTC"}
    reason = _live_analysis_unavailable(data)
    if reason:
        return {"triggered": False, "direction": "neutral", "label": "等待有效报价", "reason": reason}
    rules = ASSET_ANOMALY_RULES[data.key]
    return_5m = _return_for_minutes(data.bars, 5, data.price)
    return_15m = _return_for_minutes(data.bars, 15, data.price)
    return_24h = data.change_24h
    recent_moves = [
        abs(_percent_change(current.close, previous.close))
        for previous, current in zip(data.bars[-49:-1], data.bars[-48:])
        if previous.close > 0
    ]
    baseline_move = statistics.median(recent_moves) if recent_moves else 0.0
    volatility_ratio = abs(return_5m) / max(baseline_move, 0.01)

    candidates: list[dict[str, Any]] = []
    for horizon, value in (("5m", return_5m), ("15m", return_15m), ("24h", return_24h)):
        threshold = rules[horizon]
        if abs(value) >= threshold:
            candidates.append(
                {
                    "kind": "return",
                    "horizon": horizon,
                    "return_pct": value,
                    "threshold": threshold,
                    "severity": abs(value) / threshold,
                }
            )
    if (
        abs(return_5m) >= rules["volatility_floor"]
        and volatility_ratio >= rules["volatility_multiple"]
    ):
        candidates.append(
            {
                "kind": "volatility",
                "horizon": "5m",
                "return_pct": return_5m,
                "threshold": rules["volatility_floor"],
                "severity": volatility_ratio / rules["volatility_multiple"] + 0.4,
            }
        )

    selected = max(candidates, key=lambda item: float(item["severity"]), default=None)
    if selected:
        rising = float(selected["return_pct"]) > 0
        horizon_labels = {"5m": "5分钟", "15m": "15分钟", "24h": "24小时"}
        if selected["kind"] == "volatility":
            label = "波动率突增"
        elif selected["horizon"] == "24h":
            label = "24小时强势" if rising else "24小时弱势"
        else:
            label = f"{horizon_labels[selected['horizon']]}急涨" if rising else f"{horizon_labels[selected['horizon']]}急跌"
        reason = (
            f"{horizon_labels[selected['horizon']]}涨跌 {float(selected['return_pct']):+.2f}%，"
            f"触及 {data.name} 异动阈值 {float(selected['threshold']):.2f}%"
        )
    else:
        label = "波动正常"
        reason = "当前短周期波动未达到提醒阈值"

    return {
        "triggered": selected is not None,
        "direction": "up" if selected and float(selected["return_pct"]) > 0 else "down" if selected else "neutral",
        "label": label,
        "primary_horizon": str(selected["horizon"]) if selected else "",
        "primary_return": round(float(selected["return_pct"]), 4) if selected else 0.0,
        "threshold": round(float(selected["threshold"]), 4) if selected else 0.0,
        "severity": round(float(selected["severity"]), 4) if selected else 0.0,
        "return_5m": round(return_5m, 4),
        "return_15m": round(return_15m, 4),
        "return_24h": round(return_24h, 4),
        "baseline_5m_move": round(baseline_move, 4),
        "volatility_ratio": round(volatility_ratio, 2),
        "reason": reason,
    }


def _asset_alert_candidate(
    current: AssetMarketData,
    previous: AssetMarketData | None,
) -> dict[str, Any] | None:
    if current.key not in ASSET_QUOTE_JUMP_RULES:
        return None
    if previous is None or current.price <= 0 or previous.price <= 0:
        return None
    if _live_analysis_unavailable(current) or not math.isfinite(previous.price):
        return None

    candidates: list[dict[str, Any]] = []
    elapsed_seconds = (current.price_time - previous.price_time).total_seconds()
    if 0.5 <= elapsed_seconds <= 300:
        return_pct = _percent_change(current.price, previous.price)
        rules = ASSET_QUOTE_JUMP_RULES[current.key]
        is_fast = elapsed_seconds <= 10
        elapsed_minutes = elapsed_seconds / 60.0
        threshold = (
            rules["fast"]
            if is_fast
            else rules["minute"] * math.sqrt(max(1.0, elapsed_minutes))
        )
        if abs(return_pct) >= threshold:
            rising = return_pct > 0
            horizon_label = (
                f"{max(1, round(elapsed_seconds))}秒"
                if is_fast
                else f"{max(1, round(elapsed_minutes))}分钟"
            )
            candidates.append(
                {
                    "direction": "up" if rising else "down",
                    "label": f"{'秒级' if is_fast else '分钟'}急{'涨' if rising else '跌'}",
                    "horizon_label": horizon_label,
                    "return_pct": return_pct,
                    "severity": abs(return_pct) / threshold + 0.5,
                    "reason": f"最新报价在{horizon_label}内达到系统提醒阈值",
                }
            )

    signal = _asset_anomaly_metrics(current)
    previous_signal = _asset_anomaly_metrics(previous)
    signal_escalated = (
        bool(signal["triggered"])
        and (
            not bool(previous_signal["triggered"])
            or signal["direction"] != previous_signal["direction"]
            or float(signal["severity"]) >= float(previous_signal["severity"]) * 1.5
        )
    )
    if signal_escalated:
        candidates.append(
            {
                "direction": signal["direction"],
                "label": signal["label"],
                "horizon_label": {
                    "5m": "5分钟",
                    "15m": "15分钟",
                    "24h": "24小时",
                }.get(str(signal["primary_horizon"]), "短周期"),
                "return_pct": float(signal["primary_return"]),
                "severity": float(signal["severity"]),
                "reason": signal["reason"],
            }
        )
    return max(candidates, key=lambda item: float(item["severity"]), default=None)


def _read_json(url: str, headers: dict[str, str], timeout_seconds: int) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError("行情响应过大")
    return json.loads(raw)


def _parse_iso_datetime(value: str, *, strict: bool = False) -> datetime:
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        if strict: raise ValueError("报价缺少有效的原始时间") from None
        return datetime(1970,1,1,tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        if strict: raise ValueError("报价时间缺少时区")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bar_at_or_before(bars: list[PriceBar] | tuple[PriceBar, ...], timestamp: int) -> PriceBar | None:
    selected = None
    for bar in bars:
        if bar.timestamp <= timestamp:
            selected = bar
        else:
            break
    return selected


def _return_for_minutes(bars: tuple[PriceBar, ...], minutes: int, current_price: float) -> float:
    target = bars[-1].timestamp - minutes * 60
    reference = _bar_at_or_before(bars, target) or bars[0]
    return _percent_change(current_price, reference.close)


def _percent_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current / previous - 1.0) * 100.0


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _trend_signal(closes: list[float]) -> float:
    if len(closes) < 21 or closes[-1] == 0:
        return 0.0
    spread_pct = (_ema(closes[-40:], 8) / _ema(closes[-80:], 21) - 1.0) * 100.0
    return math.tanh(spread_pct / 0.45)


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    changes = [closes[index] - closes[index - 1] for index in range(len(closes) - period, len(closes))]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _realized_volatility(closes: list[float]) -> float:
    if len(closes) < 3:
        return 0.0
    returns = [
        math.log(closes[index] / closes[index - 1])
        for index in range(max(1, len(closes) - 30), len(closes))
        if closes[index - 1] > 0 and closes[index] > 0
    ]
    return statistics.pstdev(returns) * 100.0 if len(returns) > 1 else 0.0


def _momentum_scale(asset: str, horizon: str) -> float:
    scales = {
        "GOLD": {"15m": 0.18, "1h": 0.38, "24h": 1.10},
        "BTC": {"15m": 0.45, "1h": 0.95, "24h": 3.00},
        "OIL": {"15m": 0.35, "1h": 0.80, "24h": 2.50},
        "US5Y": {"15m": 1.0, "1h": 2.5, "24h": 6.0},
    }
    return scales[asset][horizon]


def _aggregate_news(asset: str, news: list[dict[str, Any]]) -> tuple[float, int]:
    now = utc_now()
    weighted = 0.0
    total_weight = 0.0
    count = 0
    seen_events: set[str] = set()
    for item in news:
        if item.get("asset") != asset or item.get("probability_eligible") is False:
            continue
        event_key = item.get("event_key") or news_event_key("GLOBAL", asset, str(item.get("title", "")),
            str(item.get("raw_summary", "")), str(item.get("published_at", "")))
        if event_key and event_key in seen_events:
            continue
        if event_key:
            seen_events.add(event_key)
        published = _parse_iso_datetime(str(item.get("published_at", "")))
        age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
        source_weight = _clamp(float(item.get("source_weight") or 0.9), 0.6, 1.2)
        relevance_weight = _clamp(float(item.get("relevance_weight") or 1.0), 0.5, 1.0)
        weight = math.exp(-age_hours / 30.0) * source_weight * relevance_weight
        weighted += float(item.get("impact_score", 0.0)) * weight
        total_weight += weight
        count += 1
    return (weighted / total_weight if total_weight else 0.0, count)


def _probability_drivers(return_pct: float, trend: float, news_score: float, news_count: int) -> list[str]:
    momentum = f"动量{return_pct:+.2f}%"
    trend_copy = "短趋势向上" if trend > 0.12 else "短趋势向下" if trend < -0.12 else "短趋势平稳"
    news_copy = (
        f"新闻偏多({news_count})"
        if news_score > 0.08
        else f"新闻偏空({news_count})"
        if news_score < -0.08
        else f"新闻中性({news_count})"
    )
    return [momentum, trend_copy, news_copy]


def _news_direction(text: str, asset: str | None = None) -> float:
    if asset == "OIL":
        return _oil_news_direction(text)
    if asset == "US5Y":
        return _treasury_news_direction(text)
    asset_positive: tuple[str, ...] = ()
    asset_negative: tuple[str, ...] = ()
    if asset == "GOLD":
        asset_positive = (
            "gold gains", "gold rises", "gold rallies", "gold rebounds", "gold jumps",
            "gold hits fresh high", "xau/usd extends rally", "dollar slides", "dollar weakens",
            "lower yields", "long gold", "黄金上涨", "金价上涨", "黄金走强", "金价走高",
            "美元走弱",
        )
        asset_negative = (
            "gold falls", "gold drops", "gold slides", "gold tumbles", "xau/usd extends losses",
            "dollar rallies", "dollar strengthens", "higher yields", "黄金下跌", "金价下跌",
            "黄金走弱", "金价走低", "美元走强",
        )
    elif asset == "BTC":
        asset_positive = (
            "bitcoin rallies", "bitcoin rises", "bitcoin rebounds", "bitcoin jumps", "btc rallies",
            "btc rises", "spot bitcoin etf inflow", "short squeeze", "比特币上涨", "比特币反弹",
            "比特幣上漲", "资金流入",
        )
        asset_negative = (
            "bitcoin falls", "bitcoin drops", "bitcoin slides", "bitcoin tumbles", "btc falls",
            "btc drops", "spot bitcoin etf outflow", "long liquidation", "比特币下跌",
            "比特幣下跌", "资金流出",
        )
    positive = sum(
        1 for phrase in GENERAL_POSITIVE + asset_positive if _contains_pattern(text, phrase)
    )
    negative = sum(
        1 for phrase in GENERAL_NEGATIVE + asset_negative if _contains_pattern(text, phrase)
    )
    total = positive + negative
    return 0.0 if total == 0 else (positive - negative) / total


def _treasury_news_direction(text: str) -> float:
    text = text.casefold()
    if re.search(r"\b(?:not|denies|unlikely)\b|否认|否認|未降息|未加息|不降息", text):
        return 0.0
    yield_up = bool(re.search(r"\byields?\b.{0,25}\b(?:rise[sd]?|rose|surge[sd]?|jump[sd]?|higher|climb[sd]?)\b|收益率(?:上[涨漲升行]|走高)|加息", text))
    yield_down = bool(re.search(r"\byields?\b.{0,25}\b(?:fall[sd]?|fell|drop[sd]?|lower|decline[sd]?|slide[sd]?)\b|收益率(?:下降|下跌|下行|走低)|降息", text))
    if yield_up == yield_down:
        return 0.0
    return -1.0 if yield_up else 1.0


def _oil_news_direction(text: str) -> float:
    text = text.casefold()
    if re.search(r"\b(?:not|no|unlikely to|denies|denied)\b|否认|否認|不减产|不減產|未减产|未減產|取消减产", text):
        return 0.0
    positive = (
        r"\b(?:oil|crude|wti|brent)(?:\s+(?:oil|crude|prices?|futures))*\s+(?:rise[sd]?|rall(?:y|ies)|gain[sd]?|surge[sd]?|jump[sd]?)\b",
        r"\b(?:output|production) cuts?\b|\bcuts? (?:oil )?(?:output|production)\b",
        r"\b(?:oil |crude |crude oil )?(?:inventories|stockpiles)\b.{0,25}\b(?:fall[sd]?|drop[sd]?|decline[sd]?)\b",
        r"\b(?:inventory|crude|oil) (?:draw|drawdown)\b|\bsupply disruption\b",
        r"减产|減產|供应中断|供應中斷|油价上涨|油價上漲|原油上涨|原油上漲|原油库存下降|原油庫存下降",
    )
    negative = (
        r"\b(?:oil|crude|wti|brent)(?:\s+(?:oil|crude|prices?|futures))*\s+(?:fall[sd]?|drop[sd]?|slide[sd]?|slump[sd]?|tumble[sd]?)\b",
        r"\b(?:boosts?|raises?|increases?) (?:oil )?(?:output|production)\b|\b(?:output|production) (?:increase|hike)\b",
        r"\b(?:oil |crude |crude oil )?(?:inventories|stockpiles)\b.{0,25}\b(?:rise[sd]?|increase[sd]?|build[sd]?|surge[sd]?)\b",
        r"\b(?:inventory|crude|oil) build\b|\boversupply\b|\b(?:oil )?demand (?:falls|weakens)\b",
        r"增产|增產|供应过剩|供應過剩|油价下跌|油價下跌|原油下跌|原油库存增加|原油庫存增加|原油库存上升|原油庫存上升",
    )
    up = any(re.search(pattern, text) for pattern in positive)
    down = any(re.search(pattern, text) for pattern in negative)
    return 0.0 if up == down else 1.0 if up else -1.0


def _oil_news_reason(text: str, score: float) -> str:
    if abs(score) <= 0.08:
        return "对油价的净影响待确认：方向证据不足或供需与价格信号冲突，需核对原文及市场预期。"
    tone = "利多油价" if score > 0 else "利空油价"
    if re.search(r"inventor|stockpile|drawdown|库存|庫存", text):
        reason = "库存下降通常收紧供需" if score > 0 else "库存增加通常缓解供应紧张"
    elif re.search(r"output|production|供[应應]|[减減增]产|[减減增]產", text):
        reason = "减产或供应中断减少可用供给" if score > 0 else "增产或供应过剩增加可用供给"
    else:
        reason = "标题反映价格与需求动量，尚不等同于基本面变化"
    return f"{tone}：{reason}；属于规则研究信号，仍需核对实际值相对预期的差异。"


def _asset_event_category(text: str) -> str:
    for category, patterns in EVENT_PATTERNS:
        if any(_contains_pattern(text, pattern) for pattern in patterns):
            return category
    return "market_news"


def _contains_pattern(text: str, pattern: str) -> bool:
    normalized = pattern.casefold()
    if normalized.isascii() and any(character.isalnum() for character in normalized):
        escaped = re.escape(normalized).replace(r"\ ", r"\s+")
        return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None
    return normalized in text


def _asset_event_label(category: str) -> str:
    return {
        "macro": "宏观利率",
        "fund_flows": "资金流向",
        "tax_policy": "税费政策",
        "regulation": "监管政策",
        "market_structure": "市场结构",
        "geopolitics": "地缘风险",
        "supply_demand": "供需变化",
        "market_news": "市场消息",
    }.get(category, "市场消息")


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
