from __future__ import annotations

from .config import AppConfig
from .sources import (
    BingNewsRssSource,
    CustomRssSource,
    GoogleNewsRssSource,
    InboxSource,
    NewsSource,
    YahooFinanceRssSource,
)


def build_sources(config: AppConfig) -> list[NewsSource]:
    sources: list[NewsSource] = [InboxSource(config.sources.inbox)]
    if config.sources.yahoo_finance_rss:
        sources.append(YahooFinanceRssSource(cache_seconds=config.news_interval_seconds))
    if config.sources.google_news_rss:
        sources.append(GoogleNewsRssSource(cache_seconds=config.news_interval_seconds))
        sources.append(GoogleNewsRssSource(cache_seconds=config.news_interval_seconds, chinese=True))
    if config.sources.bing_news_rss:
        sources.append(BingNewsRssSource(cache_seconds=config.news_interval_seconds))
    sources.extend(
        CustomRssSource(feed["name"], feed["url"], cache_seconds=config.news_interval_seconds)
        for feed in config.sources.rss_feeds
    )
    return sources
