from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import WatchItem


@dataclass(frozen=True)
class AnalysisConfig:
    mode: str
    model: str


@dataclass(frozen=True)
class SourceConfig:
    google_news_rss: bool
    yahoo_finance_rss: bool
    inbox: Path
    rss_feeds: tuple[dict[str, str], ...]
    bing_news_rss: bool = True


@dataclass(frozen=True)
class StorageConfig:
    database: Path
    alerts: Path


@dataclass(frozen=True)
class AppConfig:
    poll_interval_minutes: int
    lookback_hours: int
    max_items_per_asset: int
    alert_threshold: float
    analysis: AnalysisConfig
    sources: SourceConfig
    storage: StorageConfig
    watchlist: tuple[WatchItem, ...]
    poll_interval_seconds: int = 0

    @property
    def news_interval_seconds(self) -> int:
        return self.poll_interval_seconds or self.poll_interval_minutes * 60


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    base = config_path.parent
    analysis_raw = raw.get("analysis", {})
    source_raw = raw.get("sources", {})
    storage_raw = raw.get("storage", {})

    mode = str(analysis_raw.get("mode", "auto")).lower()
    if mode not in {"auto", "rules", "openai"}:
        raise ValueError("analysis.mode 必须是 auto、rules 或 openai")

    threshold = float(raw.get("alert_threshold", 0.35))
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("alert_threshold 必须在 0 到 1 之间")

    watchlist = []
    for item in raw.get("watchlist", []):
        aliases = tuple(
            dict.fromkeys(
                [
                    str(item["symbol"]),
                    str(item["name"]),
                    *(str(alias) for alias in item.get("aliases", [])),
                ]
            )
        )
        watchlist.append(
            WatchItem(
                market=str(item["market"]).upper(),
                symbol=str(item["symbol"]).upper(),
                name=str(item["name"]),
                query=str(item.get("query") or item["name"]),
                aliases=aliases,
            )
        )
    if not watchlist:
        raise ValueError("watchlist 不能为空")

    rss_feeds = tuple(
        {
            "name": str(feed.get("name") or "custom-rss"),
            "url": str(feed["url"]),
        }
        for feed in source_raw.get("rss_feeds", [])
    )

    return AppConfig(
        poll_interval_minutes=max(1, int(raw.get("poll_interval_minutes", 2))),
        lookback_hours=max(1, int(raw.get("lookback_hours", 72))),
        max_items_per_asset=max(1, min(100,int(raw.get("max_items_per_asset", 30)))),
        alert_threshold=threshold,
        analysis=AnalysisConfig(
            mode=mode,
            model=str(analysis_raw.get("model", "gpt-5-mini")),
        ),
        sources=SourceConfig(
            google_news_rss=bool(source_raw.get("google_news_rss", True)),
            yahoo_finance_rss=bool(source_raw.get("yahoo_finance_rss", True)),
            inbox=_resolve(base, str(source_raw.get("inbox", "data/inbox.jsonl"))),
            rss_feeds=rss_feeds,
            bing_news_rss=bool(source_raw.get("bing_news_rss", True)),
        ),
        storage=StorageConfig(
            database=_resolve(base, str(storage_raw.get("database", "data/monitor.db"))),
            alerts=_resolve(base, str(storage_raw.get("alerts", "data/alerts.jsonl"))),
        ),
        watchlist=tuple(watchlist),
        poll_interval_seconds=max(1, min(86400, int(raw.get("poll_interval_seconds", max(1, int(raw.get("poll_interval_minutes", 2))) * 60)))),
    )
