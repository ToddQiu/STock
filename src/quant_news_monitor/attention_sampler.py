"""Bounded, receipt-time samples. Closed UTC days are never rewritten."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone

from .research_ledger import canonical, timestamp

NAMES = {"xiaohongshu": "小红书", "douyin": "抖音", "x": "X", "threads": "Threads"}
VERSION = "receipt-day-v2"


class AttentionSampler:
    def __init__(self, ledger):
        self.ledger = ledger
        self.path = ledger.path.with_name("attention.db")
        self.lock = threading.RLock()
        self.next_due = datetime.min.replace(tzinfo=timezone.utc)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS seen (
                    fingerprint TEXT PRIMARY KEY, day TEXT NOT NULL, platform TEXT NOT NULL,
                    cohort TEXT NOT NULL, author TEXT NOT NULL, targets TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS seen_day ON seen(day);
                CREATE INDEX IF NOT EXISTS seen_author ON seen(day,platform,cohort,author);
                CREATE TABLE IF NOT EXISTS samples (
                    day TEXT NOT NULL, platform TEXT NOT NULL, cohort TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(day,platform,cohort));
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def tick(self, policy, now=None, health=None):
        now = now or datetime.now(timezone.utc)
        stamp, day = now.isoformat(), now.date().isoformat()
        cohort = hashlib.sha256(canonical(policy).encode()).hexdigest()[:16]
        with self.lock, closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            state = dict(db.execute("SELECT key,value FROM state"))
            if state.get("last") and (now-timestamp(state["last"])).total_seconds() < 60:
                self.next_due = timestamp(state["last"])+timedelta(seconds=60)
                return
            cutoff = (now-timedelta(days=1)).isoformat()
            # Recent inputs are bounded independently of the main ledger's lifetime.
            with self.ledger.session() as source:
                rows = []
                for kind in ("news", "social"):
                    rows.extend(dict(r) for r in source.execute("""SELECT v.*,
                        (SELECT json_group_array(DISTINCT target) FROM analysis_records a
                         WHERE a.input_id=v.id AND a.completed_at<=?) AS targets
                        FROM input_versions v WHERE kind=? ORDER BY id DESC LIMIT 1001""", (stamp, kind)))
            active = {"新闻", *(NAMES.get(p, p) for p in policy.get("platforms", []))}
            truncated = set()
            fresh = []
            for record in rows:
                post = json.loads(record["payload"])
                platform = "新闻" if record["kind"] == "news" else NAMES.get(post.get("platform"), post.get("platform"))
                if platform not in active:
                    continue
                try:
                    if not timestamp(cutoff) <= timestamp(post.get("published_at")) <= now or timestamp(record["observed_at"]) > now:
                        continue
                except (ValueError, TypeError):
                    continue
                fresh.append((record, post, platform))
            for kind in ("news", "social"):
                recent = [r for r, _, _ in fresh if r["kind"] == kind]
                if len(recent) >= 1001:
                    truncated.update(p for r, _, p in fresh if r["kind"] == kind)
            day_count = db.execute("SELECT COUNT(*) FROM seen WHERE day=?", (day,)).fetchone()[0]
            for record, post, platform in reversed(fresh):
                text = post.get("text") or post.get("title") or post.get("excerpt") or post.get("url")
                if not text:
                    continue
                fingerprint = hashlib.sha256((platform+"|"+re.sub(r"\s+", "", str(text)).casefold()).encode()).hexdigest()
                targets = set(json.loads(record["targets"]))
                if post.get("market") and post.get("symbol"):
                    targets.add(post["market"]+":"+post["symbol"])
                prior = db.execute("SELECT day,targets,cohort FROM seen WHERE fingerprint=?", (fingerprint,)).fetchone()
                if prior:
                    if prior[0] == day and prior[2] == cohort:
                        targets.update(json.loads(prior[1]))
                        db.execute("UPDATE seen SET targets=? WHERE fingerprint=?", (canonical(sorted(targets)), fingerprint))
                    continue
                if day_count >= 20000:
                    truncated.add(platform)
                    continue
                author = str(post.get("author") or "").strip().casefold() if platform != "新闻" else ""
                if author and db.execute("SELECT COUNT(*) FROM seen WHERE day=? AND platform=? AND cohort=? AND author=?", (day, platform, cohort, author)).fetchone()[0] >= 3:
                    continue
                db.execute("INSERT INTO seen VALUES(?,?,?,?,?,?)", (fingerprint, day, platform, cohort, author, canonical(sorted(targets))))
                day_count += 1
            # Sealing uses actual wall time, never an invented historical publication time.
            for d, p, c, raw in db.execute("SELECT * FROM samples WHERE day<? AND json_extract(payload,'$.closed')=0", (day,)).fetchall():
                sample = json.loads(raw)
                sample.update(closed=True, observed_at=stamp, complete=sample["checks"] >= 1296 and sample.get("healthy_checks", 0) >= 1296 and not sample["truncated"])
                db.execute("UPDATE samples SET payload=? WHERE day=? AND platform=? AND cohort=?", (canonical(sample), d, p, c))
            minute = stamp[:16]
            for platform in active:
                prior = db.execute("SELECT payload FROM samples WHERE day=? AND platform=? AND cohort=?", (day, platform, cohort)).fetchone()
                sample = json.loads(prior[0]) if prior else {"date": day, "platform": platform, "cohort": cohort, "checks": 0, "closed": False,
                    "complete": False, "truncated": False, "healthy_checks": 0, "method": VERSION, "source": "STock 首次采到样本",
                    "coverage": "UTC日内首次采到的去重样本；同社交账号每日最多3条；非全平台热度", "source_url": "", "counts": {}, "total": 0}
                items = db.execute("SELECT targets FROM seen WHERE day=? AND platform=? AND cohort=?", (day, platform, cohort)).fetchall()
                counts = {}
                for (raw,) in items:
                    for target in json.loads(raw):
                        counts[target] = counts.get(target, 0)+1
                source_ok = (health or {}).get(platform, False)
                sample.update(counts=counts, total=len(items), observed_at=stamp, last_minute=minute,
                              covered_targets=sorted(":".join(w) for w in policy.get("watches", [])),
                              healthy_checks=sample.get("healthy_checks", 0)+int(source_ok and sample.get("last_minute") != minute),
                              checks=sample["checks"]+int(sample.get("last_minute") != minute), truncated=sample["truncated"] or platform in truncated)
                db.execute("INSERT OR REPLACE INTO samples VALUES(?,?,?,?)", (day, platform, cohort, canonical(sample)))
            db.execute("DELETE FROM seen WHERE day<?", ((now-timedelta(days=7)).date().isoformat(),))
            db.execute("DELETE FROM samples WHERE day<?", ((now-timedelta(days=400)).date().isoformat(),))
            db.execute("INSERT OR REPLACE INTO state VALUES('last',?)", (stamp,))
            self.next_due = now+timedelta(seconds=60)

    def seconds_until_due(self, now=None):
        return max(0, (self.next_due-(now or datetime.now(timezone.utc))).total_seconds())

    def snapshot(self, target):
        with self.lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            samples = [json.loads(r[0]) for r in db.execute("SELECT payload FROM samples ORDER BY day DESC,platform LIMIT 4000")]
            last = db.execute("SELECT value FROM state WHERE key='last'").fetchone()
        rows = [{**{k: v for k, v in r.items() if k not in {"counts", "covered_targets"}}, "target": target,
                 "count": r["counts"].get(target, 0) if target in r.get("covered_targets", []) else None} for r in samples]
        return {"rows": rows, "last_sample_at": last[0] if last else None, "interval_seconds": 60, "method": VERSION,
                "note": "每分钟检查本机已入库证据，按首次采到日累计；同一帖子不重复贡献。完整日需至少90%分钟检查且来源健康；不足的日子只展示，不用于模型。新闻采集健康未核验时不参与模型。未接入的平台、当日未覆盖的公司不生成零热度。"}
