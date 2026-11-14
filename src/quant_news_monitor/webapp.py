from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import os
import re
import socket
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from .assistant import Assistant
from urllib.parse import parse_qs, quote, unquote, urlparse

from .analyzers import select_analyzer
from .adr_monitor import HynixAdrMonitor
from .asset_monitor import CrossAssetMonitor, build_asset_outlooks
from .config import load_config
from .config_store import ConfigStore
from .fund_data import FUND_SOURCE_NAME, search_china_funds
from .korea_stock_monitor import KoreaStockMonitor
from .index_monitor import IndexMonitor
from .market_scanner import MarketMovementScanner, MovementRunSummary
from .models import iso_utc, news_event_key, parse_datetime, utc_now
from .monitor import NewsMonitor, RunSummary
from .portfolio_monitor import PortfolioMonitor, build_portfolio_analysis
from .portfolio_book import get_portfolio, portfolios, all_positions
from .startup_cache import SnapshotStore
from .read_snapshot import ReadSnapshot
from .us_sessions import build_extended_monitor
from .portfolio_chart import CHART_MODES, DEFAULT_PERIODS, chart_session_status, fetch_portfolio_chart, build_technical_analysis, normalize_chart_interval, resample_intraday_chart
from .short_term import analyze_short_term, forecast_duration_minutes
from .short_term_research import MinuteResearch, target_key
from .short_term_signals import STRATEGIES, DEFAULT_BENCHMARKS
from .short_term_models import MODELS
from .runtime import build_sources
from .storage import NewsStore
from .news_translation import NewsTranslator, is_chinese_headline
from .stock_mentions import matches_stock
from .social_sentiment import SocialSentimentMonitor
from .research_ledger import ResearchLedger
from .etf_research import import_payload
from . import investment
from .etf_sources import load_etf
from .company_landscape import load_company_landscape, seed_company_research
from .fundamentals import FundamentalMonitor
from .earnings_calendar import EarningsCalendar
from .forward_monitor import ForwardMonitor
from .briefing import BriefingMonitor
from .trump_monitor import (
    TrumpMonitor,
    apply_trump_factor_to_asset_snapshot,
    build_trump_factor,
)


UI_DIR = Path(__file__).resolve().parent / "ui"
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

MOVEMENT_MARKET_ORDER = ("CN", "HK", "US", "KR")
MOVEMENT_CORE_STOCKS = (
    {"market": "KR", "symbol": "005930.KS", "name": "三星电子"},
    {"market": "KR", "symbol": "000660.KS", "name": "SK 海力士"},
)


class AppState:
    portfolio_desktop_notifications = True

    def __init__(
        self,
        config_path: str | Path,
        *,
        start_asset_monitor: bool = True,
    ):
        self.config_path = Path(config_path).resolve()
        self.assistant = Assistant(self.config_path.parent)
        modules = sorted(path for path in Path(__file__).parent.iterdir() if path.suffix in {".py", ".json"})
        manifest = "".join(f"{path.name}:{hashlib.sha256(path.read_bytes()).hexdigest().upper()}\n" for path in modules)
        self.build_id = hashlib.sha256(manifest.encode("utf-8")).hexdigest().upper()
        self.config_store = ConfigStore(self.config_path)
        raw = self.config_store.read()
        self.research_ledger = ResearchLedger(load_config(self.config_path).storage.database.parent / "research.db")
        self.snapshot_store = SnapshotStore(load_config(self.config_path).storage.database)
        self.company_landscape = load_company_landscape(self.research_ledger)
        self.auto_enabled = bool(raw.get("auto_monitor_enabled", False))
        self.scan_in_progress = False
        self.movement_scan_in_progress = False
        self.last_run: dict[str, Any] | None = None
        self.last_movement_run: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._chart_cache: dict[tuple, tuple[float, dict]] = {}
        self._restore_chart_cache()
        self._stop_event = threading.Event()
        self._next_scan_at = time.monotonic()
        self._next_movement_at = 0.0
        self._movement_not_before = 0.0
        self.translator = NewsTranslator(self.config_path.parent, enabled=bool(raw.get("news_translation_enabled", False)))
        self._scheduler = threading.Thread(
            target=self._scheduler_loop,
            name="news-monitor-scheduler",
            daemon=True,
        )
        self.asset_monitor = CrossAssetMonitor(
            self.config_path,
            news_interval_seconds=max(1,min(300,int(raw.get("asset_news_interval_seconds",15)))),
            ledger=self.research_ledger,
            snapshot_store=self.snapshot_store,
            system_notifications_enabled=bool(
                raw.get("asset_system_notifications_enabled", True)
            ),
        )
        self.trump_monitor = TrumpMonitor(self.config_path,ledger=self.research_ledger)
        self.hynix_adr_monitor = HynixAdrMonitor(snapshot_store=self.snapshot_store)
        self.korea_stock_monitor = KoreaStockMonitor(snapshot_store=self.snapshot_store)
        self.index_monitor = IndexMonitor(snapshot_store=self.snapshot_store, config_store=self.config_store)
        self.portfolio_monitor = PortfolioMonitor(str(self.config_path), snapshot_store=self.snapshot_store,
                                                  system_notifications_allowed=self.portfolio_desktop_notifications)
        self.social_monitor = SocialSentimentMonitor(self.config_path,ledger=self.research_ledger, trump_snapshot=self.trump_monitor.snapshot,
                                                    news_health=lambda: self.last_run if self.auto_enabled else None)
        self.forward_monitor = ForwardMonitor(self.config_path,self.research_ledger,lambda:self.assets_snapshot(for_research=True))
        self.minute_research = MinuteResearch(load_config(self.config_path).storage.database.parent / 'minute-research.db',
                                              self.portfolio_chart, context_provider=self.short_term_context)
        self.fundamental_monitor = FundamentalMonitor(self.config_path, self.research_ledger, self.company_landscape["companies"])
        self.earnings_calendar = EarningsCalendar(load_config(self.config_path).storage.database, self.config_store)
        from .briefing_editorial import fetch_week_calendar
        self.briefing_monitor = BriefingMonitor(self.config_path, self.briefing_context, week_calendar_fetcher=fetch_week_calendar)
        from .instrument_analysis import InstrumentAnalysis
        self.instrument_analysis = InstrumentAnalysis(self)
        from .movement_investigation import MovementInvestigation
        self.movement_investigation = MovementInvestigation(self)
        from .portfolio_research import PortfolioResearch
        self.portfolio_research = PortfolioResearch(self)
        self._portfolio_researches = {"default": self.portfolio_research}
        self._scheduler.start()
        if start_asset_monitor:
            self.translator.start()
            self.asset_monitor.start()
            self.trump_monitor.start()
            self.hynix_adr_monitor.start()
            self.korea_stock_monitor.start()
            self.index_monitor.start()
            self.portfolio_monitor.start()
            self.social_monitor.start()
            self.forward_monitor.start()
            self.minute_research.start()
            self.fundamental_monitor.start()
            self.earnings_calendar.start()
            self.briefing_monitor.start()

    def briefing_context(self):
        """Refresh news once, then collect bounded, attributed inputs off the UI thread."""
        result = {"news": [], "events": [], "earnings": [], "errors": []}
        self.trigger_scan()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self._stop_event.wait(.2):
            with self._lock:
                if not self.scan_in_progress:
                    break
        with self._lock:
            run = copy.deepcopy(self.last_run or {})
            collecting = self.scan_in_progress
        result["news_last_scan_at"] = run.get("finished_at")
        if collecting:
            result["errors"].append("新闻刷新尚未完成，本期采用已采集且通过时间校验的记录")
        elif run.get("errors"):
            result["errors"].append("部分新闻来源刷新失败，覆盖可能不完整")
        store = NewsStore(load_config(self.config_path).storage.database)
        for name, reader in (("news", lambda: store.recent_news(10000)),
                             ("events", lambda: store.latest_market_movements(200))):
            try:
                result[name] = reader()
            except Exception as exc:
                result["errors"].append(f"{name}读取失败：{type(exc).__name__}")
        try:
            calendar = self.earnings_calendar.query(days="7", scope="all", limit=10000)
            result["earnings"] = calendar["items"]
            if calendar["runtime"].get("stale"):
                result["errors"].append("财报日程为上次缓存，日期待核实")
        except Exception as exc:
            result["errors"].append(f"财报日程读取失败：{type(exc).__name__}")
        result["portfolio_snapshot"] = self.portfolio_monitor.snapshot()
        result["collected_at"] = iso_utc(utc_now())
        return result

    def get_portfolio_research(self, portfolio_id="default"):
        get_portfolio(self.config_store.read(), portfolio_id)
        from .portfolio_research import PortfolioResearch
        with self._lock:
            if portfolio_id not in self._portfolio_researches:
                self._portfolio_researches[portfolio_id] = PortfolioResearch(self, portfolio_id)
            return self._portfolio_researches[portfolio_id]

    def portfolio_directory(self, raw=None, current=None):
        raw = self.config_store.read() if raw is None else raw
        rows, events = [], []
        for book in portfolios(raw):
            view = current if current and book["id"] == current["id"] else build_portfolio_analysis(
                book.get("positions", []), self.portfolio_monitor.snapshot(book["id"]), [], [],
                principal_cny=book.get("principal_cny"), account=book)
            rows.append({"id": book["id"], "name": book["name"], "base_currency": view["base_currency"],
                         "summary": view["summary"], "runtime": view["runtime"], "updated_at": view["runtime"].get("updated_at")})
            events.extend(view.get("risk", {}).get("events", []))
        return {"portfolios": rows, "portfolio_risk_events": events}

    def _news_inputs(self, database):
        def load():
            store = NewsStore(database)
            return {"news": store.recent_news(10000), "alerts": store.recent_alerts(10000),
                    "total_news": store.count_news(), "as_of": iso_utc(utc_now())}
        with self._lock:
            if getattr(self, "_news_read_path", None) != database:
                self._news_read_path = database
                self._news_read_cache = ReadSnapshot(load)
            cache = self._news_read_cache
        return cache.get()

    def invalidate_read_snapshots(self):
        with self._lock:
            cache = getattr(self, "_news_read_cache", None)
        if cache is not None:
            cache.invalidate()

    def runtime_snapshot(self, portfolio_id="default"):
        get_portfolio(self.config_store.read(), portfolio_id)
        with self._lock:
            runtime = {"auto_enabled": self.auto_enabled, "scan_in_progress": self.scan_in_progress,
                       "movement_scan_in_progress": self.movement_scan_in_progress,
                       "last_run": self.last_run, "last_movement_run": self.last_movement_run}
        snapshot = self.portfolio_monitor.snapshot(portfolio_id)
        return {"runtime": runtime, "portfolio": {"id": portfolio_id, "runtime": {
            "refresh_in_progress": snapshot.get("refresh_in_progress", False),
            "updated_at": snapshot.get("updated_at")}}, "trump": {"runtime": self.trump_monitor.snapshot()["runtime"]}}

    def portfolio_details(self, portfolio_id="default", market=None, symbol=None):
        account = get_portfolio(self.config_store.read(), portfolio_id)
        if market is not None or symbol is not None:
            from .security_master import normalize
            market, symbol = normalize(market or "", symbol or "")
            if not any(p["market"] == market and p["symbol"] == symbol for p in account.get("positions", [])):
                raise ValueError("持仓不存在，请刷新后重试")
            target = f"{market}:{symbol}"
            return {"portfolio_id": portfolio_id, "target": target,
                    "cards": self.research_ledger.position_cards({target})}
        portfolio = build_portfolio_analysis(account.get("positions", []),
            self.portfolio_monitor.snapshot(portfolio_id), [], [], account=account)
        return {"portfolio_id": portfolio_id, "trades": {**portfolio["trades"], "records_loaded": True}}

    def export_portfolio(self, portfolio_id="default"):
        from .portfolio_export import build_workbook
        captured_at = utc_now()
        raw = self.config_store.read()
        account = get_portfolio(raw, portfolio_id)
        snapshot = self.portfolio_monitor.snapshot(portfolio_id)
        kind = lambda name: name if portfolio_id == "default" else name + ":" + portfolio_id
        with self._lock:
            research = self._portfolio_researches.get(portfolio_id)
        if research is not None:
            with research.lock:
                peers = copy.deepcopy(research.data.get("peers", []))
        else:
            saved = self.research_ledger.inputs(kind("portfolio_research"), limit=1)["items"]
            peers = saved[0]["payload"].get("peers", []) if saved else []
        imports = self.research_ledger.inputs(kind("portfolio_research_import"), limit=50)["items"]
        peers += [row for entry in imports for row in entry["payload"].get("peers", [])]
        whitelist = raw.get("stock_whitelist", [])
        targets = {f"{row['market']}:{row['symbol']}" for row in [*account.get("positions", []), *whitelist]}
        cards = self.research_ledger.position_cards(targets)
        return build_workbook(account, snapshot, whitelist, portfolio_id=portfolio_id,
                              captured_at=captured_at, peers=peers, cards=cards)

    def _portfolio_view(self, account, portfolio_snapshot, scoped_news, watchlist, *, compact=True):
        portfolio_id = account.get("id", "default")
        portfolio_positions = list(account.get("positions") or [])
        portfolio = build_portfolio_analysis(
            portfolio_positions,
            portfolio_snapshot,
            scoped_news,
            watchlist,
            principal_cny=account.get("principal_cny"),
            account=account,
        )
        from .stock_profile import watch_quote
        quote_as_of = utc_now()
        portfolio.update(id=portfolio_id, name=account.get("name") or "默认组合",
                         pending_positions=[{**row, "quote": watch_quote(portfolio_snapshot, row, now=quote_as_of)}
                                            for row in account.get("pending_positions", [])])
        from .portfolio_workspace import holding_metadata, position_workspace
        research = self.get_portfolio_research(portfolio_id)
        with research.lock:
            peers = copy.deepcopy(research.data.get("peers", []))
        imports = self.research_ledger.inputs(research.kind("portfolio_research_import"), limit=50)["items"]
        peers += [r for entry in imports for r in entry["payload"].get("peers", [])]
        cards = self.research_ledger.position_cards({f"{r['market']}:{r['symbol']}" for r in portfolio["positions"]})
        holding_now = utc_now()
        for row in portfolio["positions"]:
            row.update(holding_metadata(row, portfolio_id, holding_now))
            row["workspace"] = position_workspace(row, peers, cards)
            if compact:
                row["workspace"]["cards"] = [{**{key: card.get(key) for key in ("id", "target", "name", "stance", "version", "horizon")},
                    **{key: str(card.get(key) or "")[:240] for key in ("business", "facts", "risks")}}
                    for card in row["workspace"]["cards"]]
                row["workspace"]["cards_loaded"] = False
        if compact:
            portfolio["trades"] = {"summary": portfolio["trades"]["summary"], "records": [], "records_loaded": False}
        return portfolio

    def portfolio_snapshot(self, portfolio_id="default"):
        raw = self.config_store.read()
        account = get_portfolio(raw, portfolio_id)
        config = load_config(self.config_path)
        held = {(row['market'], _normalized_stock_symbol(row['symbol'])) for row in account.get('positions', [])}
        watches = {(w.market, _normalized_stock_symbol(w.symbol)): w for w in config.watchlist if (w.market, _normalized_stock_symbol(w.symbol)) in held}
        now = utc_now()
        records = self._news_inputs(config.storage.database)['value']['news']
        news = []
        for row in records:
            watch = watches.get((str(row.get('market')), _normalized_stock_symbol(row.get('symbol'))))
            if (watch and now - timedelta(hours=config.lookback_hours) <= parse_datetime(str(row.get('published_at') or '')) <= now + timedelta(minutes=5)
                    and (row.get('source') in {'manual', 'local-inbox'} or matches_stock(f"{row.get('title','')} {row.get('news_summary','')}", watch))):
                news.append(row)
        posts = self.trump_monitor.snapshot()['posts']
        factors = [{'market':w.market, 'symbol':w.symbol, 'trump_factor':build_trump_factor(posts, market=w.market,
                    symbol=w.symbol, name=w.name, aliases=w.aliases, include_evidence=False)} for w in watches.values()]
        portfolio = self._portfolio_view(account, self.portfolio_monitor.snapshot(portfolio_id),
                                         _deduplicate_records(news, limit=10000), factors)
        return {'portfolio':portfolio, **self.portfolio_directory(raw, portfolio)}

    def dashboard(self, portfolio_id="default", *, compact=False) -> dict[str, Any]:
        news_as_of = iso_utc(utc_now())
        config = load_config(self.config_path)
        raw = self.config_store.read()
        account = get_portfolio(raw, portfolio_id)
        portfolio_positions = list(account.get("positions") or [])
        movement_scope = _build_movement_scope(all_positions(raw), mode=raw.get("movement_scope", "portfolio"))
        movement_scope_by_key = {
            (str(item["market"]), _normalized_stock_symbol(item["symbol"])): item
            for item in movement_scope["items"]
        }
        movement_keys = set(movement_scope_by_key)
        watched_keys = {
            (item.market, _normalized_stock_symbol(item.symbol))
            for item in config.watchlist
        }
        store = NewsStore(config.storage.database)
        cached_news = self._news_inputs(config.storage.database) if compact else None
        if cached_news:
            news_as_of = cached_news["value"]["as_of"]
        trump = self.trump_monitor.snapshot()
        trump_posts = trump["posts"]
        adr_premium = self.hynix_adr_monitor.snapshot()
        korea_quotes = self.korea_stock_monitor.snapshot()
        trump_factors = {
            (item.market, item.symbol): build_trump_factor(
                trump_posts,
                market=item.market,
                symbol=item.symbol,
                name=item.name,
                aliases=item.aliases,
                include_evidence=not compact,
            )
            for item in config.watchlist
        }
        watchlist = []
        for item in config.watchlist:
            watchlist.append(
                {
                    "market": item.market,
                    "symbol": item.symbol,
                    "name": item.name,
                    "query": item.query,
                    "aliases": list(item.aliases),
                    "trump_factor": copy.deepcopy(
                        trump_factors[(item.market, item.symbol)]
                    ),
                    "adr_premium": (
                        copy.deepcopy(adr_premium)
                        if item.market == "KR"
                        and _normalized_stock_symbol(item.symbol) == "000660"
                        else None
                    ),
                }
            )
        with self._lock:
            runtime = {
                "auto_enabled": self.auto_enabled,
                "scan_in_progress": self.scan_in_progress,
                "movement_scan_in_progress": self.movement_scan_in_progress,
                "last_run": self.last_run,
                "last_movement_run": self.last_movement_run,
            }
        cutoff = utc_now() - timedelta(hours=config.lookback_hours)
        watches = {(w.market, _normalized_stock_symbol(w.symbol)): w for w in config.watchlist}
        def relevant_record(item: dict) -> bool:
            watch = watches.get((str(item.get("market")), _normalized_stock_symbol(item.get("symbol"))))
            return bool(watch and (item.get("source") in {"manual", "local-inbox"}
                                  or matches_stock(f"{item.get('title', '')} {item.get('news_summary', '')}", watch)))
        scoped_news = [
            item
            for item in (cached_news["value"]["news"] if cached_news else store.recent_news(10000))
            if _record_is_watched(item, watched_keys)
            and parse_datetime(str(item.get("published_at") or "")) >= cutoff
            and parse_datetime(str(item.get("published_at") or "")) <= utc_now() + timedelta(minutes=5)
            and relevant_record(item)
        ]
        scoped_news = _deduplicate_records(scoped_news, limit=10000)
        watch_factors = _watch_news_factors(scoped_news)
        for watch in watchlist:
            watch["news_factors"] = self.translator.decorate(watch_factors.get(
                (watch["market"], _normalized_stock_symbol(watch["symbol"])),
                {"bullish": [], "bearish": [], "bullish_count": 0, "bearish_count": 0}))
            watch["news_factors"]["window_hours"] = config.lookback_hours
        scoped_alerts = [
            item
            for item in (cached_news["value"]["alerts"] if cached_news else store.recent_alerts(10000))
            if _record_is_watched(item, watched_keys)
            and parse_datetime(str(item.get("alert_created_at") or "")) >= cutoff
            and parse_datetime(str(item.get("published_at") or "")) >= cutoff
            and abs(float(item.get("score") or 0)) >= config.alert_threshold
            and relevant_record(item)
        ]
        scoped_alerts = _deduplicate_records(scoped_alerts, limit=10000)
        direction_threshold = 0.04
        stats = {
            "window_hours": config.lookback_hours,
            "total_news": cached_news["value"]["total_news"] if cached_news else store.count_news(),
            "news_count": len(scoped_news),
            "bullish": sum(
                1
                for item in scoped_news
                if float(item.get("score") or 0.0) > direction_threshold
            ),
            "bearish": sum(
                1
                for item in scoped_news
                if float(item.get("score") or 0.0) < -direction_threshold
            ),
            "neutral": sum(
                1
                for item in scoped_news
                if abs(float(item.get("score") or 0.0)) <= direction_threshold
            ),
            "direction_threshold": direction_threshold,
            "alert_count": len(scoped_alerts),
            "last_analyzed_at": max(
                (str(item.get("analyzed_at") or "") for item in scoped_news),
                default=None,
            ),
        }
        news = _attach_trump_factors(
            _deduplicate_records(
                scoped_news,
                limit=160,
            ),
            trump_factors,
        )
        alerts = _attach_trump_factors(
            _deduplicate_records(
                scoped_alerts,
                limit=80,
            ),
            trump_factors,
        )
        portfolio_snapshot = self.portfolio_monitor.snapshot(portfolio_id)
        portfolio = self._portfolio_view(account, portfolio_snapshot, scoped_news, watchlist, compact=compact)
        from .stock_profile import watch_quote
        quote_as_of = utc_now()
        whitelist = [{**row, 'quote': watch_quote(portfolio_snapshot, row, now=quote_as_of)} for row in raw.get('stock_whitelist', [])]
        directory = self.portfolio_directory(raw, portfolio)
        extended_monitor = build_extended_monitor(portfolio["positions"])
        extended_symbols = {row["symbol"] for row in extended_monitor["rows"] if row["market_session"] in {"pre", "post", "closed"}}
        scoped_movements = []
        for item in store.latest_market_movements(200):
            in_scope = _record_is_watched(item, movement_keys)
            if not in_scope and (movement_scope["mode"] != "all" or item.get("market") not in {"CN", "HK", "US"}):
                continue
            if item.get("market") == "US" and item.get("symbol") in extended_symbols:
                continue
            tagged = dict(item)
            scope_item = movement_scope_by_key.get(
                (
                    str(item.get("market", "")).upper(),
                    _normalized_stock_symbol(item.get("symbol", "")),
                ), {"source": "market", "source_label": "市场发现"}
            )
            tagged["monitor_source"] = scope_item["source"]
            tagged["monitor_source_label"] = scope_item["source_label"]
            scoped_movements.append(tagged)
        movements = _attach_trump_to_movements(scoped_movements + extended_monitor["events"], trump_posts, include_evidence=not compact)
        movements.sort(key=lambda item: item["intensity_score"], reverse=True)
        latest_movement_scan = max(
            (str(item["scan_id"]) for item in movements),
            default=None,
        )
        movement_scan_by_market = {
            market: max(
                (str(item["scan_id"]) for item in movements if item["market"] == market),
                default=None,
            )
            for market in movement_scope["markets"]
        }
        movement_stats = {
            "count": len(movements),
            "monitored_count": movement_scope["count"],
            "portfolio_count": movement_scope["portfolio_count"],
            "up": sum(1 for item in movements if item["direction"] == "up"),
            "down": sum(1 for item in movements if item["direction"] == "down"),
            "high_confidence": sum(1 for item in movements if float(item["confidence"]) >= 0.7),
            "direct_evidence": sum(1 for item in movements if str(item["evidence_url"]).startswith(("http://", "https://"))),
            "scan_id": latest_movement_scan,
            "stale_markets": [
                market
                for market, scan_id in movement_scan_by_market.items()
                if scan_id and scan_id != latest_movement_scan
            ],
        }
        sentiment = _combine_sentiment_with_trump(
            store.sentiment_by_symbol(hours=config.lookback_hours, records=scoped_news),
            watchlist,
        )
        trump["factors"] = sorted(
            [
                {
                    "market": item["market"],
                    "symbol": item["symbol"],
                    "name": item["name"],
                    **copy.deepcopy(item["trump_factor"]),
                }
                for item in watchlist
            ],
            key=lambda item: abs(float(item["score"])),
            reverse=True,
        )
        trump["global_factor"] = build_trump_factor(
            trump_posts,
            market="GLOBAL",
            symbol="GLOBAL",
            name="Global market",
            include_evidence=not compact,
        )
        return {
            "generated_at": iso_utc(utc_now()),
            "stats": {**stats, "watch_count": len(watchlist)},
            "freshness": {
                "news_published_at": max((str(item.get("published_at") or "") for item in scoped_news), default=None),
                "news_collected_at": max((str(item.get("discovered_at") or "") for item in scoped_news), default=None),
                "news_analyzed_at": stats["last_analyzed_at"],
            },
            "news": self.translator.decorate(news),
            "news_as_of": news_as_of,
            "news_refresh": {key: cached_news[key] for key in ("stale", "refreshing", "error")} if cached_news else {},
            "translation": self.translator.status(),
            "alerts": self.translator.decorate(alerts),
            "movements": self.translator.decorate(movements),
            "movement_stats": movement_stats,
            "extended_monitor": extended_monitor,
            "movement_scope": movement_scope,
            "sentiment_by_symbol": sentiment,
            "watchlist": watchlist,
            "stock_whitelist": whitelist,
            "korea_quotes": korea_quotes,
            "portfolio": portfolio,
            **directory,
            "trump": self.translator.decorate(trump),
            "settings": {
                "poll_interval_minutes": config.poll_interval_minutes,
                "poll_interval_seconds": config.news_interval_seconds,
                "max_items_per_asset": config.max_items_per_asset,
                "asset_news_interval_seconds":self.asset_monitor.news_interval_seconds,
                "lookback_hours": config.lookback_hours,
                "alert_threshold": config.alert_threshold,
                "analysis_mode": config.analysis.mode,
                "model": config.analysis.model,
                "google_news_rss": config.sources.google_news_rss,
                "yahoo_finance_rss": config.sources.yahoo_finance_rss,
                "bing_news_rss": config.sources.bing_news_rss,
                "news_translation_enabled": self.translator.enabled,
                "fundamentals_enabled": bool(raw.get("fundamentals_enabled", False)),
                "movement_scope": movement_scope["mode"],
                "api_key_configured": bool(getattr(config.analysis, "api_key", None)),
                "api_credential": self.analysis_credential_status(),
                "x_api_configured": bool(trump["runtime"]["configured"]),
                "x_api_credential_source": trump["runtime"]["credential_source"],
                "auto_monitor_enabled": bool(raw.get("auto_monitor_enabled", False)),
            },
            "runtime": runtime,
        }

    def short_term_context(self, market, symbol, now):
        config = load_config(self.config_path)
        last_run = copy.deepcopy(self.last_run or {})
        snapshot = {'news_covered': any(w.market == market and w.symbol == symbol for w in config.watchlist),
                    'news_runtime': {'auto_enabled': self.auto_enabled, 'last_run': last_run},
                    'news_error': '；'.join(str(error) for error in last_run.get('errors', [])) or None}
        try:
            snapshot['news'] = NewsStore(config.storage.database).recent_news(1000, market=market, symbol=symbol)
        except Exception as exc:
            snapshot['news_error'] = f'{type(exc).__name__}: {exc}'[:200]
        try:
            facts = self.fundamental_monitor.snapshot()
            snapshot['fundamentals'] = next((row for row in facts['items']
                                            if market == 'US' and row['symbol'] == symbol), None)
        except Exception as exc:
            snapshot['fundamentals_error'] = f'{type(exc).__name__}: {exc}'[:200]
        return snapshot

    def instrument_news_snapshot(self, market: str, symbol: str) -> dict[str, Any]:
        """Use the same full news window and relevance rules as holding news_count."""
        config = load_config(self.config_path)
        market, symbol = str(market).upper(), _normalized_stock_symbol(symbol)
        watch = next((w for w in config.watchlist if w.market == market and _normalized_stock_symbol(w.symbol) == symbol), None)
        if watch is None:
            raise ValueError("该标的不在新闻监测范围内")
        now = utc_now()
        snapshot = self._news_inputs(config.storage.database)
        records = [row for row in snapshot['value']['news']
            if str(row.get('market')) == market and _normalized_stock_symbol(row.get('symbol')) == symbol
            and now - timedelta(hours=config.lookback_hours) <= parse_datetime(str(row.get('published_at') or '')) <= now + timedelta(minutes=5)
            and (row.get('source') in {'manual', 'local-inbox'} or matches_stock(f"{row.get('title','')} {row.get('news_summary','')}", watch))]
        news = self.translator.decorate(_deduplicate_records(records, limit=10000))
        return {'market': market, 'symbol': symbol, 'news': news, 'count': len(news),
                'generated_at': snapshot['value']['as_of'], 'lookback_hours': config.lookback_hours,
                'news_refresh': {key: snapshot[key] for key in ('stale', 'refreshing', 'error')}}

    def news_snapshot(self, *, language: str = "all", cached=False) -> dict[str, Any]:
        if language not in {"zh", "all"}:
            raise ValueError("不支持的新闻语言")
        generated_at = iso_utc(utc_now())
        config = load_config(self.config_path)
        now = utc_now()
        watches = {(w.market, _normalized_stock_symbol(w.symbol)): w for w in config.watchlist}
        records = []
        source_language = "all" if self.translator.enabled else language
        snapshot = self._news_inputs(config.storage.database) if cached and source_language == "all" else None
        if snapshot:
            generated_at = snapshot["value"]["as_of"]
        for item in (snapshot["value"]["news"][:400] if snapshot else NewsStore(config.storage.database).recent_news(400, language=source_language)):
            if language == "zh" and not self.translator.enabled and not is_chinese_headline(str(item.get("title", "")), str(item.get("source", ""))):
                continue
            watch = watches.get((str(item.get("market")), _normalized_stock_symbol(item.get("symbol"))))
            if not watch or not now - timedelta(hours=config.lookback_hours) <= parse_datetime(item.get("published_at")) <= now + timedelta(minutes=5):
                continue
            if item.get("source") not in {"manual", "local-inbox"} and not matches_stock(f"{item.get('title', '')} {item.get('news_summary', '')}", watch):
                continue
            records.append(item)
            if len(records) >= 800:
                break
        news = _deduplicate_records(records, limit=160)
        posts = self.trump_monitor.snapshot()["posts"]
        factors = {(w.market, w.symbol): build_trump_factor(posts, market=w.market, symbol=w.symbol, name=w.name, aliases=w.aliases, include_evidence=not cached)
                   for w in config.watchlist}
        translated = self.translator.decorate(_attach_trump_factors(news, factors))
        if language == "zh":
            translated = [item for item in translated if is_chinese_headline(str(item.get("title", "")), str(item.get("source", "")))
                          or item.get("title_translation_status") == "translated"]
        return {"generated_at": generated_at, "language": language, "news": translated,
                "news_refresh": {key: snapshot[key] for key in ("stale", "refreshing", "error")} if snapshot else {},
                "translation": self.translator.status(),
                "runtime": {"auto_enabled": self.auto_enabled, "scan_in_progress": self.scan_in_progress,
                            "last_run": self.last_run, "poll_interval_seconds": config.news_interval_seconds,
                            "source_note": "每秒检查调度；单轮采集不重叠。实际到达时间取决于上游发布、请求耗时和限流。"}}

    def trigger_scan(
        self,
        *,
        offline: bool = False,
        follow_with_movement: bool = False,
    ) -> bool:
        with self._lock:
            if self.scan_in_progress:
                return False
            self.scan_in_progress = True
        thread = threading.Thread(
            target=self._run_scan,
            kwargs={
                "offline": offline,
                "follow_with_movement": follow_with_movement,
            },
            name="news-monitor-scan",
            daemon=True,
        )
        thread.start()
        return True

    def trigger_movement_scan(self) -> bool:
        with self._lock:
            if self.movement_scan_in_progress or time.monotonic() < self._movement_not_before:
                return False
            self.movement_scan_in_progress = True
        thread = threading.Thread(
            target=self._run_movement_scan,
            name="market-movement-scan",
            daemon=True,
        )
        thread.start()
        return True

    def set_auto(self, enabled: bool) -> None:
        with self._lock:
            self.auto_enabled = enabled
            self._next_scan_at = time.monotonic()
            self.config_store.set_value("auto_monitor_enabled", enabled)

    def add_watch_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.config_store.add_watch_item(payload)

    def remove_watch_item(self, market: str, symbol: str) -> bool:
        return self.config_store.remove_watch_item(market, symbol)

    def add_position(self, payload: dict[str, Any]) -> dict[str, Any]:
        position = self.config_store.add_position(payload)
        self.portfolio_monitor.trigger_refresh()
        return position

    def adjust_position(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        position = self.config_store.adjust_position(payload)
        self.portfolio_monitor.trigger_refresh()
        return position

    def set_position_risk(self, payload: dict[str, Any]) -> dict[str, Any]:
        position = self.config_store.set_position_risk(payload)
        self.portfolio_monitor.trigger_refresh()
        return position

    def remove_position(self, market: str, symbol: str, portfolio_id="default") -> bool:
        removed = self.config_store.remove_position(market, symbol, portfolio_id)
        if removed:
            self.portfolio_monitor.trigger_refresh()
        return removed

    def trigger_portfolio_refresh(self) -> None:
        self.portfolio_monitor.trigger_refresh()

    def clear_positions(self, portfolio_id="default") -> int:
        removed = self.config_store.clear_positions(portfolio_id)
        if removed:
            self.portfolio_monitor.trigger_refresh()
        return removed

    def search_funds(self, query: str) -> dict[str, Any]:
        return {
            "items": search_china_funds(query),
            "source": FUND_SOURCE_NAME,
            "disclaimer": "基金净值按交易日确认，实际持仓以支付宝记录为准。",
        }

    def portfolio_chart(
        self, market: str, symbol: str, period: str | None, mode: str = "daily",
        *, interval: str | None = None,
    ) -> dict[str, Any]:
        market, symbol, mode = market.strip().upper(), symbol.strip().upper(), mode.strip().lower()
        selected = normalize_chart_interval(mode, interval)
        result = self._portfolio_chart_base(market, symbol, period, mode)
        retained = bool(result.get("retained_from_cache"))
        if selected not in {None, "1m"}:
            work = getattr(self, "_chart_runtime", None)
            selected_period = (period or DEFAULT_PERIODS.get(mode) or "").strip().lower()
            result = (work.resample(result, market, symbol, selected_period, selected, resample_intraday_chart)
                      if work else resample_intraday_chart(result, market, selected))
        return self._chart_response(result, market, symbol, retained=retained)

    def _portfolio_chart_base(
        self,
        market: str,
        symbol: str,
        period: str | None,
        mode: str = "daily",
    ) -> dict[str, Any]:
        """Return internal source data; portfolio_chart copies before responding."""
        market, symbol, mode = market.strip().upper(), symbol.strip().upper(), mode.strip().lower()
        key = (market, symbol, mode, (period or DEFAULT_PERIODS.get(mode) or "").strip().lower())
        ttl = (10 if market == "KR" and symbol == "^KS11" else 20 if (market, symbol) in {("CN", "000688.SS"), ("US", "NQ.CFD")} else 60) if mode == "intraday" else 300
        with self._lock:
            from concurrent.futures import Future
            from .chart_cache import ChartWork
            if not hasattr(self, "_chart_runtime"):
                self._chart_runtime = ChartWork()
            work = self._chart_runtime
            cached = self._chart_cache.get(key)
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1]
            pending = work.inflight.get(key)
            owner = pending is None
            if owner:
                pending = work.inflight[key] = Future()
        if not owner:
            result, retained = pending.result()
            return {**result, "retained_from_cache": True} if retained else result
        try:
            try:
                result = copy.deepcopy(fetch_portfolio_chart(market, symbol, period, mode=mode))
            except (OSError, RuntimeError):
                if not cached:
                    raise
                result, retained = cached[1], True
            else:
                retained = False
            if result.get("kind") != "price_line" and result.get("candles") and "oscillators" not in (result.get("technical") or {}):
                result = {**result, "technical": build_technical_analysis(result["candles"], mode=result.get("mode", "daily"), is_fund=False)}
            with self._lock:
                if not retained:
                    self._chart_cache.pop(key, None)
                    self._chart_cache[key] = (time.monotonic(), result)
                    while len(self._chart_cache) > 16:
                        self._chart_cache.pop(next(iter(self._chart_cache)))
                elif self._chart_cache.get(key) is cached:
                    self._chart_cache[key] = (cached[0], result)
                pending.set_result((result, retained))
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                work.inflight.pop(key, None)
        if not retained:
            # Serialize saves, then capture the latest cache: an older request
            # cannot overwrite a newer snapshot. Disk I/O never owns _lock.
            with work.snapshot_lock:
                with self._lock:
                    entries = [{"key": list(k), "data": value[1]} for k, value in self._chart_cache.items()]
                self.snapshot_store.save("charts", {"entries": entries}, force=True)
        return {**result, "retained_from_cache": True} if retained else result

    def _restore_chart_cache(self) -> None:
        entries = (self.snapshot_store.load("charts") or {}).get("entries")
        for entry in entries[-16:] if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            key, data = entry.get("key"), entry.get("data")
            if not isinstance(key, list) or len(key) != 4 or not all(isinstance(part, str) for part in key) or not isinstance(data, dict):
                continue
            if key[0] not in {"CN", "HK", "KR", "US", "ASSET", "FUND"} or key[2] not in CHART_MODES or key[3] not in CHART_MODES[key[2]]:
                continue
            if data.get("mode") != key[2] or data.get("period") != key[3]:
                continue
            stats = data.get("stats")
            if not isinstance(stats, dict) or not isinstance(stats.get("latest"), (float, int)) or not math.isfinite(stats["latest"]) or stats["latest"] <= 0:
                continue
            if not isinstance(data.get("source_url"), str) or urlparse(data["source_url"]).scheme != "https":
                continue
            rows = data.get("points") if data.get("kind") == "nav" else data.get("candles")
            if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
                continue
            price_line = data.get("kind") == "price_line"
            if price_line and (key != ["US", "NQ.CFD", "intraday", "1d"] or data.get("symbol") != "NQ.CFD" or data.get("instrument_type") != "CFD"):
                continue
            fields = ("nav",) if data.get("kind") == "nav" else ("timestamp", "close") if price_line else ("timestamp", "open", "high", "low", "close")
            if not all(all(isinstance(row.get(field), (float, int)) and math.isfinite(row[field]) and row[field] > 0 for field in fields) for row in rows):
                continue
            if data.get("kind") != "nav" and any(row["timestamp"] > time.time() + 60 or (not price_line and (row["high"] < max(row["open"], row["close"], row["low"]) or row["low"] > min(row["open"], row["close"]))) for row in rows):
                continue
            self._chart_cache[tuple(key)] = (-float("inf"), data)

    @staticmethod
    def _chart_response(data: dict, market: str, symbol: str, *, retained: bool = False) -> dict:
        result = copy.deepcopy(data)
        if result.get("kind") != "price_line" and result.get("candles") and "oscillators" not in (result.get("technical") or {}):
            result["technical"] = build_technical_analysis(result["candles"], mode=result.get("mode", "daily"), is_fund=False)
        status = chart_session_status(market, symbol, result)
        if status != "unknown":
            result["session_status"] = status
        if status == "closed":
            result["note"] = "当前图表时段已收市或休市，保留最近有效行情和原始时间；最后一根不一定是官方收盘价。" + result.get("note", "")
        if retained:
            result["retained_from_cache"] = True
            result["note"] = "本次联网未能确认新数据，显示已保存的同标的、同周期图表；不是最新报价。" + result.get("note", "")
        return result

    def update_settings(self, payload: dict[str, Any]) -> None:
        saved = self.config_store.update_settings(payload)
        self.asset_monitor.set_news_interval(saved.get("asset_news_interval_seconds",15))
        self.translator.set_enabled(bool(saved.get("news_translation_enabled", False)))
        if "fundamentals_enabled" in payload and saved.get("fundamentals_enabled"):
            self.fundamental_monitor.trigger_refresh()
        with self._lock:
            self._next_scan_at = time.monotonic() + 1

    def company_research(self) -> dict:
        result = copy.deepcopy(self.company_landscape)
        facts = {row["symbol"]: row for row in self.fundamental_monitor.snapshot()["items"]}
        for company in result["companies"]:
            company["financials"] = facts.get(company.get("ticker"))
        return result

    def restore_features(self) -> dict:
        with self._lock:
            self.update_settings({"news_translation_enabled": True, "fundamentals_enabled": True,
                                  "movement_scope": "all", "google_news_rss": True,
                                  "yahoo_finance_rss": True, "bing_news_rss": True})
            self.set_auto(True)
            self.set_asset_system_notifications(True)
            settings = self.social_monitor.settings()
            self.social_monitor.configure({"enabled": True,
                                           "web_sources": list(dict.fromkeys(settings.get("web_sources", []) + ["hackernews", "valuepickr", "tradingqna"]))})
            self.forward_monitor.configure({"enabled": True})
            created = seed_company_research(self.research_ledger, self.company_landscape)
        return {"ok": True, "research_drafts_created": created, "features": self.feature_status()}

    def feature_status(self) -> dict:
        raw = self.config_store.read()
        credential = self.analysis_credential_status()
        social = self.social_monitor.snapshot()
        translation = self.translator.status()
        trump = self.trump_monitor.snapshot()["runtime"]
        fundamentals = self.fundamental_monitor.snapshot()["runtime"]
        earnings = self.earnings_calendar.runtime()
        assets = self.asset_monitor.snapshot()["runtime"]
        features = [
            {"id": "earnings", "name": "美股财报预告", "state": "error" if earnings["error"] else "ready" if earnings["collected_at"] and not earnings["stale"] else "waiting",
             "detail": earnings["error"] or "独立美股公司库 · 每6小时检查 · 日期为预计；盘前盘后信息按来源提供"},
            {"id": "news", "name": "新闻与事件分析", "state": "running" if self.auto_enabled else "disabled",
             "detail": "免费新闻源；限流时自动退避，不绕过来源访问限制"},
            {"id": "translation", "name": "中文翻译", "state": "disabled" if not translation["enabled"] else "error" if translation["state"] == "unavailable" else "running",
             "detail": translation.get("error") or f"{translation['state']} · 待处理 {translation['pending']} · 原文保留"},
            {"id": "fundamentals", "name": "公司财报", "state": "disabled" if not fundamentals["enabled"] else "error" if fundamentals["error"] else "running",
             "detail": fundamentals["error"] or "SEC 年度财报 · 每6小时检查 · A/H/K与非上市机构未覆盖"},
            {"id": "movements", "name": "市场异动", "state": "running" if self.auto_enabled else "disabled",
             "detail": "A股/港股/美股公开扫描 + 三星/海力士" if raw.get("movement_scope") == "all" else "持仓 + 三星/海力士"},
            {"id": "portfolio", "name": "持仓与盘前盘后", "state": "ready" if all_positions(raw) else "empty",
             "detail": "随录入持仓启用；不会恢复已删除仓位"},
            {"id": "social", "name": "公开社交情绪", "state": "running" if social["runtime"]["enabled"] else "disabled",
             "detail": f"已连接 {social['runtime']['web_connected_count']} 个公开源 · 24小时明确观点 {social['summary']['web_directional_count']} 条"},
            {"id": "trump", "name": "Trump 公开信息与政策因子", "state": "running" if trump["connected"] else "error" if trump["errors"] else "waiting" if trump.get("public_enabled") else "error" if trump["configured"] else "blocked",
             "detail": trump["message"]},
            {"id": "model", "name": "大模型分析", "state": "disabled" if (raw.get("analysis") or {}).get("mode") == "rules" else "ready" if credential["configured"] else "blocked",
             "detail": credential["error"] or ("已有密钥，连接是否成功以实际报告为准；规则模式不会调用模型" if credential["configured"] else "尚未配置模型密钥，可在分析与来源中填写；本地规则仍可使用")},
            {"id": "forward", "name": "前向研究记录", "state": "error" if self.forward_monitor.error else "running" if self.forward_monitor.settings()["enabled"] else "disabled",
             "detail": self.forward_monitor.error or "黄金/BTC前向记录；分数未经概率校准，不下单"},
            {"id": "notifications", "name": "系统异动提醒", "state": "running" if assets["system_notifications_enabled"] and assets["system_notifications_available"] else "disabled",
             "detail": assets.get("system_notification_error") or "仅黄金/BTC，达到条件才发送"},
        ]
        for source in social["sources"]:
            if source["key"] in {"xiaohongshu", "douyin", "x", "threads"}:
                features.append({"id": source["key"], "name": source["name"],
                                 "state": "running" if source["status"] == "connected" else "error" if source["configured"] else "blocked",
                                 "detail": source["message"]})
        return {"features": features, "checked_at": iso_utc(utc_now()),
                "blocked": [row["id"] for row in features if row["state"] == "blocked"]}

    def add_manual_news(self, payload: dict[str, Any]) -> None:
        config = load_config(self.config_path)
        market = str(payload.get("market", "")).upper().strip()
        symbol = str(payload.get("symbol", "")).upper().strip()
        title = str(payload.get("title", "")).strip()
        summary = str(payload.get("summary", "")).strip()
        if not title:
            raise ValueError("新闻标题不能为空")
        if not any(
            item.market == market and item.symbol == symbol for item in config.watchlist
        ):
            raise ValueError("请先把该股票加入股票池")
        record = {
            "market": market,
            "symbol": symbol,
            "title": title,
            "summary": summary,
            "source": "manual",
            "url": str(payload.get("url", "")).strip(),
            "published_at": iso_utc(utc_now()),
        }
        config.sources.inbox.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with config.sources.inbox.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def assets_snapshot(self, *, for_research: bool = False) -> dict[str, Any]:
        trump = self.trump_monitor.snapshot()
        snapshot = apply_trump_factor_to_asset_snapshot(
            self.asset_monitor.snapshot(for_research=for_research),
            trump["posts"],
        )
        snapshot["outlooks"] = build_asset_outlooks(
            snapshot.get("assets", []),
            snapshot.get("news", []),
            snapshot.get("probabilities", []),
        )
        snapshot["trump"] = trump
        snapshot["runtime"]["trump_status"] = trump["runtime"]["status"]
        return snapshot if for_research else self.translator.decorate(snapshot)

    def trigger_asset_refresh(self) -> bool:
        return self.asset_monitor.trigger_refresh(include_news=True)

    def set_asset_system_notifications(self, enabled: bool) -> bool:
        saved = self.config_store.set_asset_system_notifications(enabled)
        return self.asset_monitor.set_system_notifications_enabled(saved)

    def test_asset_system_notification(self) -> tuple[bool, str | None]:
        return self.asset_monitor.test_system_notification()

    def trigger_trump_refresh(self) -> bool:
        return self.trump_monitor.trigger_refresh()

    def update_x_token(self, payload: dict[str, Any]) -> None:
        if bool(payload.get("clear", False)):
            self.trump_monitor.clear_token()
            return
        token = str(payload.get("token", "")).strip()
        if not token:
            raise ValueError("请输入 X API Bearer Token")
        self.trump_monitor.save_token(token)

    def analysis_credential_status(self):
        from .ai_credentials import OpenAICredentials
        return OpenAICredentials(self.config_path).status()

    def update_analysis_credential(self, payload):
        from .ai_credentials import OpenAICredentials
        if not isinstance(payload, dict) or set(payload) not in ({"api_key"}, {"clear"}):
            raise ValueError("密钥设置字段无效")
        store = OpenAICredentials(self.config_path)
        try:
            if "clear" in payload:
                if payload["clear"] is not True:
                    raise ValueError("清除密钥须明确确认")
                store.clear()
            else:
                store.save(payload["api_key"])
        except (OSError, RuntimeError):
            raise ValueError("本机密钥无法保存或读取，请在当前 Windows 账户重试") from None
        return store.status()

    def shutdown(self) -> None:
        self._stop_event.set()
        self.instrument_analysis.shutdown()
        self.movement_investigation.shutdown()
        self.briefing_monitor.shutdown()
        self.assistant.shutdown()
        self.translator.shutdown()
        self.forward_monitor.shutdown()
        self.minute_research.shutdown()
        self.asset_monitor.shutdown()
        self.trump_monitor.shutdown()
        self.hynix_adr_monitor.shutdown()
        self.korea_stock_monitor.shutdown()
        self.index_monitor.shutdown()
        self.portfolio_monitor.shutdown()
        for research in list(self._portfolio_researches.values()):
            research.shutdown()
        self.social_monitor.shutdown()
        self.fundamental_monitor.shutdown()
        self.earnings_calendar.shutdown()

    def _scheduler_loop(self) -> None:
        while not self._stop_event.wait(1.0):
            with self._lock:
                enabled = self.auto_enabled
                due = time.monotonic() >= self._next_scan_at
                scanning = self.scan_in_progress
            if enabled and due and not scanning:
                self.trigger_scan(follow_with_movement=True)

    def _run_scan(self, *, offline: bool, follow_with_movement: bool) -> None:
        started_at = utc_now()
        try:
            config = load_config(self.config_path)
            analyzer, analyzer_message = select_analyzer(
                config.analysis.mode,
                config.analysis.model,
                watchlist=config.watchlist,
                api_key=getattr(config.analysis, "api_key", None),
            )
            monitor = NewsMonitor(
                config,
                build_sources(config),
                analyzer,
                NewsStore(config.storage.database),
            )
            summary = monitor.run_once(offline=offline)
            result = _serialize_run(summary, started_at, analyzer_message, offline)
        except Exception as exc:
            traceback.print_exc()
            result = {
                "started_at": iso_utc(started_at),
                "finished_at": iso_utc(utc_now()),
                "fetched": 0,
                "inserted": 0,
                "analyzed": 0,
                "alert_count": 0,
                "errors": [f"{type(exc).__name__}: {exc}"],
                "analyzer": "启动失败",
                "offline": offline,
            }
        finally:
            try:
                interval = load_config(self.config_path).news_interval_seconds
            except Exception:
                interval = 60
            self.invalidate_read_snapshots()
            with self._lock:
                self.last_run = result
                self.scan_in_progress = False
                self._next_scan_at = time.monotonic() + interval
                should_follow = (
                    follow_with_movement
                    and self.auto_enabled
                    and not self._stop_event.is_set()
                    and time.monotonic() >= self._next_movement_at
                )
                if should_follow:
                    self._next_movement_at = time.monotonic() + (300 if self.config_store.read().get("movement_scope") == "all" else 60)
            if should_follow:
                self.trigger_movement_scan()

    def _run_movement_scan(self) -> None:
        started_at = utc_now()
        raw = self.config_store.read()
        movement_scope = _build_movement_scope(all_positions(raw), mode=raw.get("movement_scope", "portfolio"))
        try:
            config = load_config(self.config_path)
            analyzer, analyzer_message = select_analyzer(
                config.analysis.mode,
                config.analysis.model,
                watchlist=config.watchlist,
                api_key=getattr(config.analysis, "api_key", None),
            )
            focus_symbols = _movement_focus_symbols(movement_scope)
            summary = MarketMovementScanner(
                analyzer,
                focus_symbols=focus_symbols,
            ).run()
            NewsStore(config.storage.database).save_market_movements(list(summary.movements))
            result = _serialize_movement_run(summary, analyzer_message)
            result["monitored_count"] = movement_scope["count"]
            result["retry_after_seconds"] = summary.retry_after_seconds
            if summary.retry_after_seconds:
                with self._lock:
                    self._next_movement_at = max(self._next_movement_at, time.monotonic() + summary.retry_after_seconds)
                    self._movement_not_before = self._next_movement_at
        except Exception as exc:
            result = {
                "started_at": iso_utc(started_at),
                "finished_at": iso_utc(utc_now()),
                "found": 0,
                "direct_evidence": 0,
                "market_counts": {
                    market: 0 for market in movement_scope["markets"]
                },
                "monitored_count": movement_scope["count"],
                "errors": [f"{type(exc).__name__}: {exc}"],
                "analyzer": "启动失败",
            }
        finally:
            with self._lock:
                self.last_movement_run = result
                self.movement_scan_in_progress = False


def _normalized_stock_symbol(symbol: object) -> str:
    normalized = str(symbol or "").upper().rsplit(":", 1)[-1]
    for suffix in (".KS", ".KQ", ".HK", ".SS", ".SZ"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _build_movement_scope(positions: list[dict[str, Any]], *, mode="portfolio") -> dict[str, Any]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for position in positions:
        market = str(position.get("market", "")).upper().strip()
        if market not in MOVEMENT_MARKET_ORDER:
            continue
        symbol = str(position.get("symbol", "")).upper().strip()
        normalized_symbol = _normalized_stock_symbol(symbol)
        if not normalized_symbol:
            continue
        key = (market, normalized_symbol)
        by_key[key] = {
            "market": market,
            "symbol": symbol,
            "name": str(position.get("name") or symbol),
            "is_portfolio": True,
            "is_core": False,
        }

    for core in MOVEMENT_CORE_STOCKS:
        market = str(core["market"])
        symbol = str(core["symbol"])
        key = (market, _normalized_stock_symbol(symbol))
        existing = by_key.get(key)
        if existing is not None:
            existing["is_core"] = True
            continue
        by_key[key] = {
            "market": market,
            "symbol": symbol,
            "name": str(core["name"]),
            "is_portfolio": False,
            "is_core": True,
        }

    order = {market: index for index, market in enumerate(MOVEMENT_MARKET_ORDER)}
    items = sorted(
        by_key.values(),
        key=lambda item: (
            order.get(str(item["market"]), len(order)),
            not bool(item["is_portfolio"]),
            str(item["symbol"]),
        ),
    )
    for item in items:
        if item["is_portfolio"] and item["is_core"]:
            item["source"] = "portfolio_core"
            item["source_label"] = "持仓 · 核心"
        elif item["is_portfolio"]:
            item["source"] = "portfolio"
            item["source_label"] = "持仓"
        else:
            item["source"] = "core"
            item["source_label"] = "核心"

    market_counts = {
        market: sum(1 for item in items if item["market"] == market)
        for market in MOVEMENT_MARKET_ORDER
    }
    markets = [market for market in MOVEMENT_MARKET_ORDER if market_counts[market]]
    return {
        "mode": mode,
        "count": None if mode == "all" else len(items),
        "portfolio_count": sum(1 for item in items if item["is_portfolio"]),
        "core_count": len(MOVEMENT_CORE_STOCKS),
        "markets": list(MOVEMENT_MARKET_ORDER) if mode == "all" else markets,
        "market_counts": market_counts,
        "items": items,
    }


def _movement_focus_symbols(scope: dict[str, Any]) -> dict[str, set[str] | None]:
    focused: dict[str, set[str]] = {}
    for item in scope.get("items", []):
        focused.setdefault(str(item["market"]), set()).add(str(item["symbol"]))
    if scope.get("mode") == "all":
        focused.update({market: None for market in ("CN", "HK", "US")})
    return focused


def _record_is_watched(
    record: dict[str, Any],
    watched_keys: set[tuple[str, str]],
) -> bool:
    key = (
        str(record.get("market", "")).upper(),
        _normalized_stock_symbol(record.get("symbol", "")),
    )
    return key in watched_keys


def _attach_trump_factors(
    records: list[dict[str, Any]],
    factors: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    attached = []
    for record in records:
        item = dict(record)
        key = (str(item.get("market", "")), str(item.get("symbol", "")))
        item["trump_factor"] = copy.deepcopy(factors.get(key, _zero_trump_factor()))
        attached.append(item)
    return attached


def _attach_trump_to_movements(
    movements: list[dict[str, Any]],
    posts: list[dict[str, Any]],
    *, include_evidence: bool = True,
) -> list[dict[str, Any]]:
    attached = []
    for movement in movements:
        item = dict(movement)
        factor = build_trump_factor(
            posts,
            market=str(item.get("market", "")),
            symbol=str(item.get("symbol", "")),
            name=str(item.get("name", "")),
            sector=str(item.get("sector", "")),
            include_evidence=include_evidence,
        )
        base_confidence = float(item.get("confidence") or 0.0)
        direction = 1.0 if float(item.get("change_pct") or 0.0) > 0 else -1.0
        alignment = direction * float(factor["score"])
        confidence_adjustment = _clamp(alignment * 0.12, -0.10, 0.12)
        if item.get("live_extended"):
            confidence_adjustment = 0.0
        item["base_confidence"] = round(base_confidence, 4)
        item["confidence"] = round(
            _clamp(base_confidence + confidence_adjustment, 0.0, 1.0),
            4,
        )
        item["trump_confidence_adjustment"] = round(confidence_adjustment, 4)
        item["trump_factor"] = factor
        attached.append(item)
    return attached


def _watch_news_factors(records: list[dict]) -> dict:
    grouped = {}
    for record in sorted(records, key=lambda row: str(row.get("published_at") or ""), reverse=True):
        metadata = record.get("analysis_metadata") or {}
        if (record.get("evidence_status") or metadata.get("evidence_status")) != "supported" or metadata.get("direction_eligible") is False:
            continue
        score = record.get("score")
        if not isinstance(score, (int, float)) or not math.isfinite(score) or abs(score) <= 0.04:
            continue
        if not (record.get("impact_reason") or record.get("analysis_summary")):
            continue
        key = (record.get("market"), _normalized_stock_symbol(record.get("symbol", "")))
        result = grouped.setdefault(key, {"bullish": [], "bearish": [], "bullish_count": 0, "bearish_count": 0})
        side = "bullish" if score > 0 else "bearish"
        result[side + "_count"] += 1
        if len(result[side]) < 3:
            result[side].append(record)
    return grouped


def _combine_sentiment_with_trump(
    sentiment: list[dict[str, object]],
    watchlist: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    values = {
        (str(item["market"]), str(item["symbol"])): dict(item)
        for item in sentiment
    }
    combined = []
    for watch in watchlist:
        key = (str(watch["market"]), str(watch["symbol"]))
        row = values.get(
            key,
            {
                "market": key[0],
                "symbol": key[1],
                "news_count": 0,
                "directional_count": 0,
                "bullish_count": 0,
                "bearish_count": 0,
                "average_score": 0.0,
                "latest_at": None,
            },
        )
        factor = copy.deepcopy(watch.get("trump_factor") or _zero_trump_factor())
        base_score = float(row.get("average_score") or 0.0)
        adjustment = float(factor["score"]) * 0.25
        row["base_average_score"] = round(base_score, 4)
        row["trump_adjustment"] = round(adjustment, 4)
        row["average_score"] = round(_clamp(base_score + adjustment, -1.0, 1.0), 4)
        row["trump_factor"] = factor
        combined.append(row)
    combined.sort(key=lambda item: abs(float(item["average_score"])), reverse=True)
    return combined


def _zero_trump_factor() -> dict[str, Any]:
    return {
        "score": 0.0,
        "direction": "neutral",
        "relevance": 0.0,
        "confidence": 0.0,
        "themes": [],
        "theme_labels": [],
        "post_count": 0,
        "direct_mention": False,
        "reason": "近 72 小时暂无与该标的相关的 Trump X 动态",
        "latest_post_id": "",
        "latest_post_url": "",
        "latest_post_at": "",
    }


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _deduplicate_records(records: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    positions: dict[tuple[str, str, str], int] = {}
    for record in records:
        title = re.sub(r"\s+-\s+[^-]{2,60}$", "", str(record.get("title", "")))
        title = re.sub(r"\s+", " ", title).strip().casefold()
        event_key = record.get("event_key") or news_event_key(str(record.get("market", "")), str(record.get("symbol", "")),
            str(record.get("title", "")), str(record.get("news_summary", "")), str(record.get("published_at", "")))
        key = (str(record.get("market", "")), str(record.get("symbol", "")), str(event_key or title))
        position = positions.get(key)
        if position is None:
            positions[key] = len(unique)
            unique.append(record)
        elif _link_rank(record.get("url")) > _link_rank(unique[position].get("url")):
            unique[position] = record
    return unique[:limit]


def _link_rank(value: object) -> int:
    url = str(value or "")
    if not url.startswith(("http://", "https://")):
        return 0
    return 1 if "news.google.com" in url else 2


def _serialize_run(
    summary: RunSummary,
    started_at: datetime,
    analyzer_message: str,
    offline: bool,
) -> dict[str, Any]:
    return {
        "started_at": iso_utc(started_at),
        "finished_at": iso_utc(utc_now()),
        "fetched": summary.fetched,
        "inserted": summary.inserted,
        "analyzed": summary.analyzed,
        "alert_count": len(summary.alerts),
        "errors": list(summary.errors),
        "analyzer": analyzer_message,
        "offline": offline,
    }


def _serialize_movement_run(
    summary: MovementRunSummary,
    analyzer_message: str,
) -> dict[str, Any]:
    return {
        "scan_id": summary.scan_id,
        "started_at": iso_utc(summary.started_at),
        "finished_at": iso_utc(summary.finished_at),
        "found": len(summary.movements),
        "direct_evidence": sum(1 for item in summary.movements if item.evidence_url.startswith(("http://", "https://"))),
        "market_counts": dict(summary.market_counts),
        "errors": list(summary.errors),
        "analyzer": analyzer_message,
    }


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows SO_REUSEADDR permits multiple live servers on one port, routing
    # requests to an old instance even after a new version reports startup.
    allow_reuse_address = os.name != "nt"
    # Cold page loads and multiple team clients burst beyond the default backlog of five.
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], app_state: AppState):
        super().__init__(address, DashboardHandler)
        self.app_state = app_state

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def _local_assistant(self) -> bool:
        return (type(self.server) is DashboardServer
                and self.server.server_address[0] in {"127.0.0.1", "::1"}
                and self.client_address[0] in {"127.0.0.1", "::1"})

    def _trusted_request(self) -> bool:
        try:
            host = urlparse("http://" + self.headers.get("Host", ""))
            allowed = {"127.0.0.1", "localhost", "::1", self.server.server_address[0]}
            origin = self.headers.get("Origin")
            valid = host.hostname in allowed and host.port == self.server.server_port
            if origin:
                parsed = urlparse(origin)
                valid = valid and parsed.scheme == "http" and parsed.netloc == host.netloc
        except ValueError:
            valid = False
        if not valid:
            self._send_json({"error": "不允许外部页面访问本机监测服务"}, status=HTTPStatus.FORBIDDEN)
        return valid

    def do_GET(self) -> None:
        if not self._trusted_request():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/assistant/"):
            if not self._local_assistant():
                self._send_json({"available": False, "error": "本地助手仅在桌面本机服务启用"}, status=HTTPStatus.FORBIDDEN)
                return
            try:
                if parsed.path == "/api/assistant/status":
                    self._send_json({"available": True, **self.server.app_state.assistant.status()})
                elif parsed.path.startswith("/api/assistant/jobs/"):
                    self._send_json(self.server.app_state.assistant.get(parsed.path.rsplit("/", 1)[1]))
                else:
                    self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path in {"/api/investment", "/api/investment/export"}:
            query = parse_qs(parsed.query)
            portfolio_id = (query.get("portfolio_id") or ["default"])[0]
            try:
                get_portfolio(self.server.app_state.config_store.read(), portfolio_id)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            rows = investment.plans(self.server.app_state.research_ledger, include_archived=True, portfolio_id=portfolio_id)
            if parsed.path.endswith("/export"):
                identifier = (query.get("id") or [""])[0]
                row = next((r for r in rows if r["plan"]["id"] == identifier), None)
                if row is None:
                    self._send_json({"error": "方案不存在"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"format": "stock-investment-plan", "plan": row["plan"]}, download_name="STock-investment-plan.json")
            else:
                self._send_json({"items": rows})
            return
        if parsed.path.startswith("/api/research"):
            try:
                state = self.server.app_state
                query = parse_qs(parsed.query)
                value = lambda key,default: (query.get(key) or [default])[0]
                if parsed.path == "/api/research/earnings/annotations":
                    result = state.earnings_calendar.annotations()
                elif parsed.path in {"/api/research/earnings", "/api/research/earnings/export"}:
                    result = state.earnings_calendar.query(search=value("search", ""), days=value("days", "30"),
                        scope=value("scope", "liquid"), sort=value("sort", "date"), include_unknown=value("include_unknown", "0") == "1",
                        offset=int(value("offset", 0)), limit=10000 if parsed.path.endswith("/export") else 100)
                    if parsed.path.endswith("/export"):
                        self._send_json(result, download_name="quantify-earnings-calendar.json")
                        return
                elif parsed.path == "/api/research/companies":
                    result = state.company_research()
                elif parsed.path == "/api/research/etf":
                    result = load_etf(state.research_ledger, value("symbol", ""))
                elif parsed.path == "/api/research/cards":
                    result = state.research_ledger.cards(search=value("search", ""), stance=value("stance", "all"), offset=int(value("offset", 0)))
                elif parsed.path == "/api/research/valuations":
                    result = state.research_ledger.valuations(offset=int(value("offset", 0)))
                elif parsed.path == "/api/research/inputs":
                    result = state.research_ledger.inputs(value("kind","all"),offset=int(value("offset",0)))
                    for item in result["items"]:
                        item["display"] = state.translator.decorate(item["payload"])
                elif parsed.path.startswith("/api/research/inputs/"):
                    result = state.research_ledger.input_detail(int(parsed.path.rsplit("/",1)[1]))
                    result["display"] = state.translator.decorate(result["payload"])
                elif parsed.path.startswith("/api/research/forecasts/"):
                    result = state.research_ledger.detail(parsed.path.rsplit("/",1)[1])
                    result["news_display"] = state.translator.decorate(result["inputs"].get("news", []))
                elif parsed.path in {"/api/research","/api/research/export"}:
                    if parsed.path.endswith("/export") and value("type","") == "inputs":
                        inputs = state.research_ledger.inputs(value("kind","all"),limit=200)
                        result = {"schema_version":1,"exported_at":iso_utc(utc_now()),"total_matching":inputs["total"],
                                  "exported_count":len(inputs["items"]),"truncated":inputs["total"]>len(inputs["items"]),
                                  "inputs":[state.research_ledger.input_detail(row["id"]) for row in inputs["items"]]}
                        self._send_json(result,download_name="quantify-input-versions.json")
                        return
                    filters = {key:value(key,"all") for key in ("asset","horizon","status")}
                    result = state.forward_monitor.snapshot(**filters,offset=int(value("offset",0)))
                    if parsed.path.endswith("/export"):
                        records,total = state.research_ledger.forecasts(**filters,limit=1000)
                        result = {"schema_version":1,"exported_at":iso_utc(utc_now()),"total_matching":total,
                                  "exported_count":len(records),"truncated":total>len(records),
                                  "records":[state.research_ledger.detail(row["id"]) for row in records]}
                        self._send_json(result,download_name="quantify-forward-records.json")
                        return
                else:
                    raise ValueError("记录接口不存在")
                self._send_json(result)
            except (ValueError,TypeError) as exc:
                self._send_json({"error":str(exc)},status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/social":
            query = parse_qs(parsed.query)
            try:
                self._send_json(self.server.app_state.translator.decorate(self.server.app_state.social_monitor.snapshot(
                    hours=int((query.get("hours") or [24])[0]),
                    platform=(query.get("platform") or ["all"])[0],
                    sample_scope=(query.get("sample_scope") or ["all"])[0],
                    sample_query=(query.get("sample_query") or [""])[0],
                )))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/portfolios":
            try:
                self._send_json(self.server.app_state.portfolio_directory())
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/portfolio/records":
            try:
                identifier = (parse_qs(parsed.query).get("portfolio_id") or ["default"])[0]
                self._send_json(self.server.app_state.config_store.portfolio_records(identifier))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/movements/investigation":
            query = parse_qs(parsed.query)
            try:
                self._send_json(self.server.app_state.movement_investigation.snapshot(
                    (query.get("market") or [""])[0], (query.get("symbol") or [""])[0],
                    (query.get("movement_signature") or [""])[0]))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path in {"/api/analysis-skills", "/api/briefing-skills"}:
            if not self._local_assistant():
                self._send_json({"error": "Skill 仅在本机管理"}, status=HTTPStatus.FORBIDDEN)
            else:
                from .analysis_skills import read_skills
                self._send_json(read_skills(self.server.app_state.config_store,
                                           'briefing' if parsed.path == '/api/briefing-skills' else 'analysis'))
            return
        if parsed.path == "/api/portfolio/instrument-analysis":
            try:
                identifier = (parse_qs(parsed.query).get("portfolio_id") or ["default"])[0]
                self._send_json(self.server.app_state.instrument_analysis.snapshot(identifier))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path in {"/api/portfolio/instrument-analysis.pdf", "/api/portfolio/instrument-analysis/portfolio.pdf"}:
            query = parse_qs(parsed.query)
            identifier = (query.get("portfolio_id") or ["default"])[0]
            analysis = self.server.app_state.instrument_analysis
            try:
                if parsed.path.endswith("/portfolio.pdf"):
                    filename, content = analysis.export_portfolio_pdf(identifier)
                else:
                    filename, content = analysis.export_pdf(identifier, (query.get("target") or [""])[0],
                                                            (query.get("generated_at") or [None])[0])
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except (OSError, RuntimeError, ImportError):
                self._send_json({"error": "AI 分析 PDF 暂时无法导出，请稍后重试"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_attachment(filename, content, "application/pdf", "STock-AI-analysis.pdf")
            return
        if parsed.path == "/api/portfolio/export.xlsx":
            try:
                identifier = (parse_qs(parsed.query).get("portfolio_id") or ["default"])[0]
                filename, content = self.server.app_state.export_portfolio(identifier)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except (OSError, RuntimeError):
                self._send_json({"error": "组合快照暂时无法导出，请稍后重试"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_xlsx(filename, content)
            return
        if parsed.path == "/api/watchlist/whitelist":
            self._send_json({"items": self.server.app_state.config_store.read().get("stock_whitelist", [])})
            return
        if parsed.path == "/api/dashboard":
            try:
                query = parse_qs(parsed.query)
                options = {"compact": True} if query.get("details") == ["0"] else {}
                self._send_json(self.server.app_state.dashboard((query.get("portfolio_id") or ["default"])[0], **options))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/portfolio/snapshot":
            try:
                identifier = (parse_qs(parsed.query).get("portfolio_id") or ["default"])[0]
                self._send_json(self.server.app_state.portfolio_snapshot(identifier))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path in {"/api/runtime", "/api/portfolio/details"}:
            try:
                query = parse_qs(parsed.query)
                identifier = (query.get("portfolio_id") or ["default"])[0]
                result = self.server.app_state.runtime_snapshot(identifier) if parsed.path == "/api/runtime" else self.server.app_state.portfolio_details(
                    identifier, (query.get("market") or [None])[0], (query.get("symbol") or [None])[0])
                self._send_json(result)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/news/instrument":
            try:
                query = parse_qs(parsed.query)
                self._send_json(self.server.app_state.instrument_news_snapshot(
                    (query.get('market') or [''])[0], (query.get('symbol') or [''])[0]))
            except ValueError as error:
                self._send_json({'error': str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/news":
            try:
                language = (parse_qs(parsed.query).get("language") or ["all"])[0]
                options = {"cached": True} if parse_qs(parsed.query).get("cached") == ["1"] else {}
                self._send_json(self.server.app_state.news_snapshot(language=language, **options))
            except ValueError as error:
                self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/health":
            self._send_json({"status": "ok", "build_id": self.server.app_state.build_id})
            return
        if parsed.path == "/api/features":
            self._send_json(self.server.app_state.feature_status())
            return
        if parsed.path == "/api/fundamentals":
            self._send_json(self.server.app_state.fundamental_monitor.snapshot())
            return
        if parsed.path == "/api/securities/resolve":
            from .security_master import resolve
            query = parse_qs(parsed.query)
            try:
                self._send_json(resolve((query.get("market") or [""])[0], (query.get("symbol") or [""])[0]))
            except (ValueError, OSError, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/portfolio/research":
            try:
                self._send_json(self.server.app_state.get_portfolio_research((parse_qs(parsed.query).get("portfolio_id") or ["default"])[0]).snapshot())
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/briefings/pdf":
            query = parse_qs(parsed.query)
            try:
                path = self.server.app_state.briefing_monitor.pdf_file((query.get("id") or [""])[0])
                content = path.read_bytes()
            except (KeyError, FileNotFoundError):
                self._send_json({"error": "PDF尚未生成或文件已移走，请重新导出"}, status=HTTPStatus.NOT_FOUND)
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except OSError:
                self._send_json({"error": "PDF暂时无法读取，请重新导出"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            disposition = "attachment" if query.get("download") == ["1"] else "inline"
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Disposition", f"{disposition}; filename=\"STock-briefing.pdf\"; filename*=UTF-8''{quote(path.name, safe='')}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            return
        if parsed.path in {"/api/briefings", "/api/briefings/report"}:
            try:
                query = parse_qs(parsed.query)
                monitor = self.server.app_state.briefing_monitor
                result = monitor.get((query.get("id") or [""])[0]) if parsed.path.endswith("/report") else monitor.snapshot(limit=100)
                self._send_json(result)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/portfolio/backtests":
            from .backtest_archive import read
            try:
                query = parse_qs(parsed.query)
                research = self.server.app_state.get_portfolio_research((query.get("portfolio_id") or ["default"])[0])
                self._send_json(read(research, (query.get("id") or [None])[0]))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/assets":
            self._send_json(self.server.app_state.assets_snapshot())
            return
        if parsed.path == "/api/indices":
            self._send_json(self.server.app_state.index_monitor.snapshot())
            return
        if parsed.path == "/api/funds/search":
            query = (parse_qs(parsed.query).get("q") or [""])[0]
            try:
                self._send_json(self.server.app_state.search_funds(query))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except (OSError, RuntimeError, json.JSONDecodeError) as exc:
                self._send_json(
                    {"error": f"基金检索暂时不可用：{exc}"},
                    status=HTTPStatus.BAD_GATEWAY,
                )
            return
        if parsed.path == '/api/short-term/catalog':
            self._send_json({'models':MODELS,'strategies':STRATEGIES,'benchmarks':DEFAULT_BENCHMARKS})
            return
        if parsed.path in {'/api/short-term/research', '/api/short-term/record'}:
            query = parse_qs(parsed.query)
            try:
                research = self.server.app_state.minute_research
                if parsed.path.endswith('/record'):
                    result = research.record((query.get('id') or [''])[0])
                else:
                    result = research.snapshot((query.get('market') or [''])[0], (query.get('symbol') or [''])[0])
                self._send_json(result)
            except ValueError as exc:
                self._send_json({'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/short-term":
            query = parse_qs(parsed.query, keep_blank_values=True)
            market = (query.get("market") or [""])[0].strip().upper()
            symbol = (query.get("symbol") or [""])[0].strip().upper()
            mode = (query.get("mode") or ["daily"])[0]
            try:
                if market not in {"US", "CN", "HK", "KR"} or mode not in {"daily", "intraday", "forecast"}:
                    raise ValueError("不支持的研判市场或周期")
                duration = {}
                for unit in ('hours', 'minutes'):
                    if unit in query:
                        try:
                            duration[unit] = int(query[unit][0])
                        except ValueError:
                            raise ValueError("预测时长须为整数小时或整数分钟") from None
                if mode == 'forecast':
                    forecast_duration_minutes(**duration)
                chart = self.server.app_state.portfolio_chart(market, symbol, "1d", "intraday")
                self._send_json(analyze_short_term(chart, market, symbol, mode, **duration))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except (OSError, RuntimeError, json.JSONDecodeError) as exc:
                self._send_json({"error": f"研判行情暂不可用：{exc}"}, status=HTTPStatus.BAD_GATEWAY)
            return
        if parsed.path == "/api/portfolio/chart":
            query = parse_qs(parsed.query, keep_blank_values=True)
            market = (query.get("market") or [""])[0]
            symbol = (query.get("symbol") or [""])[0]
            period = (query.get("period") or [None])[0]
            mode = (query.get("mode") or ["daily"])[0]
            interval = (query.get("interval") or [None])[0]
            try:
                self._send_json(
                    self.server.app_state.portfolio_chart(
                        market, symbol, period, mode, **({"interval": interval} if interval is not None else {})
                    )
                )
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except (OSError, RuntimeError, json.JSONDecodeError) as exc:
                self._send_json(
                    {"error": f"历史行情暂时不可用：{exc}",
                     "retry_after_seconds": getattr(exc, "retry_after_seconds", 20)},
                    status=HTTPStatus.BAD_GATEWAY,
                )
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        if not self._trusted_request():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/assistant/"):
            if not self._local_assistant():
                self._send_json({"error": "本地助手不向远程客户端开放"}, status=HTTPStatus.FORBIDDEN)
                return
            try:
                payload = self._read_json(max_length=12_000)
                if parsed.path == "/api/assistant/ask":
                    self._send_json(self.server.app_state.assistant.submit(payload), status=HTTPStatus.ACCEPTED)
                elif parsed.path == "/api/assistant/cancel":
                    self._send_json(self.server.app_state.assistant.cancel(str(payload.get("id", ""))))
                else:
                    self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = self._read_json(max_length=1_400_000 if parsed.path in {"/api/research/etf/import", "/api/portfolio/analysis.xlsx"} or parsed.path.startswith("/api/investment/") else 550_000 if parsed.path == "/api/portfolio/research" else 64_000)
            if parsed.path in {"/api/portfolio/instrument-analysis.pdf", "/api/portfolio/instrument-analysis/portfolio.pdf"}:
                if not self._local_assistant():
                    self._send_json({"error": "标的模型分析仅在本机使用"}, status=HTTPStatus.FORBIDDEN)
                    return
                identifier = payload.get("portfolio_id")
                if not isinstance(identifier, str):
                    raise ValueError("请选择要导出的持仓组合")
                try:
                    analysis = self.server.app_state.instrument_analysis
                    if parsed.path.endswith("/portfolio.pdf"):
                        filename, content = analysis.export_fresh_portfolio_pdf(identifier)
                    else:
                        filename, content = analysis.export_fresh_pdf(identifier, payload.get("target"))
                except (OSError, RuntimeError, ImportError):
                    self._send_json({"error": "本轮分析暂时无法导出，请稍后重试"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._send_attachment(filename, content, "application/pdf", "STock-AI-analysis.pdf")
                return
            if parsed.path == "/api/portfolio/analysis.xlsx":
                from .portfolio_analysis_export import build_analysis_workbook
                identifier = payload.get("portfolio_id")
                requested = (parse_qs(parsed.query).get("portfolio_id") or [identifier])[0]
                if not isinstance(identifier, str) or requested != identifier:
                    raise ValueError("导出组合编号不一致，请重新导出")
                account = get_portfolio(self.server.app_state.config_store.read(), identifier)
                filename, content = build_analysis_workbook(account, payload, captured_at=utc_now())
                self._send_xlsx(filename, content)
                return
            if parsed.path in {"/api/briefings/settings", "/api/briefings/run", "/api/briefings/export"}:
                monitor = self.server.app_state.briefing_monitor
                if parsed.path.endswith("/settings"):
                    self._send_json(monitor.update_settings(payload))
                elif parsed.path.endswith("/export"):
                    try:
                        result = monitor.export_pdf(payload.get("id", ""))
                    except KeyError:
                        self._send_json({"error": "报告不存在"}, status=HTTPStatus.NOT_FOUND)
                        return
                    self._send_json(result, status=HTTPStatus.ACCEPTED if result.get("accepted") else HTTPStatus.OK)
                else:
                    result = monitor.trigger(payload.get("market", "GLOBAL"), payload.get("kind"), payload.get("date"), refresh=payload.get("refresh", False))
                    self._send_json(result, status=HTTPStatus.ACCEPTED if result.get("accepted") else HTTPStatus.OK)
                return
            if parsed.path.startswith('/api/short-term/'):
                research = self.server.app_state.minute_research
                if parsed.path == '/api/short-term/research/settings':
                    self._send_json(research.configure(payload))
                    return
                market, symbol = target_key(payload.get('market',''), payload.get('symbol',''))
                if parsed.path in {'/api/short-term/predict','/api/short-term/strategies'}:
                    duration = {unit: payload[unit] for unit in ('hours', 'minutes') if unit in payload}
                    minutes = forecast_duration_minutes(**duration)
                    if payload.get('model_id') not in (None,'auto') and payload['model_id'] not in MODELS:
                        raise ValueError('未知预测策略')
                    try:
                        chart = self.server.app_state.portfolio_chart(market,symbol,'1d','intraday')
                    except (RuntimeError,OSError) as exc:
                        self._send_json({'error':f'分钟行情暂不可用：{exc}'},status=HTTPStatus.BAD_GATEWAY)
                        return
                    result=research.inspect_strategies(market,symbol,chart) if parsed.path.endswith('/strategies') else research.issue(market,symbol,chart,minutes=minutes,model_id=payload.get('model_id'))
                    self._send_json(result)
                elif parsed.path == '/api/short-term/research/watch':
                    research.register(market,symbol,payload.get('enabled',True))
                    self._send_json(research.snapshot(market,symbol))
                elif parsed.path == '/api/short-term/research/run':
                    research.register(market,symbol)
                    self._send_json({'accepted':research.trigger()},status=HTTPStatus.ACCEPTED)
                elif parsed.path == '/api/short-term/research/rollback':
                    research.rollback(market,symbol,payload.get('horizon',30))
                    self._send_json(research.snapshot(market,symbol))
                else:
                    self._send_json({'error':'接口不存在'},status=HTTPStatus.NOT_FOUND)
                return
            if parsed.path.startswith(("/api/portfolio/", "/api/investment/")):
                portfolio_id = (parse_qs(parsed.query).get("portfolio_id") or [payload.get("portfolio_id", "default")])[0]
                get_portfolio(self.server.app_state.config_store.read(), portfolio_id)
                payload["portfolio_id"] = portfolio_id
            if parsed.path == "/api/indices/settings":
                self._send_json(self.server.app_state.index_monitor.configure(payload.get("selected_ids")))
                return
            if parsed.path == "/api/portfolios":
                result = self.server.app_state.config_store.save_portfolio(payload.get("name"), payload.get("id"))
                self._send_json(result)
                return
            if parsed.path in {"/api/investment/preview", "/api/investment/import", "/api/investment/save"}:
                ledger = self.server.app_state.research_ledger
                if parsed.path.endswith("/preview"):
                    plan, warnings = investment.parse_payload(payload)
                    result = {"plan": plan, "warnings": warnings}
                elif parsed.path.endswith("/import"):
                    result = investment.import_plan(ledger, payload, portfolio_id)
                else:
                    result = investment.save(ledger, payload, portfolio_id)
                self._send_json(result)
                return
            if parsed.path == "/api/research/earnings/refresh":
                accepted = self.server.app_state.earnings_calendar.trigger_refresh()
                self._send_json({"accepted": accepted, "runtime": self.server.app_state.earnings_calendar.runtime()},
                                status=HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT)
                return
            if parsed.path == "/api/research/etf/import":
                self._send_json(import_payload(self.server.app_state.research_ledger, payload), status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/features/restore":
                self._send_json(self.server.app_state.restore_features())
                return
            if parsed.path == "/api/fundamentals/refresh":
                accepted = self.server.app_state.fundamental_monitor.trigger_refresh()
                self._send_json({"accepted": accepted}, status=HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT)
                return
            if parsed.path == "/api/research/cards":
                result = self.server.app_state.research_ledger.save_card(payload)
                self._send_json(result, status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/research/valuations":
                result = self.server.app_state.research_ledger.save_valuation(payload)
                self._send_json(result, status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/research/settings":
                self.server.app_state.forward_monitor.configure(payload)
                self._send_json({"ok":True})
                return
            if parsed.path == "/api/social/refresh":
                accepted = self.server.app_state.social_monitor.trigger_refresh(payload.get("platform"), manual=True)
                result = {"accepted": True} if accepted else {"accepted": False, "error": "采集任务正在运行，请等待本轮完成"}
                self._send_json(result, status=HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT)
                return
            if parsed.path == "/api/social/settings":
                self.server.app_state.social_monitor.configure(payload)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/social/import":
                result = self.server.app_state.social_monitor.import_posts(payload)
                self._send_json(result, status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/scan":
                accepted = self.server.app_state.trigger_scan(
                    offline=bool(payload.get("offline", False))
                )
                status = HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT
                self._send_json({"accepted": accepted}, status=status)
                return
            if parsed.path == "/api/movements/scan":
                accepted = self.server.app_state.trigger_movement_scan()
                status = HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT
                self._send_json({"accepted": accepted}, status=status)
                return
            if parsed.path == "/api/movements/investigation":
                if not self._local_assistant():
                    self._send_json({"error": "异动模型分析仅在本机使用"}, status=HTTPStatus.FORBIDDEN)
                    return
                result = self.server.app_state.movement_investigation.request(
                    payload.get("market", ""), payload.get("symbol", ""), force=payload.get("force") is True)
                self._send_json(result, status=HTTPStatus.ACCEPTED if result["status"] == "running" else HTTPStatus.OK)
                return
            if parsed.path == "/api/assets/refresh":
                accepted = self.server.app_state.trigger_asset_refresh()
                status = HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT
                self._send_json({"accepted": accepted}, status=status)
                return
            if parsed.path == "/api/assets/notifications":
                enabled = self.server.app_state.set_asset_system_notifications(
                    bool(payload.get("enabled", False))
                )
                self._send_json({"ok": True, "enabled": enabled})
                return
            if parsed.path == "/api/assets/alert-test":
                sent, error = self.server.app_state.test_asset_system_notification()
                status = HTTPStatus.OK if sent else HTTPStatus.SERVICE_UNAVAILABLE
                self._send_json({"ok": sent, "error": error}, status=status)
                return
            if parsed.path == "/api/trump/refresh":
                accepted = self.server.app_state.trigger_trump_refresh()
                status = HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT
                self._send_json({"accepted": accepted}, status=status)
                return
            if parsed.path == "/api/trump/token":
                self.server.app_state.update_x_token(payload)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/analysis/credential":
                self._send_json(self.server.app_state.update_analysis_credential(payload))
                return
            if parsed.path == "/api/auto":
                self.server.app_state.set_auto(bool(payload.get("enabled", False)))
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/watchlist/whitelist":
                if payload.get("action", "add") == "add":
                    from .security_master import resolve
                    try:
                        identity = resolve(str(payload.get("market", "")), str(payload.get("symbol", "")))
                    except RuntimeError as exc:
                        raise ValueError(f"证券识别暂不可用：{exc}") from exc
                    payload = {**payload, "market": identity["market"], "symbol": identity["symbol"], "name": identity["name"]}
                item = self.server.app_state.config_store.save_stock_whitelist(payload)
                self._send_json({"item": item})
                return
            if parsed.path == "/api/watchlist":
                item = self.server.app_state.add_watch_item(payload)
                self._send_json({"item": item}, status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/watchlist/replace":
                from .security_master import resolve
                previous = payload.get("previous")
                if not isinstance(previous, dict):
                    raise ValueError("缺少原股票标识")
                try:
                    identity = resolve(str(payload.get("market", "")), str(payload.get("symbol", "")))
                except RuntimeError as exc:
                    raise ValueError(f"证券识别暂不可用：{exc}") from exc
                item = self.server.app_state.config_store.add_watch_item(identity, replace=(
                    str(previous.get("market", "")).upper(), str(previous.get("symbol", "")).upper()))
                self._send_json({"item": item})
                return
            if parsed.path == "/api/portfolio/positions":
                item = self.server.app_state.add_position(payload)
                self._send_json({"item": item}, status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/portfolio/principal":
                amount = self.server.app_state.config_store.set_portfolio_principal(payload.get("principal_cny"), portfolio_id)
                self._send_json({"principal_cny": amount})
                return
            if parsed.path == "/api/portfolio/dividends":
                self._send_json({"item": self.server.app_state.config_store.save_dividend(payload)})
                return
            if parsed.path == "/api/portfolio/trades":
                self._send_json({"item": self.server.app_state.config_store.add_historical_trade(payload)}, status=HTTPStatus.CREATED)
                self.server.app_state.trigger_portfolio_refresh()
                return
            if parsed.path in {"/api/analysis-skills", "/api/briefing-skills"}:
                if not self._local_assistant():
                    self._send_json({"error": "Skill 仅在本机管理"}, status=HTTPStatus.FORBIDDEN)
                else:
                    from .analysis_skills import save_skills
                    self._send_json(save_skills(self.server.app_state.config_store, payload,
                                               'briefing' if parsed.path == '/api/briefing-skills' else 'analysis'))
                return
            if parsed.path == "/api/portfolio/records":
                self._send_json(self.server.app_state.config_store.save_portfolio_records(payload))
                return
            if parsed.path == "/api/portfolio/pending":
                self._send_json(self.server.app_state.config_store.save_pending_position(payload))
                return
            if parsed.path == "/api/portfolio/instrument-analysis":
                if not self._local_assistant():
                    self._send_json({"error": "标的模型分析仅在本机使用"}, status=HTTPStatus.FORBIDDEN)
                else:
                    self._send_json(self.server.app_state.instrument_analysis.request(portfolio_id, payload))
                return
            if parsed.path == "/api/portfolio/account":
                result = self.server.app_state.config_store.set_portfolio_account(payload)
                self.server.app_state.trigger_portfolio_refresh()
                self._send_json(result)
                return
            if parsed.path == "/api/portfolio/identity":
                result = self.server.app_state.config_store.correct_position_identity(payload)
                self.server.app_state.trigger_portfolio_refresh()
                self._send_json(result)
                return
            if parsed.path == "/api/portfolio/research":
                research = self.server.app_state.get_portfolio_research(portfolio_id)
                action = payload.get("action")
                if action == "refresh":
                    self._send_json({"started": research.refresh(payload.get("target"))})
                elif action == "refresh_performance":
                    self._send_json({"started": research.refresh_performance(payload.get("year"))})
                elif action == "prepare_analysis_export":
                    self._send_json(research.prepare_analysis_export(payload.get("year")))
                elif action == "import":
                    self._send_json({"id": research.import_data(payload.get("data"))})
                elif action == "freeze":
                    self._send_json(research.freeze(payload))
                elif action == "save_backtest":
                    from .backtest_archive import save
                    self._send_json(save(research, payload))
                else:
                    raise ValueError("研究操作无效")
                return
            if parsed.path == "/api/portfolio/adjust":
                item = self.server.app_state.adjust_position(payload)
                self._send_json({"item": item, "closed": item is None})
                return
            if parsed.path == "/api/portfolio/risk":
                item = self.server.app_state.set_position_risk(payload)
                self._send_json({"item": item})
                return
            if parsed.path == "/api/portfolio/plan":
                self._send_json({"item": self.server.app_state.config_store.set_position_plan(payload)})
                return
            if parsed.path == "/api/portfolio/refresh":
                self.server.app_state.trigger_portfolio_refresh()
                self._send_json({"accepted": True}, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/settings":
                self.server.app_state.update_settings(payload)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/inbox":
                self.server.app_state.add_manual_news(payload)
                self.server.app_state.trigger_scan(offline=True)
                self._send_json({"ok": True}, status=HTTPStatus.CREATED)
                return
            self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        if not self._trusted_request():
            return
        parsed = urlparse(self.path)
        parts = [unquote(value) for value in parsed.path.split("/") if value]
        try:
            portfolio_id = (parse_qs(parsed.query).get("portfolio_id") or ["default"])[0]
            if len(parts) == 3 and parts[:2] == ["api", "portfolios"]:
                if self._read_json().get("confirmed") is not True:
                    raise ValueError("删除组合需要明确确认")
                get_portfolio(self.server.app_state.config_store.read(), parts[2])
                research = self.server.app_state._portfolio_researches.get(parts[2])
                if research and research.busy:
                    raise ValueError("组合研究正在更新，不能删除")
                with self.server.app_state.research_ledger.session() as db:
                    if db.execute("SELECT 1 FROM input_versions WHERE kind LIKE ? LIMIT 1", ("%:" + parts[2],)).fetchone():
                        raise ValueError("组合仍有配置计划或研究记录，请保留该组合")
                self.server.app_state.config_store.delete_portfolio(parts[2])
                research = self.server.app_state._portfolio_researches.pop(parts[2], None)
                if research:
                    research.shutdown()
                self.server.app_state.trigger_portfolio_refresh()
                self._send_json({"ok": True})
                return
            if len(parts) == 4 and parts[:3] == ["api", "portfolio", "dividends"]:
                if self._read_json().get("confirmed") is not True:
                    raise ValueError("删除股息记录需要明确确认")
                self.server.app_state.config_store.delete_dividend(parts[3], portfolio_id)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/portfolio/positions":
                if self._read_json().get("confirmed") is not True:
                    raise ValueError("清空全部持仓需要明确确认")
                removed = self.server.app_state.clear_positions(portfolio_id)
                self._send_json({"ok": True, "removed_count": removed})
                return
            if len(parts) == 5 and parts[:3] == ["api", "watchlist", "whitelist"]:
                removed = self.server.app_state.config_store.remove_stock_whitelist(parts[3], parts[4])
                self._send_json({"ok": True} if removed else {"error": "白名单记录不存在"},
                                status=HTTPStatus.OK if removed else HTTPStatus.NOT_FOUND)
                return
            if len(parts) == 4 and parts[:2] == ["api", "watchlist"]:
                removed = self.server.app_state.remove_watch_item(parts[2], parts[3])
                if not removed:
                    self._send_json({"error": "股票不存在"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"ok": True})
                return
            if len(parts) == 5 and parts[:3] == ["api", "portfolio", "positions"]:
                removed = self.server.app_state.remove_position(parts[3], parts[4], portfolio_id)
                if not removed:
                    self._send_json({"error": "持仓不存在"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"ok": True})
                return
            self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

        except OSError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _send_xlsx(self, filename: str, content: bytes) -> None:
        from .portfolio_export import MIME_TYPE

        self._send_attachment(filename, content, MIME_TYPE, "STock-portfolio.xlsx")

    def _send_attachment(self, filename: str, content: bytes, mime_type: str, fallback: str) -> None:
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self, *, max_length: int = 64_000) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > max_length:
            raise ValueError("请求内容过大")
        if length == 0:
            return {}
        if self.headers.get_content_type() != "application/json":
            raise ValueError("请求内容类型须为 application/json")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求必须是 JSON 对象")
        return payload

    def _send_json(self, data: Any, *, status: HTTPStatus = HTTPStatus.OK, download_name: str | None = None) -> None:
        if self.command in {"POST", "PUT", "PATCH", "DELETE"} and status < 400:
            invalidate = getattr(self.server.app_state, "invalidate_read_snapshots", None)
            if invalidate:
                invalidate()
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        compressed = False
        for coding in self.headers.get("Accept-Encoding", "").split(","):
            parts = coding.strip().split(";")
            if parts[0].strip().lower() != "gzip":
                continue
            try:
                quality = next((float(part.split("=", 1)[1]) for part in parts[1:] if part.strip().startswith("q=")), 1)
            except (ValueError, IndexError):
                quality = 0
            if 0 < quality <= 1 and len(payload) >= 4096:
                packed = gzip.compress(payload, compresslevel=1)
                if len(packed) < len(payload):
                    payload, compressed = packed, True
            break
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        if download_name:
            self.send_header("Content-Disposition",f'attachment; filename="{download_name}"')
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static(self, request_path: str) -> None:
        routes = {
            "/": "index.html",
            "/index.html": "index.html",
            "/styles.css": "styles.css",
            "/app.js": "app.js",
            "/watchlist.js": "watchlist.js",
            "/chart_tools.js": "chart_tools.js",
            "/chart_tools.css": "chart_tools.css",
            "/startup_memory.js": "startup_memory.js",
            "/social.js": "social.js",
            "/research.js": "research.js",
            "/earnings.js": "earnings.js",
            "/workbench.js": "workbench.js",
            "/workbench.css": "workbench.css",
            "/briefing.js": "briefing.js",
            "/briefing.css": "briefing.css",
            "/analysis_skills.js": "analysis_skills.js",
            "/analysis_skills.css": "analysis_skills.css",
            "/annotations.js": "annotations.js",
            "/annotations.css": "annotations.css",
            "/indices.js": "indices.js",
            "/indices.css": "indices.css",
            "/investment_core.js": "investment_core.js",
            "/investment.js": "investment.js",
            "/valuation_core.js": "valuation_core.js",
            "/valuation.js": "valuation.js",
            "/valuation.css": "valuation.css",
            "/short_term.js": "short_term.js",
            "/short_term.css": "short_term.css",
            "/portfolio_research.js": "portfolio_research.js",
            "/movement_investigation.js": "movement_investigation.js",
            "/portfolio_research_core.js": "portfolio_research_core.js",
            "/portfolio_research.css": "portfolio_research.css",
            "/portfolio_workspace.js": "portfolio_workspace.js",
            "/portfolio_pending.js": "portfolio_pending.js",
            "/portfolio_records.js": "portfolio_records.js",
            "/portfolio_workspace.css": "portfolio_workspace.css",
            "/portfolio_trades.js": "portfolio_trades.js",
            "/portfolio_trades.css": "portfolio_trades.css",
            "/portfolio_dividends.js": "portfolio_dividends.js",
            "/portfolio_dividends.css": "portfolio_dividends.css",
            "/backtest_core.js": "backtest_core.js",
            "/backtest.js": "backtest.js",
            "/backtest_learning.js": "backtest_learning.js",
            "/assistant.js": "assistant.js",
            "/assistant.css": "assistant.css",
            "/backtest.css": "backtest.css",
            "/portfolio_books.js": "portfolio_books.js",
            "/portfolio_books.css": "portfolio_books.css",
            "/investment.css": "investment.css",
            "/logo.svg": "logo.svg",
            "/trash.svg": "trash.svg",
            "/icons.svg": "icons.svg",
            "/favicon.ico": "logo.svg",
        }
        filename = routes.get(request_path)
        if filename is None:
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return
        path = UI_DIR / filename
        try:
            payload = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", STATIC_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="股票新闻监测可视化软件")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        state = AppState(args.config)
        server = DashboardServer((args.host, args.port), state)
    except (OSError, ValueError) as exc:
        print(f"启动失败: {exc}")
        return 2

    url = f"http://{args.host}:{server.server_port}"
    print(f"股票新闻监测软件已启动: {url}")
    print("关闭此窗口或按 Ctrl+C 停止。")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n软件已停止。")
    finally:
        state.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
