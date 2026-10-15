from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse_datetime(value: str | None, *, default: datetime | None = None) -> datetime:
    if not value:
        return default or utc_now()
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return default or utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_news_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path,
                          urlencode(sorted(query)), "")) if parsed.netloc else ""
    except ValueError:
        return ""


def news_event_key(market: str, symbol: str, title: str, summary: str, published_at: str) -> str:
    # ponytail: exact-content/day grouping; paraphrases remain separate until provenance can prove a common origin.
    def normalized(value: str) -> str:
        return re.sub(r"\s+", " ", value.casefold()).strip()
    title = normalized(re.sub(r"\s+(?:-|\|)\s+[^|]{2,60}$", "", title))
    summary = normalized(summary)
    if not title:
        return ""
    if summary == title:
        summary = ""
    try:
        published = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        day = published.astimezone(timezone.utc).date().isoformat() if published.tzinfo else published.date().isoformat()
    except (ValueError, TypeError):
        return ""
    return hashlib.sha256(f"{market}|{symbol}|{day}|{title}|{summary}".encode()).hexdigest()


@dataclass(frozen=True)
class WatchItem:
    market: str
    symbol: str
    name: str
    query: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class NewsItem:
    market: str
    symbol: str
    title: str
    summary: str
    source: str
    url: str
    published_at: datetime
    discovered_at: datetime = field(default_factory=utc_now)
    target_name: str = ""
    target_aliases: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        title = re.sub(r"\s+", " ", self.title.casefold()).strip()
        canonical_url = canonical_news_url(self.url) or self.url
        raw = f"{self.market}|{self.symbol}|{title}|{canonical_url}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Analysis:
    event_type: str
    direction: float
    importance: float
    surprise: float
    relevance: float
    confidence: float
    horizon: str
    summary: str
    risk_flags: tuple[str, ...]
    analyzer: str
    metadata: dict = field(default_factory=dict)

    @property
    def score(self) -> float:
        magnitude = (
            0.35 * self.importance
            + 0.30 * self.surprise
            + 0.20 * self.relevance
            + 0.15 * self.confidence
        )
        return max(-1.0, min(1.0, self.direction * magnitude))


@dataclass(frozen=True)
class Alert:
    fingerprint: str
    created_at: datetime
    market: str
    symbol: str
    title: str
    url: str
    score: float
    polarity: str
    level: str
    event_type: str
    horizon: str
    summary: str
    risk_flags: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "created_at": iso_utc(self.created_at),
            "market": self.market,
            "symbol": self.symbol,
            "title": self.title,
            "url": self.url,
            "score": round(self.score, 4),
            "polarity": self.polarity,
            "level": self.level,
            "event_type": self.event_type,
            "horizon": self.horizon,
            "summary": self.summary,
            "risk_flags": list(self.risk_flags),
        }


@dataclass(frozen=True)
class MarketMovement:
    scan_id: str
    detected_at: datetime
    market: str
    symbol: str
    name: str
    exchange: str
    sector: str
    currency: str
    price: float
    change_pct: float
    volume: float
    relative_volume: float
    market_cap: float
    movement_type: str
    intensity_score: float
    reason_category: str
    reason_summary: str
    confidence: float
    evidence_title: str = ""
    evidence_url: str = ""
    evidence_source: str = ""
    evidence_published_at: datetime | None = None
    analyzer: str = "rules-v1"
