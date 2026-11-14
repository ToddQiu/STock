from __future__ import annotations

import json
import math
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit

from .models import Alert, Analysis, MarketMovement, NewsItem, canonical_news_url, iso_utc, news_event_key, parse_datetime, utc_now
from .news_impact import explain_news_impact
from .movement_reason import REASON_VERSION, legacy_movement_reason


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
    reason_details TEXT,
    PRIMARY KEY (scan_id, market, symbol)
);

CREATE INDEX IF NOT EXISTS idx_market_movements_latest
ON market_movements(scan_id DESC, intensity_score DESC);
"""


def _article_url(url: str) -> str:
    canonical = canonical_news_url(url)
    parsed = urlsplit(canonical)
    # Home pages are not article identities; never merge unrelated headlines pointing there.
    return canonical if parsed.path not in {"", "/"} or parsed.query else ""


def _analysis_metadata(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _direction_eligible(metadata: dict) -> bool:
    return (metadata.get("judgment_version") == "evidence-v1"
            and metadata.get("direction_eligible") is True
            and metadata.get("evidence_status") == "supported")


class NewsStore:
    _schema_lock = threading.RLock()
    _initialized: dict[Path, tuple] = {}

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._schema_lock:
            token = self._file_token()
            if token is not None and self._initialized.get(self.database_path) == token:
                return
            self._initialize_schema()
            if len(self._initialized) > 256:
                self._initialized.clear()
            self._initialized[self.database_path] = self._file_token()

    def _file_token(self):
        try:
            stat = self.database_path.stat()
            return (stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime_ns", 0))
        except FileNotFoundError:
            return None

    def _initialize_schema(self):
        with self._session() as connection:
            # Frequent UI reads must not prevent the collector from committing updates.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            movement_columns = {row[1] for row in connection.execute("PRAGMA table_info(market_movements)")}
            if "reason_details" not in movement_columns:
                try:
                    connection.execute("ALTER TABLE market_movements ADD COLUMN reason_details TEXT")
                except sqlite3.OperationalError:
                    if "reason_details" not in {row[1] for row in connection.execute("PRAGMA table_info(market_movements)")}:
                        raise
            columns = {row[1] for row in connection.execute("PRAGMA table_info(news_items)")}
            for column in ("analysis_metadata", "event_key", "retry_after", "attempted_analyzer", "article_url", "superseded_by"):
                if column not in columns:
                    try:
                        connection.execute(f"ALTER TABLE news_items ADD COLUMN {column} TEXT")
                    except sqlite3.OperationalError:
                        if column not in {row[1] for row in connection.execute("PRAGMA table_info(news_items)")}:
                            raise
            connection.execute("CREATE INDEX IF NOT EXISTS idx_news_event ON news_items(event_key)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_news_article ON news_items(market, symbol, article_url)")
            connection.execute("""CREATE INDEX IF NOT EXISTS idx_news_current_published
                ON news_items(published_at DESC, fingerprint)
                WHERE superseded_by IS NULL AND analyzed_at IS NOT NULL""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC, fingerprint)")
            connection.execute("CREATE VIEW IF NOT EXISTS current_news_items AS SELECT * FROM news_items WHERE superseded_by IS NULL")
            # Add provenance without rewriting fingerprints or deleting historical observations.
            for row in connection.execute("SELECT fingerprint, url FROM news_items WHERE article_url IS NULL").fetchall():
                connection.execute("UPDATE news_items SET article_url=? WHERE fingerprint=?", (_article_url(row['url']), row['fingerprint']))
            if "superseded_by" not in columns:
                connection.execute("""WITH revisions AS (
                    SELECT fingerprint, FIRST_VALUE(fingerprint) OVER (
                        PARTITION BY market, symbol, article_url ORDER BY published_at DESC, discovered_at DESC, rowid DESC
                    ) AS latest FROM news_items WHERE article_url!=''
                ) UPDATE news_items SET superseded_by=(SELECT latest FROM revisions WHERE revisions.fingerprint=news_items.fingerprint)
                WHERE fingerprint IN (SELECT fingerprint FROM revisions WHERE fingerprint!=latest)""")
            # Retain the old judgment for audit, but never trade on unvalidated legacy scores.
            legacy_rows = connection.execute("""SELECT * FROM news_items WHERE analyzed_at IS NOT NULL
                AND COALESCE(CASE WHEN json_valid(analysis_metadata)
                    THEN json_extract(analysis_metadata, '$.judgment_version') END, '') != 'evidence-v1'""").fetchall()
            for row in legacy_rows:
                metadata = _analysis_metadata(row["analysis_metadata"])
                old_alert = connection.execute("SELECT * FROM alerts WHERE fingerprint=?", (row["fingerprint"],)).fetchone()
                metadata["legacy_judgment"] = {key: row[key] for key in (
                    "analyzed_at", "analyzer", "direction", "score", "confidence", "importance",
                    "surprise", "relevance", "analysis_summary", "risk_flags",
                )}
                if old_alert:
                    metadata["legacy_judgment"]["alert"] = dict(old_alert)
                metadata.update(judgment_version="evidence-v1", evidence_status="legacy", direction_eligible=False)
                connection.execute("""UPDATE news_items SET direction=0, score=0, confidence=0,
                    analysis_summary='旧版判断待重新核实，暂不计入利多利空信号。', risk_flags='[]',
                    analysis_metadata=? WHERE fingerprint=?""", (json.dumps(metadata, ensure_ascii=False), row["fingerprint"]))
                connection.execute("DELETE FROM alerts WHERE fingerprint=?", (row["fingerprint"],))

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=15)
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
                    attempts=0, last_error=NULL, retry_after=NULL, attempted_analyzer=NULL, score=NULL WHERE fingerprint=? AND superseded_by IS NULL
                    AND (news_summary!=? OR source!=? OR published_at!=?)""",
                    (item.summary,item.source,iso_utc(item.published_at),item.fingerprint,
                     item.summary,item.source,iso_utc(item.published_at)),
                )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO news_items (
                        fingerprint, market, symbol, title, news_summary, source,
                        url, published_at, discovered_at, article_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        _article_url(item.url),
                    ),
                )
                inserted += cursor.rowcount
                article_url = _article_url(item.url)
                if cursor.rowcount and article_url:
                    latest = connection.execute("""SELECT fingerprint FROM news_items WHERE market=? AND symbol=? AND article_url=?
                        ORDER BY published_at DESC, discovered_at DESC, rowid DESC LIMIT 1""",
                        (item.market, item.symbol, article_url)).fetchone()[0]
                    connection.execute("""UPDATE news_items SET superseded_by=CASE WHEN fingerprint=? THEN NULL ELSE ? END
                        WHERE market=? AND symbol=? AND article_url=?""", (latest, latest, item.market, item.symbol, article_url))
        return inserted

    def pending_items(
        self,
        limit: int = 500,
        *,
        analyzer: str | None = None,
        eligible: Callable[[NewsItem], bool] | None = None,
        now: datetime | None = None,
    ) -> list[NewsItem]:
        items = []
        with self._session() as connection:
            conditions = "analyzed_at IS NULL"
            parameters = []
            if analyzer:
                conditions = f"({conditions} OR COALESCE(analyzer, '') != ?)"
                parameters.append(analyzer)
            conditions += " AND (retry_after IS NULL OR retry_after <= ?"
            parameters.append(iso_utc(now or utc_now()))
            if analyzer:
                conditions += " OR COALESCE(attempted_analyzer, '') != ?"
                parameters.append(analyzer)
            conditions += ")"
            rows = connection.execute(
                f"SELECT * FROM current_news_items WHERE {conditions} ORDER BY published_at DESC",
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
            metadata = dict(analysis.metadata)
            old = connection.execute("SELECT analysis_metadata FROM news_items WHERE fingerprint=?", (item.fingerprint,)).fetchone()
            legacy = _analysis_metadata(old["analysis_metadata"] if old else None).get("legacy_judgment")
            if legacy:
                metadata["legacy_judgment"] = legacy
            connection.execute(
                """
                UPDATE news_items SET
                    analyzed_at = ?, analyzer = ?, event_type = ?, direction = ?,
                    importance = ?, surprise = ?, relevance = ?, confidence = ?,
                    horizon = ?, analysis_summary = ?, risk_flags = ?, score = ?,
                    attempts = 0, last_error = NULL, retry_after = NULL, attempted_analyzer = NULL, analysis_metadata = ?, event_key = ?
                WHERE fingerprint = ?
                """,
                (
                    iso_utc(analyzed_at),
                    analysis.analyzer,
                    analysis.event_type,
                    analysis.direction if _direction_eligible(metadata) else 0.0,
                    analysis.importance,
                    analysis.surprise,
                    analysis.relevance,
                    analysis.confidence,
                    analysis.horizon,
                    analysis.summary,
                    json.dumps(analysis.risk_flags, ensure_ascii=False),
                    analysis.score if _direction_eligible(metadata) else 0.0,
                    json.dumps(metadata, ensure_ascii=False),
                    news_event_key(item.market, item.symbol, item.title, item.summary, iso_utc(item.published_at)),
                    item.fingerprint,
                ),
            )
            if not _direction_eligible(metadata):
                connection.execute("DELETE FROM alerts WHERE fingerprint=?", (item.fingerprint,))

    def save_error(self, item: NewsItem, error: str, *, analyzer: str = "", now: datetime | None = None) -> None:
        with self._session() as connection:
            row = connection.execute("SELECT attempts, attempted_analyzer FROM news_items WHERE fingerprint=?", (item.fingerprint,)).fetchone()
            if row is None:
                return
            attempts = (row['attempts'] if row['attempted_analyzer'] == analyzer else 0) + 1
            retry_after = iso_utc((now or utc_now()) + timedelta(seconds=min(3600, 30 * 2 ** min(attempts-1, 7))))
            connection.execute(
                """
                UPDATE news_items
                SET attempts = ?, last_error = ?, retry_after = ?, attempted_analyzer = ?
                WHERE fingerprint = ?
                """,
                (attempts, error[:1000], retry_after, analyzer, item.fingerprint),
            )

    def create_alert(self, alert: Alert) -> bool:
        with self._session() as connection:
            current = connection.execute("SELECT analysis_metadata FROM current_news_items WHERE fingerprint=?", (alert.fingerprint,)).fetchone()
            if current is None or not _direction_eligible(_analysis_metadata(current["analysis_metadata"])):
                return False
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO alerts (
                    fingerprint, created_at, polarity, level, score
                ) SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM current_news_items WHERE fingerprint=?) AND NOT EXISTS (
                    SELECT 1 FROM alerts a JOIN current_news_items old ON old.fingerprint=a.fingerprint
                    JOIN current_news_items current ON current.fingerprint=?
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
                    alert.fingerprint,
                ),
            )
            return cursor.rowcount == 1

    def reconcile_alert(self, fingerprint: str, threshold: float) -> None:
        with self._session() as connection:
            row = connection.execute("SELECT score, analysis_metadata FROM news_items WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if row is None:
                return
            score = float(row["score"] or 0)
            if not _direction_eligible(_analysis_metadata(row["analysis_metadata"])) or score == 0 or abs(score) < threshold:
                connection.execute("DELETE FROM alerts WHERE fingerprint = ?", (fingerprint,))
            else:
                connection.execute("UPDATE alerts SET score = ?, polarity = ?, level = ? WHERE fingerprint = ?",
                                   (score, "bullish" if score > 0 else "bearish", "high" if abs(score) >= .65 else "medium", fingerprint))

    def count_news(self) -> int:
        with self._session() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0])

    def recent_news(self, limit: int = 100, *, language: str = "all", market: str | None = None,
                    symbol: str | None = None) -> list[dict[str, object]]:
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT
                    n.*,
                    a.created_at AS alert_created_at,
                    a.polarity AS alert_polarity,
                    a.level AS alert_level
                FROM current_news_items AS n
                LEFT JOIN alerts AS a ON a.fingerprint = n.fingerprint
                WHERE n.analyzed_at IS NOT NULL
                  AND n.source != 'demo' AND n.title NOT LIKE '[演示数据]%'
                  AND (? != 'zh' OR n.title GLOB '*[一-龥]*')
                  AND (? IS NULL OR n.market = ?)
                  AND (? IS NULL OR n.symbol = ?)
                ORDER BY n.published_at DESC, n.fingerprint ASC
                LIMIT ?
                """,
                (language, market, market, symbol, symbol, max(1, min(limit, 10000))),
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
                JOIN current_news_items AS n ON n.fingerprint = a.fingerprint
                WHERE n.analyzed_at IS NOT NULL AND n.source != 'demo' AND n.title NOT LIKE '[演示数据]%'
                  AND CASE WHEN json_valid(n.analysis_metadata) THEN
                      json_extract(n.analysis_metadata, '$.judgment_version') = 'evidence-v1'
                      AND json_extract(n.analysis_metadata, '$.evidence_status') = 'supported'
                      AND json_extract(n.analysis_metadata, '$.direction_eligible') = 1 ELSE 0 END
                ORDER BY a.created_at DESC, a.fingerprint ASC
                LIMIT ?
                """,
                (max(1, min(limit, 10000)),),
            ).fetchall()
        return [_serialize_news_row(row) for row in rows]

    def sentiment_by_symbol(self, *, hours: int, records: list[dict] | None = None) -> list[dict[str, object]]:
        now = utc_now()
        rows = records
        if rows is None:
            cutoff = iso_utc(now - timedelta(hours=hours))
            with self._session() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        market, symbol, title, news_summary, event_key, score, importance, confidence, published_at, analysis_metadata
                    FROM current_news_items
                    WHERE analyzed_at IS NOT NULL AND published_at >= ?
                    ORDER BY published_at DESC, fingerprint ASC
                    """,
                    (cutoff,),
                ).fetchall()
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
            if not _direction_eligible(_analysis_metadata(record.get("analysis_metadata"))):
                continue
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
                        evidence_source, evidence_published_at, analyzer, reason_details
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        json.dumps(item.reason_details, ensure_ascii=False),
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
    metadata = _analysis_metadata(row["analysis_metadata"])
    if metadata.get("judgment_version") != "evidence-v1":
        metadata = {**metadata, "evidence_status": "legacy", "direction_eligible": False}
    eligible = _direction_eligible(metadata)
    score = float(row["score"] or 0.0) if eligible else 0.0
    status = metadata.get("evidence_status", "legacy")
    judgment_label = ("多空交织" if status == "mixed" else "中性" if status == "neutral" else
                      "待核实" if not eligible else "利多" if score > .04 else "利空" if score < -.04 else "中性")
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
        "judgment_label": judgment_label,
        "direction_eligible": eligible,
        "event_type": row["event_type"] or "other",
        "direction": round(float(row["direction"] or 0.0), 4) if eligible else 0.0,
        "importance": round(float(row["importance"] or 0.0), 4),
        "surprise": round(float(row["surprise"] or 0.0), 4),
        "relevance": round(float(row["relevance"] or 0.0), 4),
        "confidence": round(float(row["confidence"] or 0.0), 4),
        "horizon": row["horizon"] or "1-5d",
        "analysis_summary": "旧版判断待重新核实，暂不计入利多利空信号。" if status == "legacy" else row["analysis_summary"] or "",
        "risk_flags": [] if status == "legacy" else risk_flags,
        "score": round(score, 4),
        "polarity": "bullish" if score > 0.04 else "bearish" if score < -0.04 else "neutral",
        "impact_label": impact["label"],
        "impact_area": impact["area"],
        "impact_reason": impact["reason"],
        "alert_created_at": row["alert_created_at"] if eligible else None,
        "alert_polarity": row["alert_polarity"] if eligible else None,
        "alert_level": row["alert_level"] if eligible else None,
    }


def _serialize_movement_row(row: sqlite3.Row) -> dict[str, object]:
    change_pct = float(row["change_pct"] or 0.0)
    try:
        details = json.loads(row["reason_details"] or "{}")
    except (ValueError, TypeError):
        details = {}
    if not isinstance(details, dict) or details.get("version") != REASON_VERSION:
        facts = details.get("facts", []) if isinstance(details, dict) else []
        summary = "。".join(fact["text"] for fact in facts if isinstance(fact, dict)
                           and isinstance(fact.get("text"), str) and fact["text"] != row["evidence_title"])
        details = legacy_movement_reason(row["market"], row["symbol"], row["name"], row["evidence_title"],
            row["evidence_url"], row["evidence_source"], row["evidence_published_at"], row["detected_at"], change_pct, summary)
    linked = details.get("status") == "linked"
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
        "reason_category": details.get("category", "other") if linked else "unconfirmed",
        "reason_summary": "".join(details.get(key, "") for key in ("fact_summary", "summary", "relation_text")) if linked else "原因未核实",
        "reason_details": details,
        "confidence": min(.55, round(float(row["confidence"] or 0.0), 2)) if linked else 0,
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
