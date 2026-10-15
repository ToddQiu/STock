from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .models import NewsItem, canonical_news_url, iso_utc, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS input_versions (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, identity TEXT NOT NULL, version INTEGER NOT NULL,
 observed_at TEXT NOT NULL, published_at TEXT, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 UNIQUE(kind,identity,version)
);
CREATE INDEX IF NOT EXISTS input_identity ON input_versions(kind,identity,id DESC);
CREATE TABLE IF NOT EXISTS analysis_records (
 id TEXT PRIMARY KEY, input_id INTEGER NOT NULL REFERENCES input_versions(id), target TEXT NOT NULL,
 started_at TEXT NOT NULL, completed_at TEXT NOT NULL, engine TEXT NOT NULL,
 metadata TEXT NOT NULL, output TEXT NOT NULL, dedupe_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS analysis_time ON analysis_records(completed_at DESC);
CREATE INDEX IF NOT EXISTS analysis_input_time ON analysis_records(input_id,completed_at);
CREATE TABLE IF NOT EXISTS forecasts (
 id TEXT PRIMARY KEY, asset TEXT NOT NULL, horizon TEXT NOT NULL, input_id INTEGER NOT NULL REFERENCES input_versions(id),
 created_at TEXT NOT NULL, due_at TEXT NOT NULL, cohort TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS forecast_scope ON forecasts(asset,horizon,created_at DESC);
CREATE TABLE IF NOT EXISTS forecast_entries (
 forecast_id TEXT PRIMARY KEY REFERENCES forecasts(id), observed_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecast_outcomes (
 forecast_id TEXT PRIMARY KEY REFERENCES forecasts(id), recorded_at TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL
);
"""
HORIZONS = {"15m": 900, "1h": 3600, "24h": 86400}
QUOTE_MAX_AGE = 120
ENTRY_GRACE = 120
EXIT_GRACE = 180
METHOD_VERSION = "forward-v1"


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return parsed.astimezone(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def engine_fingerprint(*files: str) -> str:
    root = Path(__file__).parent
    hasher = hashlib.sha256()
    for filename in files:
        hasher.update(filename.encode())
        hasher.update((root/filename).read_bytes())
    return hasher.hexdigest()


def news_payload(item: NewsItem) -> tuple[str, dict]:
    url = canonical_news_url(item.url) or item.fingerprint
    return f"{item.market}:{item.symbol}:{url}", {
        "market": item.market, "symbol": item.symbol, "title": item.title,
        "summary": item.summary, "source": item.source, "url": item.url,
        "published_at": iso_utc(item.published_at),
    }


def valid_quote(quote: dict, now: datetime, *, not_before: datetime | None = None) -> bool:
    try:
        price = float(quote["price"])
        when = timestamp(quote["price_time"])
        return (math.isfinite(price) and price > 0 and not quote.get("delayed", False)
                and bool(quote.get("source")) and quote.get("currency") == "USD"
                and 0 <= (now-when).total_seconds() <= QUOTE_MAX_AGE
                and (not_before is None or when >= not_before))
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def paper_return(direction: int, entry: float, exit_price: float, policy: dict) -> dict:
    if direction == 0:
        return {"gross_return_pct": 0., "net_return_pct": 0., "entry_fill": None, "exit_fill": None, "cost_pct": 0.}
    slip = policy["slippage_bps"]/10000
    fee = policy["fee_bps"]/10000
    entry_fill = entry * (1+direction*slip)
    exit_fill = exit_price * (1-direction*slip)
    gross = direction * (exit_price/entry-1)*100
    net = (direction*(exit_fill/entry_fill-1) - fee*(1+exit_fill/entry_fill))*100
    return {"gross_return_pct": gross, "net_return_pct": net, "entry_fill": entry_fill,
            "exit_fill": exit_fill, "cost_pct": gross-net}


class ResearchLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as db:
            db.executescript(SCHEMA)
            for table in ("input_versions", "analysis_records", "forecasts", "forecast_entries", "forecast_outcomes"):
                for action in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'research records are append-only'); END")

    @contextmanager
    def session(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def observe(self, kind: str, identity: str, payload: dict, *, published_at: str | None = None, now: datetime | None = None,
                expected_version: int | None = None) -> dict:
        observed = iso_utc(now or utc_now())
        encoded, checksum = canonical(payload), digest(payload)
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT * FROM input_versions WHERE kind=? AND identity=? ORDER BY id DESC LIMIT 1", (kind, identity)).fetchone()
            if expected_version is not None and (latest["version"] if latest else 0) != expected_version:
                raise ValueError("研究已被更新，请重新打开最新版本后保存")
            if latest and latest["sha256"] == checksum:
                return dict(latest)
            version = latest["version"]+1 if latest else 1
            cursor = db.execute("INSERT INTO input_versions(kind,identity,version,observed_at,published_at,payload,sha256) VALUES(?,?,?,?,?,?,?)",
                                (kind, identity, version, observed, published_at, encoded, checksum))
            return dict(db.execute("SELECT * FROM input_versions WHERE id=?", (cursor.lastrowid,)).fetchone())

    def observe_news(self, item: NewsItem) -> dict:
        identity, payload = news_payload(item)
        return self.observe("news", identity, payload, published_at=payload["published_at"])

    def record_analysis(self, input_id: int, target: str, output: dict, *, started_at: datetime, completed_at: datetime,
                        engine: str, metadata: dict, dedupe_key: str | None = None) -> str:
        identifier = uuid.uuid4().hex
        with self.session() as db:
            version = db.execute("SELECT observed_at FROM input_versions WHERE id=?", (input_id,)).fetchone()
            if not version or not timestamp(version["observed_at"]) <= started_at <= completed_at:
                raise ValueError("分析时间早于输入记录时间")
            db.execute("INSERT OR IGNORE INTO analysis_records VALUES(?,?,?,?,?,?,?,?,?)",
                       (identifier, input_id, target, iso_utc(started_at), iso_utc(completed_at), engine, canonical(metadata), canonical(output), dedupe_key))
        return identifier

    def create_forecast(self, row: dict, quote: dict, context: dict, *, policy: dict, engine: dict, now: datetime | None = None) -> str | None:
        now = now or utc_now()
        horizon, asset = row.get("horizon"), row.get("asset")
        if horizon not in HORIZONS or asset not in {"GOLD", "BTC"} or not valid_quote(quote, now):
            return None
        try:
            probability = float(row["up_probability"])/100
            if not math.isfinite(probability) or not 0 <= probability <= 1 or not 0 <= (now-timestamp(row["updated_at"])).total_seconds() <= 60:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        version = self.observe("forecast_inputs", asset, context, now=now)
        cohort = digest({"method": METHOD_VERSION, "engine": engine, "policy": policy})[:16]
        due = now + timedelta(seconds=HORIZONS[horizon])
        identifier = uuid.uuid4().hex
        payload = {"id": identifier, "asset": asset, "symbol": quote["symbol"], "horizon": horizon,
                   "created_at": iso_utc(now), "signal_generated_at": row["updated_at"], "due_at": iso_utc(due),
                   "up_probability": probability, "direction": 1 if probability >= .58 else -1 if probability <= .42 else 0,
                   "reference_quote": quote, "signal": row, "policy": policy, "engine": engine,
                   "cohort": cohort, "method": METHOD_VERSION, "input_id": version["id"], "input_sha256": version["sha256"],
                   "entry_not_before": iso_utc(now+timedelta(seconds=policy["latency_seconds"])),
                   "entry_deadline": iso_utc(now+timedelta(seconds=policy["latency_seconds"]+ENTRY_GRACE)),
                   "exit_deadline": iso_utc(due+timedelta(seconds=EXIT_GRACE))}
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT 1 FROM forecasts f LEFT JOIN forecast_outcomes o ON o.forecast_id=f.id WHERE f.asset=? AND f.horizon=? AND (o.forecast_id IS NULL OR f.due_at>?)", (asset, horizon,iso_utc(now))).fetchone()
            if active:
                return None
            db.execute("INSERT INTO forecasts VALUES(?,?,?,?,?,?,?,?)", (identifier, asset, horizon, version["id"], iso_utc(now), iso_utc(due), cohort, canonical(payload)))
        return identifier

    def active_slots(self, *, now: datetime | None = None) -> set[tuple[str, str]]:
        with self.session() as db:
            return {(r["asset"], r["horizon"]) for r in db.execute("SELECT asset,horizon FROM forecasts f LEFT JOIN forecast_outcomes o ON o.forecast_id=f.id WHERE o.forecast_id IS NULL OR f.due_at>?",(iso_utc(now or utc_now()),))}

    def advance(self, quotes: list[dict], *, now: datetime | None = None) -> None:
        now = now or utc_now()
        by_asset = {q.get("key"): q for q in quotes}
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT f.payload, e.payload AS entry FROM forecasts f LEFT JOIN forecast_entries e ON e.forecast_id=f.id LEFT JOIN forecast_outcomes o ON o.forecast_id=f.id WHERE o.forecast_id IS NULL").fetchall()
            for row in rows:
                prediction = json.loads(row["payload"])
                identifier = prediction["id"]
                quote = by_asset.get(prediction["asset"], {})
                entry = json.loads(row["entry"]) if row["entry"] else None
                outcome = None
                if entry is None:
                    deadline = timestamp(prediction["entry_deadline"])
                    if now > deadline:
                        outcome = {"status": "missed_entry", "reason": "未在记录完成后的入场窗口收到新鲜报价，未事后补价"}
                    elif (valid_quote(quote, now, not_before=timestamp(prediction["entry_not_before"]))
                          and quote.get("source") == prediction["reference_quote"].get("source")
                          and quote.get("symbol") == prediction["symbol"]):
                        entry = {"quote": quote, "observed_at": iso_utc(now),
                                 "actual_delay_seconds": (now-timestamp(prediction["created_at"])).total_seconds()}
                        db.execute("INSERT INTO forecast_entries VALUES(?,?,?)", (identifier, iso_utc(now), canonical(entry)))
                if entry is not None:
                    due = timestamp(prediction["due_at"])
                    if now > timestamp(prediction["exit_deadline"]):
                        outcome = {"status": "missed_exit", "reason": "到期窗口内没有可用报价，未用恢复连接后的价格补算"}
                    elif (now >= due and valid_quote(quote, now, not_before=due)
                          and quote.get("source") == entry["quote"].get("source") and quote.get("symbol") == prediction["symbol"]):
                        market_return = (quote["price"]/prediction["reference_quote"]["price"]-1)*100
                        actual_up = int(market_return > 0)
                        direction = prediction["direction"]
                        outcome = {"status": "evaluated", "quote": quote, "observed_at": iso_utc(now),
                                   "exit_lag_seconds": (now-due).total_seconds(), "actual_return_pct": market_return,
                                   "actual_up": actual_up, "hit": direction*(1 if market_return > 0 else -1 if market_return < 0 else 0) > 0 if direction else None,
                                   "brier": (prediction["up_probability"]-actual_up)**2,
                                   **paper_return(direction, entry["quote"]["price"], quote["price"], prediction["policy"])}
                if outcome:
                    outcome["recorded_at"] = iso_utc(now)
                    db.execute("INSERT INTO forecast_outcomes VALUES(?,?,?,?)", (identifier, iso_utc(now), outcome["status"], canonical(outcome)))

    def forecasts(self, *, asset: str = "all", horizon: str = "all", status: str = "all", limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        conditions, args = [], []
        if asset != "all": conditions.append("f.asset=?"); args.append(asset)
        if horizon != "all": conditions.append("f.horizon=?"); args.append(horizon)
        if status == "pending": conditions.append("o.forecast_id IS NULL")
        elif status == "missing": conditions.append("o.status IN ('missed_entry','missed_exit')")
        elif status == "evaluated": conditions.append("o.status='evaluated'")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        tables = " FROM forecasts f LEFT JOIN forecast_entries e ON e.forecast_id=f.id LEFT JOIN forecast_outcomes o ON o.forecast_id=f.id"
        with self.session() as db:
            total = db.execute("SELECT COUNT(*)"+tables+where, args).fetchone()[0]
            rows = db.execute("SELECT f.payload,e.payload AS entry,o.payload AS outcome"+tables+where+" ORDER BY f.created_at DESC LIMIT ? OFFSET ?", (*args, max(1,min(limit,10000)), max(0,offset))).fetchall()
        return [self._forecast_row(r) for r in rows], total

    @staticmethod
    def _forecast_row(row) -> dict:
        result = json.loads(row["payload"])
        result["entry"] = json.loads(row["entry"]) if row["entry"] else None
        result["outcome"] = json.loads(row["outcome"]) if row["outcome"] else None
        result["status"] = result["outcome"]["status"] if result["outcome"] else "tracking" if result["entry"] else "pending_entry"
        return result

    def detail(self, identifier: str) -> dict:
        with self.session() as db:
            row = db.execute("SELECT f.payload,e.payload AS entry,o.payload AS outcome,v.payload AS inputs,v.observed_at FROM forecasts f JOIN input_versions v ON v.id=f.input_id LEFT JOIN forecast_entries e ON e.forecast_id=f.id LEFT JOIN forecast_outcomes o ON o.forecast_id=f.id WHERE f.id=?", (identifier,)).fetchone()
        if not row: raise ValueError("记录不存在")
        return {**self._forecast_row(row), "inputs": json.loads(row["inputs"]), "input_observed_at": row["observed_at"]}

    def inputs(self, kind: str = "all", limit: int = 50, offset: int = 0) -> dict:
        where, args = (" WHERE v.kind=?", [kind]) if kind != "all" else ("", [])
        with self.session() as db:
            total = db.execute("SELECT COUNT(*) FROM input_versions v"+where, args).fetchone()[0]
            rows = db.execute("SELECT v.*, (SELECT MIN(observed_at) FROM input_versions p WHERE p.kind=v.kind AND p.identity=v.identity) AS first_received_at, (SELECT COUNT(*) FROM analysis_records a WHERE a.input_id=v.id) AS analysis_count FROM input_versions v"+where+" ORDER BY v.id DESC LIMIT ? OFFSET ?", (*args,max(1,min(limit,200)),max(0,offset))).fetchall()
        return {"items": [{**dict(r), "payload": json.loads(r["payload"])} for r in rows], "total": total}

    def input_detail(self, identifier: int) -> dict:
        with self.session() as db:
            row = db.execute("SELECT * FROM input_versions WHERE id=?", (identifier,)).fetchone()
            if not row: raise ValueError("输入版本不存在")
            versions = db.execute("SELECT id,version,observed_at,sha256 FROM input_versions WHERE kind=? AND identity=? ORDER BY version", (row["kind"],row["identity"])).fetchall()
            analyses = db.execute("SELECT * FROM analysis_records WHERE input_id=? ORDER BY completed_at", (identifier,)).fetchall()
        return {**dict(row), "payload": json.loads(row["payload"]), "versions": [dict(v) for v in versions],
                "analyses": [{**dict(a), "metadata": json.loads(a["metadata"]), "output": json.loads(a["output"])} for a in analyses]}

    def cards(self, *, search: str = "", stance: str = "all", offset: int = 0) -> dict:
        if stance not in {"all", "bullish", "neutral", "bearish"} or offset < 0:
            raise ValueError("研究筛选条件不正确")
        with self.session() as db:
            rows = db.execute("""SELECT v.* FROM input_versions v WHERE v.kind='research_card'
                AND v.id=(SELECT MAX(p.id) FROM input_versions p WHERE p.kind=v.kind AND p.identity=v.identity)
                ORDER BY v.id DESC""").fetchall()
        cards = [{**json.loads(row["payload"]), "version": row["version"], "input_version_id": row["id"],
                  "observed_at": row["observed_at"]} for row in rows]
        cards = [card for card in cards if (stance == "all" or card["stance"] == stance)
                 and search.casefold() in f"{card['name']} {card['target']}".casefold()]
        return {"items": cards[offset:offset+50], "total": len(cards), "offset": offset, "page_size": 50}

    def save_card(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("研究内容格式不正确")
        identifier = payload.get("id") or uuid.uuid4().hex
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("研究标识不正确")
        version = payload.get("base_version", 0)
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError("研究版本不正确")
        def field(data, name, maximum=10000):
            value = data.get(name, "")
            if not isinstance(value, str) or len(value) > maximum:
                raise ValueError(f"{name} 须为文字，且不超过 {maximum} 字符")
            return value.strip()
        def checked_date(value):
            if value:
                try: date.fromisoformat(value)
                except ValueError: raise ValueError("资料日期须为有效日期") from None
            return value
        card = {key: field(payload, key) for key in ("business", "facts", "fundamentals", "valuation", "macro", "strategic_fit",
                                                     "risks", "review", "horizon", "expected_baseline")}
        card.update(id=identifier, name=field(payload, "name", 120), target=field(payload, "target", 120),
                    kind=field(payload, "kind", 20), stance=field(payload, "stance", 20),
                    data_as_of=checked_date(field(payload, "data_as_of", 10)))
        if not card["name"] or not card["target"] or card["kind"] not in {"cvc", "stock", "asset"} or card["stance"] not in {"bullish", "neutral", "bearish"}:
            raise ValueError("请填写公司或标的名称、标识，并选择研究类型和情景判断")
        scenarios = payload.get("scenarios", {})
        if not isinstance(scenarios, dict):
            raise ValueError("情景格式不正确")
        card["scenarios"] = {}
        for stance in ("bullish", "neutral", "bearish"):
            values = scenarios.get(stance, {})
            if not isinstance(values, dict): raise ValueError("情景格式不正确")
            card["scenarios"][stance] = {key: field(values, key) for key in ("thesis", "conditions", "invalidation")}
        evidence = payload.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) > 50:
            raise ValueError("证据须为列表，最多 50 条")
        card["evidence"] = []
        for row in evidence:
            if not isinstance(row, dict): raise ValueError("证据格式不正确")
            url = field(row, "url", 2048)
            try: parsed = urlsplit(url)
            except ValueError: raise ValueError("证据链接格式不正确") from None
            role = field(row, "role", 20)
            excerpt = field(row, "excerpt", 4000)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or role not in {"support", "counter"} or not excerpt:
                raise ValueError("每条证据须有 HTTP(S) 原文链接、摘录及证据/反证类型")
            card["evidence"].append({"url": url, "excerpt": excerpt, "role": role,
                                     "as_of": checked_date(field(row, "as_of", 10))})
        complete = (all(all(scenario.values()) for scenario in card["scenarios"].values())
                    and all(card[key] for key in ("facts", "fundamentals", "valuation", "macro", "risks", "horizon")))
        card["assessment_status"] = "conditional" if complete and card["evidence"] and card["data_as_of"] else "insufficient"
        card["baseline_status"] = "provided_unverified" if card["expected_baseline"] else "missing"
        card["score_kind"] = "conditional_research_assessment"
        saved = self.observe("research_card", identifier, card, expected_version=version)
        return {**card, "version": saved["version"], "input_version_id": saved["id"], "observed_at": saved["observed_at"]}

    def statistics(self, *, asset: str = "all", horizon: str = "all") -> dict:
        conditions, args = [], []
        if asset != "all": conditions.append("f.asset=?"); args.append(asset)
        if horizon != "all": conditions.append("f.horizon=?"); args.append(horizon)
        where = " WHERE "+" AND ".join(conditions) if conditions else ""
        with self.session() as db:
            rows = db.execute("SELECT f.payload,e.payload AS entry,o.payload AS outcome FROM forecasts f LEFT JOIN forecast_entries e ON e.forecast_id=f.id LEFT JOIN forecast_outcomes o ON o.forecast_id=f.id"+where, args).fetchall()
            input_count = db.execute("SELECT COUNT(*) FROM input_versions").fetchone()[0]
            analysis_count = db.execute("SELECT COUNT(*) FROM analysis_records").fetchone()[0]
        groups = {}
        totals = {"total": len(rows), "pending": 0, "evaluated": 0, "missing": 0, "input_count": input_count, "analysis_count": analysis_count}
        for raw in rows:
            row = self._forecast_row(raw)
            key = (row["asset"],row["horizon"],row["cohort"])
            group = groups.setdefault(key, {"asset":key[0], "horizon":key[1], "cohort":key[2], "policy":row["policy"], "engine":row["engine"], "total":0, "evaluated":0, "pending":0, "missing":0, "trades":0, "hits":0, "brier_sum":0., "baseline_brier_sum":0., "net_sum":0., "gross_sum":0., "bins":[{"low":i/10,"high":(i+1)/10,"count":0,"up":0,"predicted_sum":0.} for i in range(10)]})
            group["total"] += 1
            outcome = row["outcome"]
            category = "pending" if outcome is None else "evaluated" if outcome["status"] == "evaluated" else "missing"
            totals[category] += 1; group[category] += 1
            if category != "evaluated": continue
            group["brier_sum"] += outcome["brier"]
            group["baseline_brier_sum"] += (.5-outcome["actual_up"])**2
            group["net_sum"] += outcome["net_return_pct"]
            group["gross_sum"] += outcome["gross_return_pct"]
            if row["direction"]:
                group["trades"] += 1; group["hits"] += int(outcome["hit"])
            bucket = group["bins"][min(9,int(row["up_probability"]*10))]
            bucket["count"] += 1; bucket["up"] += outcome["actual_up"]; bucket["predicted_sum"] += row["up_probability"]
        for group in groups.values():
            n = group["evaluated"]
            group["hit_rate"] = group["hits"]/group["trades"] if group["trades"] else None
            group["brier"] = group.pop("brier_sum")/n if n else None
            group["baseline_brier"] = group.pop("baseline_brier_sum")/n if n else None
            group["mean_net_return_pct"] = group.pop("net_sum")/n if n else None
            group["mean_gross_return_pct"] = group.pop("gross_sum")/n if n else None
            for bucket in group["bins"]:
                bucket["actual_up_rate"] = bucket["up"]/bucket["count"] if bucket["count"] else None
                bucket["mean_prediction"] = bucket.pop("predicted_sum")/bucket["count"] if bucket["count"] else None
        return {"summary": totals, "groups": list(groups.values())}


class RecordingAnalyzer:
    def __init__(self, analyzer, ledger: ResearchLedger):
        self.analyzer, self.ledger = analyzer, ledger
        self.name = analyzer.name
        self.analysis_id = getattr(analyzer,"analysis_id",analyzer.name)
        self.code_sha256 = engine_fingerprint("analyzers.py", "models.py", "stock_mentions.py")

    def analyze(self, item: NewsItem):
        version = self.ledger.observe_news(item)
        started = utc_now()
        try:
            result = self.analyzer.analyze(item)
        except Exception as exc:
            completed = utc_now()
            self.ledger.record_analysis(version["id"],f"{item.market}:{item.symbol}",
                {"status":"failed","error_type":type(exc).__name__},started_at=started,completed_at=completed,engine=self.analysis_id,
                metadata={"code_sha256":self.code_sha256,"latency_seconds":(completed-started).total_seconds()})
            raise
        completed = utc_now()
        self.ledger.record_analysis(version["id"], f"{item.market}:{item.symbol}", {**asdict(result), "score":result.score},
                                   started_at=started, completed_at=completed, engine=result.analyzer,
                                   metadata={"target_name":item.target_name, "target_aliases":item.target_aliases,
                                             "requested_model":getattr(self.analyzer,"model",None), "analysis_id":self.analysis_id,
                                             "code_sha256":self.code_sha256, "latency_seconds":(completed-started).total_seconds(),
                                             "evaluation_role":"observed_sentiment_not_timed_forecast", **getattr(result,"metadata",{})})
        return result
