from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
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
from urllib.parse import parse_qs, unquote, urlparse

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
from .startup_cache import SnapshotStore
from .us_sessions import build_extended_monitor
from .portfolio_chart import CHART_MODES, DEFAULT_PERIODS, chart_session_status, fetch_portfolio_chart
from .runtime import build_sources
from .storage import NewsStore
from .news_translation import NewsTranslator, is_chinese_headline
from .stock_mentions import matches_stock
from .social_sentiment import SocialSentimentMonitor
from .research_ledger import ResearchLedger
from .etf_research import import_payload
from .company_landscape import load_company_landscape, seed_company_research
from .fundamentals import FundamentalMonitor
from .earnings_calendar import EarningsCalendar
from .forward_monitor import ForwardMonitor
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
    def __init__(
        self,
        config_path: str | Path,
        *,
        start_asset_monitor: bool = True,
    ):
        self.config_path = Path(config_path).resolve()
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
        self.index_monitor = IndexMonitor(snapshot_store=self.snapshot_store)
        self.portfolio_monitor = PortfolioMonitor(str(self.config_path), snapshot_store=self.snapshot_store)
        self.social_monitor = SocialSentimentMonitor(self.config_path,ledger=self.research_ledger, trump_snapshot=self.trump_monitor.snapshot)
        self.forward_monitor = ForwardMonitor(self.config_path,self.research_ledger,lambda:self.assets_snapshot(for_research=True))
        self.fundamental_monitor = FundamentalMonitor(self.config_path, self.research_ledger, self.company_landscape["companies"])
        self.earnings_calendar = EarningsCalendar(load_config(self.config_path).storage.database, self.config_store)
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
            self.fundamental_monitor.start()
            self.earnings_calendar.start()

    def dashboard(self) -> dict[str, Any]:
        news_as_of = iso_utc(utc_now())
        config = load_config(self.config_path)
        raw = self.config_store.read()
        portfolio_positions = list((raw.get("portfolio") or {}).get("positions") or [])
        movement_scope = _build_movement_scope(portfolio_positions, mode=raw.get("movement_scope", "portfolio"))
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
        stats = store.dashboard_stats(
            hours=config.lookback_hours,
            threshold=config.alert_threshold,
        )
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
            for item in store.recent_news(10000)
            if _record_is_watched(item, watched_keys)
            and parse_datetime(str(item.get("published_at") or "")) >= cutoff
            and parse_datetime(str(item.get("published_at") or "")) <= utc_now() + timedelta(minutes=5)
            and relevant_record(item)
        ]
        scoped_news = _deduplicate_records(scoped_news, limit=10000)
        scoped_alerts = [
            item
            for item in store.recent_alerts(10000)
            if _record_is_watched(item, watched_keys)
            and parse_datetime(str(item.get("alert_created_at") or "")) >= cutoff
            and parse_datetime(str(item.get("published_at") or "")) >= cutoff
            and abs(float(item.get("score") or 0)) >= config.alert_threshold
            and relevant_record(item)
        ]
        scoped_alerts = _deduplicate_records(scoped_alerts, limit=10000)
        direction_threshold = 0.04
        stats.update(
            {
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
        )
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
        portfolio = build_portfolio_analysis(
            portfolio_positions,
            self.portfolio_monitor.snapshot(),
            scoped_news,
            watchlist,
            principal_cny=(raw.get("portfolio") or {}).get("principal_cny"),
        )
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
        movements = _attach_trump_to_movements(scoped_movements + extended_monitor["events"], trump_posts)
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
            "translation": self.translator.status(),
            "alerts": self.translator.decorate(alerts),
            "movements": self.translator.decorate(movements),
            "movement_stats": movement_stats,
            "extended_monitor": extended_monitor,
            "movement_scope": movement_scope,
            "sentiment_by_symbol": sentiment,
            "watchlist": watchlist,
            "korea_quotes": korea_quotes,
            "portfolio": portfolio,
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
                "api_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
                "x_api_configured": bool(trump["runtime"]["configured"]),
                "x_api_credential_source": trump["runtime"]["credential_source"],
                "auto_monitor_enabled": bool(raw.get("auto_monitor_enabled", False)),
            },
            "runtime": runtime,
        }

    def news_snapshot(self, *, language: str = "all") -> dict[str, Any]:
        if language not in {"zh", "all"}:
            raise ValueError("不支持的新闻语言")
        generated_at = iso_utc(utc_now())
        config = load_config(self.config_path)
        now = utc_now()
        watches = {(w.market, _normalized_stock_symbol(w.symbol)): w for w in config.watchlist}
        records = []
        source_language = "all" if self.translator.enabled else language
        for item in NewsStore(config.storage.database).recent_news(400, language=source_language):
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
        factors = {(w.market, w.symbol): build_trump_factor(posts, market=w.market, symbol=w.symbol, name=w.name, aliases=w.aliases)
                   for w in config.watchlist}
        translated = self.translator.decorate(_attach_trump_factors(news, factors))
        if language == "zh":
            translated = [item for item in translated if is_chinese_headline(str(item.get("title", "")), str(item.get("source", "")))
                          or item.get("title_translation_status") == "translated"]
        return {"generated_at": generated_at, "language": language, "news": translated,
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

    def remove_position(self, market: str, symbol: str) -> bool:
        removed = self.config_store.remove_position(market, symbol)
        if removed:
            self.portfolio_monitor.trigger_refresh()
        return removed

    def trigger_portfolio_refresh(self) -> None:
        self.portfolio_monitor.trigger_refresh()

    def clear_positions(self) -> int:
        removed = self.config_store.clear_positions()
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
        self,
        market: str,
        symbol: str,
        period: str | None,
        mode: str = "daily",
    ) -> dict[str, Any]:
        market, symbol, mode = market.strip().upper(), symbol.strip().upper(), mode.strip().lower()
        key = (market, symbol, mode, (period or DEFAULT_PERIODS.get(mode) or "").strip().lower())
        with self._lock:
            cached = self._chart_cache.get(key)
        if cached and time.monotonic() - cached[0] < (60 if mode == "intraday" else 300):
            return self._chart_response(cached[1], market, symbol)
        try:
            result = fetch_portfolio_chart(market, symbol, period, mode=mode)
        except (OSError, RuntimeError):
            if not cached:
                raise
            return self._chart_response(cached[1], market, symbol, retained=True)
        with self._lock:
            self._chart_cache.pop(key, None)
            self._chart_cache[key] = (time.monotonic(), copy.deepcopy(result))
            while len(self._chart_cache) > 16:
                self._chart_cache.pop(next(iter(self._chart_cache)))
            self.snapshot_store.save("charts", {"entries": [{"key": list(k), "data": value[1]} for k, value in self._chart_cache.items()]}, force=True)
        return self._chart_response(result, market, symbol)

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
            fields = ("nav",) if data.get("kind") == "nav" else ("timestamp", "open", "high", "low", "close")
            if not all(all(isinstance(row.get(field), (float, int)) and math.isfinite(row[field]) and row[field] > 0 for field in fields) for row in rows):
                continue
            if data.get("kind") != "nav" and any(row["high"] < max(row["open"], row["close"], row["low"]) or row["low"] > min(row["open"], row["close"]) or row["timestamp"] > time.time() + 60 for row in rows):
                continue
            self._chart_cache[tuple(key)] = (-float("inf"), data)

    @staticmethod
    def _chart_response(data: dict, market: str, symbol: str, *, retained: bool = False) -> dict:
        result = copy.deepcopy(data)
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
            {"id": "portfolio", "name": "持仓与盘前盘后", "state": "ready" if (raw.get("portfolio") or {}).get("positions") else "empty",
             "detail": "随录入持仓启用；不会恢复已删除仓位"},
            {"id": "social", "name": "公开社交情绪", "state": "running" if social["runtime"]["enabled"] else "disabled",
             "detail": f"已连接 {social['runtime']['web_connected_count']} 个公开源 · 24小时明确观点 {social['summary']['web_directional_count']} 条"},
            {"id": "trump", "name": "Trump 公开信息与政策因子", "state": "running" if trump["connected"] else "error" if trump["errors"] else "waiting" if trump.get("public_enabled") else "error" if trump["configured"] else "blocked",
             "detail": trump["message"]},
            {"id": "model", "name": "大模型分析", "state": "ready" if os.environ.get("OPENAI_API_KEY") else "blocked",
             "detail": "已有密钥，实际分析引擎以采集结果为准" if os.environ.get("OPENAI_API_KEY") else "缺少 OPENAI_API_KEY；本地规则仍在运行，不冒充模型结果"},
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

    def shutdown(self) -> None:
        self._stop_event.set()
        self.translator.shutdown()
        self.forward_monitor.shutdown()
        self.asset_monitor.shutdown()
        self.trump_monitor.shutdown()
        self.hynix_adr_monitor.shutdown()
        self.korea_stock_monitor.shutdown()
        self.index_monitor.shutdown()
        self.portfolio_monitor.shutdown()
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
        movement_scope = _build_movement_scope(list((raw.get("portfolio") or {}).get("positions") or []), mode=raw.get("movement_scope", "portfolio"))
        try:
            config = load_config(self.config_path)
            analyzer, analyzer_message = select_analyzer(
                config.analysis.mode,
                config.analysis.model,
                watchlist=config.watchlist,
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

    def __init__(self, address: tuple[str, int], app_state: AppState):
        super().__init__(address, DashboardHandler)
        self.app_state = app_state


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

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
                elif parsed.path == "/api/research/cards":
                    result = state.research_ledger.cards(search=value("search", ""), stance=value("stance", "all"), offset=int(value("offset", 0)))
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
        if parsed.path == "/api/dashboard":
            self._send_json(self.server.app_state.dashboard())
            return
        if parsed.path == "/api/news":
            try:
                language = (parse_qs(parsed.query).get("language") or ["all"])[0]
                self._send_json(self.server.app_state.news_snapshot(language=language))
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
        if parsed.path == "/api/portfolio/chart":
            query = parse_qs(parsed.query)
            market = (query.get("market") or [""])[0]
            symbol = (query.get("symbol") or [""])[0]
            period = (query.get("period") or [None])[0]
            mode = (query.get("mode") or ["daily"])[0]
            try:
                self._send_json(
                    self.server.app_state.portfolio_chart(
                        market, symbol, period, mode
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
        try:
            payload = self._read_json(max_length=1_400_000 if parsed.path == "/api/research/etf/import" else 64_000)
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
            if parsed.path == "/api/auto":
                self.server.app_state.set_auto(bool(payload.get("enabled", False)))
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/watchlist":
                item = self.server.app_state.add_watch_item(payload)
                self._send_json({"item": item}, status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/portfolio/positions":
                item = self.server.app_state.add_position(payload)
                self._send_json({"item": item}, status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/portfolio/principal":
                amount = self.server.app_state.config_store.set_portfolio_principal(payload.get("principal_cny"))
                self._send_json({"principal_cny": amount})
                return
            if parsed.path == "/api/portfolio/adjust":
                item = self.server.app_state.adjust_position(payload)
                self._send_json({"item": item, "closed": item is None})
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
            if parsed.path == "/api/portfolio/positions":
                if self._read_json().get("confirmed") is not True:
                    raise ValueError("清空全部持仓需要明确确认")
                removed = self.server.app_state.clear_positions()
                self._send_json({"ok": True, "removed_count": removed})
                return
            if len(parts) == 4 and parts[:2] == ["api", "watchlist"]:
                removed = self.server.app_state.remove_watch_item(parts[2], parts[3])
                if not removed:
                    self._send_json({"error": "股票不存在"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"ok": True})
                return
            if len(parts) == 5 and parts[:3] == ["api", "portfolio", "positions"]:
                removed = self.server.app_state.remove_position(parts[3], parts[4])
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
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
            "/startup_memory.js": "startup_memory.js",
            "/social.js": "social.js",
            "/research.js": "research.js",
            "/earnings.js": "earnings.js",
            "/workbench.js": "workbench.js",
            "/workbench.css": "workbench.css",
            "/annotations.js": "annotations.js",
            "/annotations.css": "annotations.css",
            "/indices.js": "indices.js",
            "/indices.css": "indices.css",
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
