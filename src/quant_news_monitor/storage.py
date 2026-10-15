from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator

from .models import Alert, Analysis, MarketMovement, NewsItem, iso_utc, news_event_key, parse_datetime, utc_now
from .news_impact import explain_news_impact


SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    fingerprint TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    title TEXT NOT NULL,
    news_summary TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    analyzed_at TEXT,
    analyzer TEXT,
    event_type TEXT,
    direction REAL,
    importance REAL,
    surprise REAL,
    relevance REAL,
    confidence REAL,
    horizon TEXT,
    analysis_summary TEXT,
    risk_flags TEXT,
    score REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    fingerprint TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    polarity TEXT NOT NULL,
    level TEXT NOT NULL,
    score REAL NOT NULL,
    FOREIGN KEY (fingerprint) REFERENCES news_items(fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_news_pending
ON news_items(analyzed_at, attempts, published_at);

CREATE TABLE IF NOT EXISTS market_movements (
    scan_id TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    exchange TEXT NOT NULL,
    sector TEXT NOT NULL,
    currency TEXT NOT NULL,
    price REAL NOT NULL,
    change_pct REAL NOT NULL,
    volume REAL NOT NULL,
    relative_volume REAL NOT NULL,
    market_cap REAL NOT NULL,
    movement_type TEXT NOT NULL,
    intensity_score REAL NOT NULL,
    reason_category TEXT NOT NULL,
    reason_summary TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_title TEXT NOT NULL,
    evidence_url TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    evidence_published_at TEXT,
    analyzer TEXT NOT NULL,
    PRIMARY KEY (scan_id, market, symbol)
);

CREATE INDEX IF NOT EXISTS idx_market_movements_latest
ON market_movements(scan_id DESC, intensity_score DESC);
"""


class NewsStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as connection:
            # Frequent UI reads must not prevent the collector from committing updates.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(news_items)")}
            for column in ("analysis_metadata", "event_key"):
                if column not in columns:
                    try:
                        connection.execute(f"ALTER TABLE news_items ADD COLUMN {column} TEXT")
                    except sqlite3.OperationalError:
                        if column not in {row[1] for row in connection.execute("PRAGMA table_info(news_items)")}:
                            raise
            connection.execute("CREATE INDEX IF NOT EXISTS idx_news_event ON news_items(event_key)")

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def add_items(self, items: list[NewsItem]) -> int:
        inserted = 0
        with self._session() as connection:
            for item in items:
                connection.execute(
                    """UPDATE news_items SET news_summary=?, source=?, published_at=?, analyzed_at=NULL,
                    attempts=0, last_error=NULL, score=NULL WHERE fingerprint=?
                    AND (news_summary!=? OR source!=? OR published_at!=?)""",
                    (item.summary,item.source,iso_utc(item.published_at),item.fingerprint,
                     item.summary,item.source,iso_utc(item.published_at)),
                )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO news_items (
                        fingerprint, market, symbol, title, news_summary, source,
                        url, published_at, discovered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.fingerprint,
                        item.market,
                        item.symbol,
                        item.title,
                        item.summary,
                        item.source,
                        item.url,
                        iso_utc(item.published_at),
                        iso_utc(item.discovered_at),
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def pending_items(
        self,
        limit: int = 500,
        *,
        analyzer: str | None = None,
        eligible: Callable[[NewsItem], bool] | None = None,
    ) -> list[NewsItem]:
        items = []
        with self._session() as connection:
            conditions = "analyzed_at IS NULL AND attempts < 3"
            parameters = ()
            if analyzer:
                conditions = f"({conditions}) OR (analyzed_at IS NOT NULL AND COALESCE(analyzer, '') != ? AND last_error IS NULL)"
                parameters = (analyzer,)
            rows = connection.execute(
                f"SELECT * FROM news_items WHERE {conditions} ORDER BY published_at DESC",
                parameters,
            )
            # Apply scope before the batch limit so obsolete records cannot block current news.
            try:
                for row in rows:
                    item = NewsItem(
                        market=row["market"], symbol=row["symbol"], title=row["title"],
                        summary=row["news_summary"], source=row["source"], url=row["url"],
                        published_at=parse_datetime(row["published_at"]),
                        discovered_at=parse_datetime(row["discovered_at"]),
                    )
                    if eligible is None or eligible(item):
                        items.append(item)
                    if len(items) >= max(1, min(limit, 1000)):
                        break
            finally:
                rows.close()
        return items

    def save_analysis(self, item: NewsItem, analysis: Analysis, analyzed_at: datetime) -> None:
        with self._session() as connection:
            connection.execute(
                """
                UPDATE news_items SET
                    analyzed_at = ?, analyzer = ?, event_type = ?, direction = ?,
                    importance = ?, surprise = ?, relevance = ?, confidence = ?,
                    horizon = ?, analysis_summary = ?, risk_flags = ?, score = ?,
                    attempts = attempts + 1, last_error = NULL, analysis_metadata = ?, event_key = ?
                WHERE fingerprint = ?
                """,
                (
                    iso_utc(analyzed_at),
                    analysis.analyzer,
                    analysis.event_type,
                    analysis.direction,
                    analysis.importance,
                    analysis.surprise,
                    analysis.relevance,
                    analysis.confidence,
                    analysis.horizon,
                    analysis.summary,
                    json.dumps(analysis.risk_flags, ensure_ascii=False),
                    analysis.score,
                    json.dumps(analysis.metadata, ensure_ascii=False),
                    news_event_key(item.market, item.symbol, item.title, item.summary, iso_utc(item.published_at)),
                    item.fingerprint,
                ),
            )

    def save_error(self, item: NewsItem, error: str) -> None:
        with self._session() as connection:
            connection.execute(
                """
                UPDATE news_items
                SET attempts = attempts + 1, last_error = ?
                WHERE fingerprint = ?
                """,
                (error[:1000], item.fingerprint),
            )

    def create_alert(self, alert: Alert) -> bool:
        with self._session() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO alerts (
                    fingerprint, created_at, polarity, level, score
                ) SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS (
                    SELECT 1 FROM alerts a JOIN news_items old ON old.fingerprint=a.fingerprint
                    JOIN news_items current ON current.fingerprint=?
                    WHERE current.event_key IS NOT NULL AND current.event_key!='' AND old.event_key=current.event_key
                )
                """,
                (
                    alert.fingerprint,
                    iso_utc(alert.created_at),
                    alert.polarity,
                    alert.level,
                    alert.score,
                    alert.fingerprint,
                ),
            )
            return cursor.rowcount == 1

    def reconcile_alert(self, fingerprint: str, threshold: float) -> None:
        with self._session() as connection:
            row = connection.execute("SELECT score FROM news_items WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if row is None:
                return
            score = float(row["score"] or 0)
            if abs(score) < threshold:
                connection.execute("DELETE FROM alerts WHERE fingerprint = ?", (fingerprint,))
            else:
                connection.execute("UPDATE alerts SET score = ?, polarity = ?, level = ? WHERE fingerprint = ?",
                                   (score, "bullish" if score > 0 else "bearish", "high" if abs(score) >= .65 else "medium", fingerprint))

    def dashboard_stats(self, *, hours: int, threshold: float) -> dict[str, object]:
        cutoff = iso_utc(utc_now() - timedelta(hours=hours))
        with self._session() as connection:
            news = connection.execute(
                """
                SELECT
                    COUNT(*) AS news_count,
                    COALESCE(SUM(CASE WHEN score >= ? THEN 1 ELSE 0 END), 0) AS bullish,
                    COALESCE(SUM(CASE WHEN score <= -? THEN 1 ELSE 0 END), 0) AS bearish,
                    COALESCE(SUM(CASE WHEN ABS(score) < ? THEN 1 ELSE 0 END), 0) AS neutral,
                    MAX(analyzed_at) AS last_analyzed_at
                FROM news_items
                WHERE analyzed_at IS NOT NULL AND published_at >= ?
                """,
                (threshold, threshold, threshold, cutoff),
            ).fetchone()
            alert_count = connection.execute(
                "SELECT COUNT(*) AS count FROM alerts WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()["count"]
            total_news = connection.execute(
                "SELECT COUNT(*) AS count FROM news_items"
            ).fetchone()["count"]
        return {
            "window_hours": hours,
            "news_count": int(news["news_count"]),
            "bullish": int(news["bullish"]),
            "bearish": int(news["bearish"]),
            "neutral": int(news["neutral"]),
            "alert_count": int(alert_count),
            "total_news": int(total_news),
            "last_analyzed_at": news["last_analyzed_at"],
        }

    def recent_news(self, limit: int = 100, *, language: str = "all") -> list[dict[str, object]]:
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT
                    n.*,
                    a.created_at AS alert_created_at,
                    a.polarity AS alert_polarity,
                    a.level AS alert_level
                FROM news_items AS n
                LEFT JOIN alerts AS a ON a.fingerprint = n.fingerprint
                WHERE n.analyzed_at IS NOT NULL
                  AND n.source != 'demo' AND n.title NOT LIKE '[演示数据]%'
                  AND (? != 'zh' OR n.title GLOB '*[一-龥]*')
                ORDER BY n.published_at DESC
                LIMIT ?
                """,
                (language, max(1, min(limit, 10000))),
            ).fetchall()
        return [_serialize_news_row(row) for row in rows]

    def recent_alerts(self, limit: int = 50) -> list[dict[str, object]]:
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT
                    n.*,
                    a.created_at AS alert_created_at,
                    a.polarity AS alert_polarity,
                    a.level AS alert_level
                FROM alerts AS a
                JOIN news_items AS n ON n.fingerprint = a.fingerprint
                WHERE n.source != 'demo' AND n.title NOT LIKE '[演示数据]%'
                ORDER BY a.created_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 10000)),),
            ).fetchall()
        return [_serialize_news_row(row) for row in rows]

    def sentiment_by_symbol(self, *, hours: int, records: list[dict] | None = None) -> list[dict[str, object]]:
        now = utc_now()
        cutoff = iso_utc(now - timedelta(hours=hours))
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT
                    market, symbol, title, news_summary, event_key, score, importance, confidence, published_at
                FROM news_items
                WHERE analyzed_at IS NOT NULL AND published_at >= ?
                ORDER BY published_at DESC
                """,
                (cutoff,),
            ).fetchall()
        if records is not None:
            rows = records
        grouped: dict[tuple[str, str], dict[str, object]] = {}
        seen_events: set[str] = set()
        for row in rows:
            record = dict(row)
            event_key = record.get("event_key") or news_event_key(str(row["market"]), str(row["symbol"]),
                str(record.get("title", "")), str(record.get("news_summary", "")), str(row["published_at"]))
            if event_key and event_key in seen_events:
                continue
            if event_key:
                seen_events.add(event_key)
            key = (str(row["market"]), str(row["symbol"]))
            group = grouped.setdefault(
                key,
                {
                    "market": key[0],
                    "symbol": key[1],
                    "news_count": 0,
                    "directional_count": 0,
                    "bullish_count": 0,
                    "bearish_count": 0,
                    "weighted_score": 0.0,
                    "weight": 0.0,
                    "latest_at": row["published_at"],
                },
            )
            group["news_count"] = int(group["news_count"]) + 1
            score = float(row["score"] or 0.0)
            if abs(score) <= 0.04:
                continue
            age_hours = max(
                0.0,
                (now - parse_datetime(str(row["published_at"]))).total_seconds() / 3600,
            )
            recency_weight = math.pow(0.5, age_hours / 36.0)
            quality_weight = (
                0.55
                + 0.25 * float(row["importance"] or 0.0)
                + 0.20 * float(row["confidence"] or 0.0)
            )
            weight = recency_weight * quality_weight
            group["weighted_score"] = float(group["weighted_score"]) + score * weight
            group["weight"] = float(group["weight"]) + weight
            group["directional_count"] = int(group["directional_count"]) + 1
            count_key = "bullish_count" if score > 0 else "bearish_count"
            group[count_key] = int(group[count_key]) + 1

        result = []
        for group in grouped.values():
            total_weight = float(group.pop("weight"))
            weighted_score = float(group.pop("weighted_score"))
            group["average_score"] = round(
                weighted_score / total_weight if total_weight else 0.0,
                4,
            )
            result.append(group)
        result.sort(
            key=lambda item: (
                abs(float(item["average_score"])),
                int(item["directional_count"]),
                int(item["news_count"]),
            ),
            reverse=True,
        )
        return result

    def save_market_movements(self, movements: list[MarketMovement]) -> int:
        if not movements:
            return 0
        saved = 0
        with self._session() as connection:
            for item in movements:
                cursor = connection.execute(
                    """
                    INSERT OR REPLACE INTO market_movements (
                        scan_id, detected_at, market, symbol, name, exchange, sector,
                        currency, price, change_pct, volume, relative_volume,
                        market_cap, movement_type, intensity_score, reason_category,
                        reason_summary, confidence, evidence_title, evidence_url,
                        evidence_source, evidence_published_at, analyzer
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.scan_id,
                        iso_utc(item.detected_at),
                        item.market,
                        item.symbol,
                        item.name,
                        item.exchange,
                        item.sector,
                        item.currency,
                        item.price,
                        item.change_pct,
                        item.volume,
                        item.relative_volume,
                        item.market_cap,
                        item.movement_type,
                        item.intensity_score,
                        item.reason_category,
                        item.reason_summary,
                        item.confidence,
                        item.evidence_title,
                        item.evidence_url,
                        item.evidence_source,
                        iso_utc(item.evidence_published_at) if item.evidence_published_at else None,
                        item.analyzer,
                    ),
                )
                saved += cursor.rowcount
            cutoff = iso_utc(utc_now() - timedelta(days=30))
            connection.execute("DELETE FROM market_movements WHERE detected_at < ?", (cutoff,))
        return saved

    def latest_market_movements(self, limit: int = 100) -> list[dict[str, object]]:
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT movement.*
                FROM market_movements AS movement
                JOIN (
                    SELECT market, MAX(scan_id) AS scan_id
                    FROM market_movements
                    GROUP BY market
                ) AS latest
                  ON latest.market = movement.market
                 AND latest.scan_id = movement.scan_id
                ORDER BY movement.intensity_score DESC, ABS(movement.change_pct) DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [_serialize_movement_row(row) for row in rows]


def _serialize_news_row(row: sqlite3.Row) -> dict[str, object]:
    risk_flags: list[str] = []
    if row["risk_flags"]:
        try:
            risk_flags = [str(value) for value in json.loads(row["risk_flags"])]
        except (TypeError, json.JSONDecodeError):
            risk_flags = [str(row["risk_flags"])]
    score = float(row["score"] or 0.0)
    metadata = json.loads(row["analysis_metadata"] or "{}")
    impact = explain_news_impact(
        str(row["event_type"] or "other"),
        score,
        str(metadata.get("evidence_text", f"{row['title']} {row['news_summary']}")),
    )
    return {
        "fingerprint": row["fingerprint"],
        "market": row["market"],
        "symbol": row["symbol"],
        "title": row["title"],
        "news_summary": row["news_summary"],
        "source": row["source"],
        "url": row["url"],
        "published_at": row["published_at"],
        "discovered_at": row["discovered_at"],
        "analyzed_at": row["analyzed_at"],
        "analyzer": row["analyzer"],
        "analysis_metadata": metadata,
        "event_key": row["event_key"] or news_event_key(row["market"], row["symbol"], row["title"], row["news_summary"], row["published_at"]),
        "evidence_status": metadata.get("evidence_status", "legacy"),
        "event_type": row["event_type"] or "other",
        "direction": round(float(row["direction"] or 0.0), 4),
        "importance": round(float(row["importance"] or 0.0), 4),
        "surprise": round(float(row["surprise"] or 0.0), 4),
        "relevance": round(float(row["relevance"] or 0.0), 4),
        "confidence": round(float(row["confidence"] or 0.0), 4),
        "horizon": row["horizon"] or "1-5d",
        "analysis_summary": row["analysis_summary"] or "",
        "risk_flags": risk_flags,
        "score": round(score, 4),
        "polarity": "bullish" if score > 0.04 else "bearish" if score < -0.04 else "neutral",
        "impact_label": impact["label"],
        "impact_area": impact["area"],
        "impact_reason": impact["reason"],
        "alert_created_at": row["alert_created_at"],
        "alert_polarity": row["alert_polarity"],
        "alert_level": row["alert_level"],
    }


def _serialize_movement_row(row: sqlite3.Row) -> dict[str, object]:
    change_pct = float(row["change_pct"] or 0.0)
    return {
        "scan_id": row["scan_id"],
        "detected_at": row["detected_at"],
        "market": row["market"],
        "symbol": row["symbol"],
        "name": row["name"],
        "exchange": row["exchange"],
        "sector": row["sector"],
        "currency": row["currency"],
        "price": round(float(row["price"] or 0.0), 4),
        "change_pct": round(change_pct, 4),
        "direction": "up" if change_pct > 0 else "down" if change_pct < 0 else "flat",
        "volume": round(float(row["volume"] or 0.0), 2),
        "relative_volume": round(float(row["relative_volume"] or 0.0), 2),
        "market_cap": round(float(row["market_cap"] or 0.0), 2),
        "movement_type": row["movement_type"],
        "intensity_score": round(float(row["intensity_score"] or 0.0), 2),
        "reason_category": row["reason_category"],
        "reason_summary": row["reason_summary"],
        "confidence": round(float(row["confidence"] or 0.0), 2),
        "evidence_title": row["evidence_title"],
        "evidence_url": row["evidence_url"],
        "evidence_source": row["evidence_source"],
        "evidence_published_at": row["evidence_published_at"],
        "analyzer": row["analyzer"],
    }


def append_alert(path: Path, alert: Alert) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(alert.as_dict(), ensure_ascii=False) + "\n")
