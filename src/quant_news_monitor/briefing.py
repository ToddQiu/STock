"""Local, source-linked pre-open briefings and post-close reviews.

Reports are frozen snapshots. This is not a general web-search agent and does
not execute the commands, HTML assemblers, quotas or predictions in a skill.
"""
from __future__ import annotations

import copy
import json
import math
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .config import AnalysisConfig, analysis_config_from_raw
from .config_store import ConfigStore
from .market_sessions import CalendarUnavailable, MARKETS, SessionCalendar
from .portfolio_book import all_positions
from .portfolio_chart import fetch_portfolio_chart


DEFAULT_SETTINGS = {"enabled": True, "morning_minutes_before_open": 30, "close_minutes_after_close": 15,
                    "auto_export_pdf": True, "morning_time": "08:30", "close_time": "18:00"}
REPORT_MARKETS = {**MARKETS, "GLOBAL": {"name": "综合", "timezone": "Asia/Shanghai"}}
INDEXES = {
    "CN": [("000001.SS", "上证指数"), ("000300.SS", "沪深300")],
    "HK": [("^HSI", "恒生指数")],
    "US": [("^GSPC", "标普500"), ("^IXIC", "纳斯达克综合")],
    "KR": [("^KS11", "韩国综合指数")],
}
MAX_QUOTES = 64
MAX_NEWS = 200


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _time(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _link(value):
    value = str(value or "")
    return value if urlsplit(value).scheme in {"http", "https"} else ""


def relevant_targets(raw):
    """All portfolios plus the explicit whitelist; ordinary watches alone do not schedule."""
    result = {}
    for role, rows in (("holding", all_positions(raw)), ("whitelist", raw.get("stock_whitelist", []))):
        for row in rows:
            market, symbol = str(row.get("market", "")).upper(), str(row.get("symbol", "")).upper()
            if market not in MARKETS or not symbol:
                continue
            key = (market, symbol)
            if key not in result:
                result[key] = {"market": market, "symbol": symbol, "name": str(row.get("name") or symbol), "role": role}
                if sector := row.get("sector") or (row.get("plan") or {}).get("sector"):
                    result[key]["sector"] = str(sector)[:80]
            elif role == "whitelist":
                result[key]["in_whitelist"] = True
    return list(result.values())


def select_news(rows, market, start, cutoff, targets, *, include_overseas=False):
    """Only inputs known by cutoff may influence a frozen briefing."""
    keys = {(row["market"], row["symbol"]) for row in targets}
    selected, seen = [], set()
    for row in sorted(rows, key=lambda item: str(item.get("published_at") or ""), reverse=True):
        published, discovered, analyzed = (_time(row.get(key)) for key in ("published_at", "discovered_at", "analyzed_at"))
        row_market = row.get("market")
        if row_market not in MARKETS or (row_market != market and not include_overseas) or published is None or discovered is None:
            continue
        if not start < published <= cutoff or discovered > cutoff:
            continue
        identity = str(row.get("event_key") or row.get("fingerprint") or row.get("url") or "")
        # The same event can legitimately belong to several tracked companies.
        identity = (identity, row_market, row.get("symbol"))
        if not identity[0] or identity in seen:
            continue
        seen.add(identity)
        score = _number(row.get("score"))
        supported = (analyzed is not None and analyzed <= cutoff and row.get("evidence_status") == "supported"
                     and row.get("event_type") not in {None, "other"} and score is not None
                     and bool(row.get("analysis_summary")))
        signal = "unknown" if not supported else "bullish" if score > .04 else "bearish" if score < -.04 else "neutral"
        selected.append({
            "market": row_market, "symbol": str(row.get("symbol") or ""), "title": str(row.get("title") or "")[:600],
            "event_key": str(row.get("event_key") or row.get("fingerprint") or row.get("url") or ""),
            "excerpt": str(row.get("news_summary") or "")[:4000],
            "source": str(row.get("source") or "未标明来源"), "url": _link(row.get("url")),
            "published_at": _iso(published), "discovered_at": _iso(discovered),
            "analyzed_at": _iso(analyzed) if analyzed and analyzed <= cutoff else None,
            "signal": signal, "score": score if supported else None,
            "analysis": str(row.get("analysis_summary") or "")[:1600] if supported else "证据或分析时点不足，方向待核实",
            "event_type": str(row.get("event_type") or "other"),
            "related": (row_market, row.get("symbol")) in keys, "background": row_market != market,
        })
    selected.sort(key=lambda row: (row["related"], row["published_at"]), reverse=True)
    return selected[:MAX_NEWS], len(selected)


def closing_quote(target, session, previous, chart):
    rows = {str(row.get("date")): row for row in chart.get("candles", [])}
    current = rows.get(session.date.isoformat())
    prior = rows.get(previous.date.isoformat())
    close = _number(current.get("close")) if current else None
    before = _number(prior.get("close")) if prior else None
    result = {**target, "session_date": session.date.isoformat(), "close": None, "previous_close": None,
              "change_pct": None, "currency": str(chart.get("currency") or ""),
              "source": str(chart.get("source") or "未标明来源"), "source_url": _link(chart.get("source_url")),
              "as_of": _iso(session.close), "status": "unavailable", "reason": "未取得对应交易日的有效收盘价"}
    quote_clock = _time(chart.get("source_quote_at"))
    result["source_quote_at"] = _iso(quote_clock) if quote_clock else None
    if chart.get("stale") or chart.get("_startup_cache") or current and (current.get("partial") or current.get("complete") is False):
        result["reason"] = "来源标记为旧快照或未完成日K，未当作收盘数据"
        return result
    if close is None or close <= 0:
        return result
    # A later daily candle confirms this is a historical completed bar. For the
    # latest bar, require a source clock at/after the close; a wall-clock
    # after close alone cannot turn a stale intraday price into a closing price.
    later_bar = any(key > session.date.isoformat() for key in rows)
    if not later_bar and (quote_clock is None or quote_clock < session.close):
        result["reason"] = "来源尚未确认更新至收盘，未把盘中日K作为收盘价"
        return result
    result.update(close=close, status="partial", reason="前一交易日收盘缺失，未计算涨跌幅")
    for field, bar in (("volume", current), ("previous_volume", prior)):
        value = _number((bar or {}).get("volume"))
        if value is not None and value > 0:
            result[field] = value
    if before is not None and before > 0:
        result.update(previous_close=before, change_pct=(close / before - 1) * 100, status="ready", reason="")
    return result


def focus_signals(targets, news):
    result = []
    for target in targets:
        related = [row for row in news if row["market"] == target["market"] and row["symbol"] == target["symbol"]
                   and row["signal"] != "unknown"]
        positive = sum(row["signal"] == "bullish" for row in related)
        negative = sum(row["signal"] == "bearish" for row in related)
        if not related:
            signal, reason = "unknown", "窗口内没有足够的已核实新闻方向"
        elif positive and negative:
            signal, reason = "mixed", f"利多{positive}条、利空{negative}条，消息存在分歧"
        elif positive:
            signal, reason = "bullish", f"{positive}条已核实利多消息；仍需观察实际价格反应"
        elif negative:
            signal, reason = "bearish", f"{negative}条已核实利空消息；仍需观察实际价格反应"
        else:
            signal, reason = "neutral", "已核实消息未给出明确方向"
        result.append({**target, "signal": signal, "reason": reason, "news_count": len(related)})
    return result


def review_morning(morning, quotes, session_date):
    result = {"status": "unavailable", "session_date": session_date,
              "note": f"没有{session_date}按时完成的盘前报告，不能事后补造观点", "items": [], "checked": 0, "matched": 0}
    if not morning or not morning.get("is_preopen") or morning.get("session_date") != session_date:
        return result
    by_symbol = {(row["market"], row["symbol"]): row for row in quotes}
    for row in morning.get("focus", []):
        if row["signal"] not in {"bullish", "bearish"}:
            continue
        quote = by_symbol.get((row["market"], row["symbol"])) or {}
        change = _number(quote.get("change_pct"))
        valid = quote.get("session_date") == session_date and quote.get("status") == "ready" and change is not None
        # A zero return is neither a fulfilled bullish nor bearish direction.
        matched = (change > 0 if row["signal"] == "bullish" else change < 0) if valid else None
        result["items"].append({"symbol": row["symbol"], "name": row["name"], "signal": row["signal"],
                                "change_pct": change if valid else None, "matched": matched,
                                "status": "checked" if valid else "missing_quote"})
        result["checked"] += int(valid)
        result["matched"] += int(matched is True)
    result["status"] = "ready" if result["checked"] else "unavailable"
    result["note"] = "盘前消息方向与本日收盘涨跌对照；不是收益预测准确率，也不证明消息导致涨跌"
    return result


class BriefingMonitor:
    def __init__(self, config_path, context_provider, *, chart_fetcher=None, calendar=None, now=None, pdf_writer=None, narrative_builder=None, market_news_fetcher=None, week_calendar_fetcher=None):
        self.config_store = ConfigStore(config_path)
        self.context_provider = context_provider
        self.chart_fetcher = chart_fetcher or fetch_portfolio_chart
        self.calendar = calendar or SessionCalendar()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.pdf_writer = pdf_writer
        self.narrative_builder = narrative_builder
        self.market_news_fetcher = market_news_fetcher
        self.week_calendar_fetcher = week_calendar_fetcher
        raw = self.config_store.read()
        database = Path(raw.get("storage", {}).get("database") or "data/monitor.db")
        if not database.is_absolute():
            database = Path(config_path).resolve().parent / database
        self.path = database.parent / "briefing.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._scheduler = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="briefing")
        self._pdf_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="briefing-pdf")
        self._active = set()
        self._pdf_active = set()
        self._schedules = {}
        self._last_checked = None
        self._error = None
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS briefing_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS briefing_reports (id TEXT PRIMARY KEY, market TEXT NOT NULL, kind TEXT NOT NULL, session_date TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT, error TEXT, payload TEXT, UNIQUE(market,kind,session_date))")
            if "revision" not in {row[1] for row in db.execute("PRAGMA table_info(briefing_reports)")}:
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE briefing_reports_v2 (id TEXT PRIMARY KEY, market TEXT NOT NULL, kind TEXT NOT NULL, session_date TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT, error TEXT, payload TEXT, revision INTEGER NOT NULL DEFAULT 1, UNIQUE(market,kind,session_date,revision))")
                db.execute("INSERT INTO briefing_reports_v2 SELECT *,1 FROM briefing_reports")
                db.execute("DROP TABLE briefing_reports")
                db.execute("ALTER TABLE briefing_reports_v2 RENAME TO briefing_reports")
            db.execute("CREATE TABLE IF NOT EXISTS briefing_pdf_exports (report_id TEXT PRIMARY KEY, status TEXT NOT NULL, filename TEXT, error TEXT, exported_at TEXT)")
            db.execute("UPDATE briefing_reports SET status='failed',error='上次运行在生成完成前中断，可手动重试' WHERE status IN ('queued','running')")
            db.execute("UPDATE briefing_pdf_exports SET status='failed',error='上次 PDF 导出中断，可重新导出' WHERE status IN ('queued','running')")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self._db() as db:
            row = db.execute("SELECT payload FROM briefing_settings WHERE id=1").fetchone()
        return {**DEFAULT_SETTINGS, **(json.loads(row[0]) if row else {})}

    def update_settings(self, values):
        if not isinstance(values, dict) or set(values) - set(DEFAULT_SETTINGS):
            raise ValueError("早晚报设置字段无效")
        with self._lock:
            settings = self.settings()
            for key, value in values.items():
                if key in {"enabled", "auto_export_pdf"}:
                    if not isinstance(value, bool):
                        raise ValueError("自动早晚报开关必须是布尔值")
                elif key in {"morning_time", "close_time"}:
                    from datetime import time as clock
                    if not isinstance(value, str) or len(value) != 5:
                        raise ValueError("自动时间须为北京时间HH:MM")
                    try:
                        clock.fromisoformat(value)
                    except ValueError as error:
                        raise ValueError("自动时间须为北京时间HH:MM") from error
                elif isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 180:
                    raise ValueError("盘前提前与盘后等待时间须为5至180整数分钟")
                settings[key] = value
            with self._db() as db:
                db.execute("INSERT OR REPLACE INTO briefing_settings VALUES (1,?)", (json.dumps(settings),))
        self._wake.set()
        return settings

    def start(self):
        with self._lock:
            if self._scheduler and self._scheduler.is_alive():
                return
            self._scheduler = threading.Thread(target=self._loop, name="briefing-schedule", daemon=True)
            self._scheduler.start()

    def shutdown(self):
        self._stop.set()
        self._wake.set()
        if self._scheduler:
            self._scheduler.join(timeout=1)
        self._executor.shutdown(wait=False, cancel_futures=True)
        # PDF work is local and bounded by the frozen report. Finish the current
        # write before releasing its SQLite handle or allowing a restart.
        self._pdf_executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._pdf_active.clear()

    def _summary(self, row, pdf=None):
        value = dict(row)
        report = json.loads(value.pop("payload")) if value.get("payload") else {}
        value.pop("payload", None)
        value.update(market_name=REPORT_MARKETS[value["market"]]["name"], late=report.get("late", False),
                     news_count=len(report.get("news", [])), quote_count=report.get("coverage", {}).get("quote_available", 0))
        value["pdf"] = self._pdf_metadata(value, pdf)
        return value

    def _pdf_path(self, record):
        day = date.fromisoformat(record["session_date"])
        report_id = str(record["id"])
        if record["market"] not in REPORT_MARKETS or record["kind"] not in {"morning", "close"} or not report_id or any(not (char.isascii() and (char.isalnum() or char in "_-")) for char in report_id):
            raise ValueError("报告标识无效，无法导出 PDF")
        filename = f"STock-{record['market']}-{day.isoformat()}-{record['kind']}-{report_id}.pdf"
        return self.path.parent / "reports" / f"{day.year:04d}" / f"{day.month:02d}" / filename

    def _pdf_metadata(self, record, row):
        value = {"status": "not_exported", "filename": None, "error": None, "exported_at": None}
        if row is not None:
            value.update({key: row[key] for key in value})
            if value["status"] == "ready" and not self._pdf_path(record).is_file():
                value.update(status="failed", error="PDF 文件不存在或未随数据迁移，请重新导出")
        return value

    def snapshot(self, limit=30):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("报告数量须为1至100")
        targets = relevant_targets(self.config_store.read())
        markets = []
        for market, info in MARKETS.items():
            count = sum(row["market"] == market for row in targets)
            with self._lock:
                schedule = copy.deepcopy(self._schedules.get(market, {}))
            markets.append({"market": market, "name": info["name"], "timezone": info["timezone"], "target_count": count,
                            "calendar_status": "included", "calendar_note": "纳入综合报告，交易时段与收盘日期以本期内容为准", **schedule})
        with self._db() as db:
            rows = db.execute("SELECT * FROM briefing_reports ORDER BY created_at DESC,id DESC LIMIT ?", (limit,)).fetchall()
            pdf_rows = {row["report_id"]: row for row in db.execute("SELECT * FROM briefing_pdf_exports")}
        with self._lock:
            runtime = {"running": bool(self._active or self._pdf_active), "active_ids": sorted(self._active), "pdf_active_ids": sorted(self._pdf_active),
                       "last_checked_at": self._last_checked, "error": self._error}
        with self._lock:
            schedule = copy.deepcopy(self._schedules.get("GLOBAL", {}))
        return {"settings": self.settings(), "markets": markets, "schedule": schedule,
                "reports": [self._summary(row, pdf_rows.get(row["id"])) for row in rows], "runtime": runtime}

    def get(self, report_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM briefing_reports WHERE id=?", (str(report_id),)).fetchone()
            pdf = db.execute("SELECT * FROM briefing_pdf_exports WHERE report_id=?", (str(report_id),)).fetchone()
        if row is None:
            raise ValueError("报告不存在")
        return {**self._summary(row, pdf), "report": json.loads(row["payload"]) if row["payload"] else None}

    def export_pdf(self, report_id):
        report_id = str(report_id)
        with self._lock:
            if self._stop.is_set():
                raise ValueError("服务正在退出")
            with self._db() as db:
                record = db.execute("SELECT * FROM briefing_reports WHERE id=?", (report_id,)).fetchone()
                if record is None:
                    raise KeyError("报告不存在")
                if record["status"] not in {"ready", "partial"} or not record["payload"]:
                    raise ValueError("报告尚未生成成功，暂不能导出 PDF")
                row = db.execute("SELECT * FROM briefing_pdf_exports WHERE report_id=?", (report_id,)).fetchone()
                metadata = self._pdf_metadata(record, row)
                if report_id in self._pdf_active or metadata["status"] == "ready":
                    return {"accepted": False, "report_id": report_id, "status": metadata["status"]}
                destination = self._pdf_path(record)
                db.execute("INSERT OR REPLACE INTO briefing_pdf_exports VALUES (?,'queued',?,NULL,NULL)", (report_id, destination.name))
            self._pdf_active.add(report_id)
            self._pdf_executor.submit(self._export_pdf, report_id)
        return {"accepted": True, "report_id": report_id, "status": "queued"}

    def _export_pdf(self, report_id):
        try:
            with self._db() as db:
                db.execute("UPDATE briefing_pdf_exports SET status='running' WHERE report_id=?", (report_id,))
            record = self.get(report_id)
            if self.pdf_writer is None:
                from .briefing_pdf import write_briefing_pdf
                writer = write_briefing_pdf
            else:
                writer = self.pdf_writer
            destination = self._pdf_path(record)
            writer(record, destination)
            if not destination.is_file():
                raise OSError("PDF 导出未产生文件")
            with self._db() as db:
                db.execute("UPDATE briefing_pdf_exports SET status='ready',error=NULL,exported_at=? WHERE report_id=?", (_iso(self.now()), report_id))
        except Exception as error:
            with self._db() as db:
                db.execute("UPDATE briefing_pdf_exports SET status='failed',error=? WHERE report_id=?", (str(error)[:1000], report_id))
        finally:
            with self._lock:
                self._pdf_active.discard(report_id)

    def pdf_file(self, report_id):
        record = self.get(report_id)
        path = self._pdf_path(record)
        if record["pdf"]["status"] != "ready" or not path.is_file():
            raise FileNotFoundError("PDF 尚未就绪或文件不存在，请先导出")
        return path

    def trigger(self, market, kind, date=None, *, refresh=False):
        market = str(market).upper()
        if market not in REPORT_MARKETS or kind not in {"morning", "close"} or not isinstance(refresh, bool):
            raise ValueError("请选择有效市场与早报/晚报")
        now = self.now()
        local_day = now.astimezone(ZoneInfo(REPORT_MARKETS[market]["timezone"])).date()
        day = local_day.isoformat() if date is None else str(date)
        if day != local_day.isoformat():
            raise ValueError("仅生成该市场今天的报告；历史报告请从记录查看，不能用现在的信息回填")
        with self._lock:
            if self._stop.is_set():
                raise ValueError("服务正在退出")
            with self._db() as db:
                row = db.execute("SELECT * FROM briefing_reports WHERE market=? AND kind=? AND session_date=? ORDER BY revision DESC LIMIT 1", (market, kind, day)).fetchone()
                if row and (row["status"] in {"queued", "running"} or row["status"] != "failed" and not refresh):
                    return {"accepted": False, "report_id": row["id"], "status": row["status"]}
                revision = row["revision"] + 1 if row and row["status"] != "failed" else row["revision"] if row else 1
                if row and row["status"] != "failed":
                    row = None
                report_id = row["id"] if row else uuid.uuid4().hex
                if row:
                    db.execute("UPDATE briefing_reports SET status='queued',created_at=?,completed_at=NULL,error=NULL,payload=NULL WHERE id=?", (_iso(now), report_id))
                else:
                    db.execute("INSERT INTO briefing_reports (id,market,kind,session_date,status,created_at,revision) VALUES (?,?,?,?,'queued',?,?)", (report_id, market, kind, day, _iso(now), revision))
            self._active.add(report_id)
            self._executor.submit(self._run, report_id, market, kind, day)
        return {"accepted": True, "report_id": report_id, "status": "queued"}

    def _exists(self, market, kind, day, *, scheduled_at=None):
        with self._db() as db:
            row = db.execute("SELECT status,created_at FROM briefing_reports WHERE market=? AND kind=? AND session_date=? ORDER BY revision DESC LIMIT 1", (market, kind, day)).fetchone()
        if row is None:
            return False
        attempted = _time(row["created_at"])
        # An early rejected manual request must not consume the scheduled run.
        # trigger() resets created_at, so a failure of that scheduled attempt is
        # not retried on every subsequent scheduler tick.
        if row["status"] == "failed" and scheduled_at is not None and attempted is not None and attempted < scheduled_at:
            return False
        return True

    def _tick(self):
        from .briefing_global import tick_global
        tick_global(self)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
                with self._lock:
                    self._error = None
            except Exception as error:
                with self._lock:
                    self._error = str(error)[:500]
            self._wake.wait(30)
            self._wake.clear()

    def _run(self, report_id, market, kind, day):
        if market == "GLOBAL":
            from .briefing_global import run_global
            return run_global(self, report_id, kind, day)
        try:
            with self._db() as db:
                db.execute("UPDATE briefing_reports SET status='running' WHERE id=?", (report_id,))
            session = self.calendar.session(market, date.fromisoformat(day))
            if session is None:
                raise ValueError("该市场今日休市，不生成交易日早晚报")
            if kind == "close" and self.now() < session.close:
                raise ValueError("该市场尚未收盘，不能把盘中数据作为收盘晚报")
            raw = self.config_store.read()
            targets = [row for row in relevant_targets(raw) if row["market"] == market]
            try:
                context = self.context_provider()
                if not isinstance(context, dict):
                    raise ValueError("数据上下文格式无效")
            except Exception as error:
                context = {"errors": [f"新闻与事件读取失败：{error}"]}
            cutoff = self.now()
            previous = self.calendar.previous(market, session.date)
            # The close review also needs overnight catalysts. Publication times
            # remain separate from the closing prices they may help explain.
            start = previous.close
            if kind == "morning":
                cutoff = min(cutoff, session.open)
            news, total_news = select_news(context.get("news") or [], market, start, cutoff, targets, include_overseas=True)
            quote_session = previous if kind == "morning" else session
            quote_previous = self.calendar.previous(market, quote_session.date)
            jobs = [(dict(market=market, symbol=symbol, name=name, role="index"), quote_session, quote_previous) for symbol, name in INDEXES[market]]
            # Overnight markets are prior completed sessions, never a live snapshot.
            if kind == "morning":
                for foreign in (["CN", "HK"] if market == "US" else ["US"]):
                    try:
                        overseas = self.calendar.last_completed(foreign, cutoff)
                        overseas_previous = self.calendar.previous(foreign, overseas.date)
                        definitions = INDEXES[foreign] if foreign == "US" else INDEXES[foreign][-1:]
                        jobs.extend((dict(market=foreign, symbol=symbol, name=name, role="overseas"), overseas, overseas_previous) for symbol, name in definitions)
                    except CalendarUnavailable as error:
                        context.setdefault("errors", []).append(f"外盘{MARKETS[foreign]['name']}日历不可用：{error}")
            jobs.extend((row, quote_session, quote_previous) for row in targets)
            quote_total = len(jobs)
            quotes = self._quotes(jobs[:MAX_QUOTES])
            focus = focus_signals(targets, news)
            events = []
            for row in context.get("events") or []:
                when = _time(row.get("detected_at"))
                if row.get("market") == market and when and start < when <= cutoff:
                    events.append({key: row.get(key) for key in ("market", "symbol", "name", "detected_at", "change_pct", "reason_summary", "evidence_source", "evidence_url", "evidence_published_at")})
            earnings = []
            if market == "US":
                wanted = {row["symbol"] for row in targets}
                for row in context.get("earnings") or []:
                    release = str(row.get("earnings_date") or "")
                    if row.get("symbol") in wanted and day <= release <= (session.date + timedelta(days=7)).isoformat():
                        earnings.append({key: row.get(key) for key in ("symbol", "name", "earnings_date", "session", "date_status", "source", "source_url", "sources", "date_conflict")})
            morning = None
            review_date = previous.date.isoformat() if kind == "morning" else day
            with self._db() as db:
                row = db.execute("SELECT payload FROM briefing_reports WHERE market=? AND kind='morning' AND session_date=? AND status IN ('ready','partial')", (market, review_date)).fetchone()
            morning = json.loads(row[0]) if row and row[0] else None
            completed = self.now()
            is_preopen = kind == "morning" and completed < session.open
            late = kind == "morning" and not is_preopen
            available = sum(row["status"] == "ready" for row in quotes)
            limitations = ["行情来自公开源，收盘数据按交易日匹配；可能存在延迟或后续修订",
                          "消息方向是已有证据的规则判断，不自动推导价格涨跌或买卖指令",
                          "宏观日历、全市场涨跌家数、连板和资金流未接入完整结构化数据，不用局部样本代替"]
            if market != "US":
                limitations.append("本市场财报日程尚无完整接入，暂无未来财报列表不表示没有财报事件")
            if late:
                limitations.append("盘前时段已过，本报告是补看记录，不计入盘前观点验证")
            if quote_total > MAX_QUOTES:
                limitations.append(f"本次获取前{MAX_QUOTES}项行情，共{quote_total}项；未处理部分不补零")
            if total_news > MAX_NEWS:
                limitations.append(f"展示优先相关的{MAX_NEWS}条新闻，窗口内符合时间条件共{total_news}条")
            if not news:
                limitations.append("窗口内没有可核验的新闻，可能与采集未完成或覆盖不足有关，不等于没有市场事件")
            errors = [str(error)[:500] for error in context.get("errors") or []]
            limitations.extend(errors)
            last_scan = _time(context.get("news_last_scan_at"))
            source_stale = last_scan is None or last_scan > completed or completed - last_scan > timedelta(minutes=30)
            sources = [
                {"name": "交易日历", "status": "ready", "as_of": _iso(completed), "note": session.source},
                {"name": "证券/指数收盘", "status": "ready" if available == quote_total else "partial", "as_of": _iso(quote_session.close), "note": f"有效{available}/{quote_total}项；每项保留来源与交易日"},
                {"name": "新闻与分析", "status": "partial" if source_stale or not news or errors else "ready", "as_of": _iso(last_scan) if last_scan else None, "note": "最近一轮采集时间未知或超过30分钟" if source_stale else "仅纳入窗口内且生成时已经收到的新闻"},
                {"name": "市场广度/资金流/宏观日历", "status": "unavailable", "as_of": None, "note": "未接入完整结构化来源"},
            ]
            direction_counts = {key: sum(row["signal"] == key for row in news) for key in ("bullish", "bearish", "neutral", "unknown")}
            summary = f"窗口内{len(news)}条新闻样本：利多{direction_counts['bullish']}、利空{direction_counts['bearish']}、中性{direction_counts['neutral']}、待核实{direction_counts['unknown']}。收盘行情完整{available}/{quote_total}项。外盘消息按原市场标示，不直接算作本市场方向。"
            report = {"title": f"{MARKETS[market]['name']} · {'盘前早报' if kind == 'morning' else '收盘晚报'}", "market": market,
                      "kind": kind, "session_date": day, "generated_at": _iso(completed),
                      "session_open": _iso(session.open), "session_close": _iso(session.close),
                      "window": {"start": _iso(start), "end": _iso(cutoff), "timezone": MARKETS[market]["timezone"]},
                      "is_preopen": is_preopen, "late": late, "summary": summary, "quotes": quotes, "news": news,
                      "events": events[:100], "earnings": earnings, "focus": focus,
                      "review": review_morning(morning, quotes, review_date),
                      "sources": sources, "limitations": limitations,
                      "coverage": {"target_total": len(targets), "quote_total": quote_total, "quote_available": available, "news_total": total_news}}
            from .briefing_narrative import build_narrative
            from .briefing_skills import DEFAULTS
            report['analysis_skill'] = raw.get('briefing_skills', {}).get(kind) or DEFAULTS[kind]
            analysis_config = analysis_config_from_raw(raw, self.config_store.path)
            try:
                narrative = (self.narrative_builder or build_narrative)(copy.deepcopy(report), analysis_config)
            except Exception:
                # A reasoning failure must not discard verified prices and news.
                narrative = build_narrative(copy.deepcopy(report), AnalysisConfig(mode="rules", model=analysis_config.model))
                narrative["limitations"].append("本期完整叙事生成未成功，已保留有限证据归纳与原始资料。")
            report["narrative"] = narrative
            report["factual_summary"] = summary
            if narrative.get("overview"):
                report["summary"] = narrative["overview"]
            completed = self.now()
            report["generated_at"] = _iso(completed)
            report["is_preopen"] = kind == "morning" and completed < session.open
            report["late"] = late = kind == "morning" and not report["is_preopen"]
            if late and not any("盘前时段已过" in note for note in limitations):
                limitations.append("盘前时段已过，本报告是补看记录，不计入盘前观点验证")
            sources.append({"name": "叙事研判", "status": "ready" if narrative.get("status") == "ready" else "partial",
                            "as_of": _iso(completed), "note": "基于本期冻结证据的模型推演，结论为待验证解释" if narrative.get("mode") == "model" else "有限证据归纳；完整模型推演尚未启用或本期不可用"})
            status = "partial" if available != quote_total or not news or errors or source_stale or late else "ready"
            with self._db() as db:
                db.execute("UPDATE briefing_reports SET status=?,completed_at=?,error=NULL,payload=? WHERE id=?", (status, _iso(completed), json.dumps(report, ensure_ascii=False, allow_nan=False), report_id))
        except Exception as error:
            with self._db() as db:
                db.execute("UPDATE briefing_reports SET status='failed',completed_at=?,error=? WHERE id=?", (_iso(self.now()), str(error)[:1000], report_id))
        else:
            # Export has its own persisted state. A local PDF failure must never
            # replace a completed report with a failed collection record.
            if not self._stop.is_set() and self.settings()["auto_export_pdf"]:
                try:
                    self.export_pdf(report_id)
                except Exception as error:
                    with self._db() as db:
                        db.execute("INSERT OR REPLACE INTO briefing_pdf_exports VALUES (?,'failed',NULL,?,NULL)", (report_id, str(error)[:1000]))
        finally:
            with self._lock:
                self._active.discard(report_id)

    def _quotes(self, jobs):
        results = [None] * len(jobs)
        saved_closes = {}
        if any(target.get("role") == "index" for target, _, _ in jobs):
            with self._db() as db:
                snapshots = db.execute("SELECT payload FROM briefing_reports WHERE status IN ('ready','partial') ORDER BY completed_at DESC LIMIT 60").fetchall()
            for snapshot in snapshots:
                for row in json.loads(snapshot["payload"] or "{}").get("quotes", []):
                    if row.get("role") == "index" and row.get("status") in {"ready", "partial"} and (_number(row.get("close")) or 0) > 0:
                        saved_closes.setdefault((row["market"], row["symbol"], row["session_date"]), row)
        pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="briefing-quote")
        def run(target, session, previous):
            charts = []
            result = closing_quote(target, session, previous, {})
            periods = ("3m", "1m") if target.get("role") == "index" else ("3m",)
            for period in periods:
                try:
                    chart = self.chart_fetcher(target["market"], target["symbol"], period, mode="daily", timeout_seconds=8)
                except Exception:
                    if target.get("role") != "index":
                        raise
                    continue
                charts.append(chart)
                candidate = closing_quote(target, session, previous, chart)
                if candidate["status"] == "ready":
                    return candidate
                if candidate["close"] is not None or result["close"] is None:
                    result = candidate
            if target.get("role") != "index":
                return result
            def archived(day):
                row = saved_closes.get((target["market"], target["symbol"], day.date.isoformat()))
                return row if row and (_time(row.get("as_of")) or datetime.min.replace(tzinfo=timezone.utc)) >= day.close else None
            stored = archived(session)
            if result["close"] is None and stored:
                result = {**stored, **target, "source": stored["source"] + " · 历史收盘"}
            prior = archived(previous)
            if result["close"] is not None:
                if result.get("change_pct") is None and prior:
                    result.update(previous_close=prior["close"], change_pct=(result["close"] / prior["close"] - 1) * 100,
                                  status="ready", reason="前收引用已核实历史收盘", source=result["source"] + " · 前收：历史收盘")
                return result
            # A missing session may use the preceding confirmed close, never today's intraday quote.
            earlier = self.calendar.previous(target["market"], previous.date)
            candidates = [closing_quote(target, previous, earlier, chart) for chart in charts]
            if prior:
                candidates.append({**prior, **target})
            fallback = next((row for row in candidates if row["status"] == "ready"),
                            next((row for row in candidates if row.get("close") is not None), None))
            if fallback:
                fallback.update(status="partial", requested_session_date=session.date.isoformat(),
                                source=fallback["source"] + " · 历史收盘参考",
                                reason=f"{session.date.isoformat()}收盘缺失，引用{previous.date.isoformat()}已确认收盘；涨跌幅仅在对应前收有效时计算")
                return fallback
            return result
        futures = {pool.submit(run, *job): index for index, job in enumerate(jobs)}
        try:
            for future in as_completed(futures, timeout=60):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as error:
                    results[index] = {**jobs[index][0], "session_date": jobs[index][1].date.isoformat(), "status": "unavailable",
                                      "close": None, "previous_close": None, "change_pct": None, "reason": str(error)[:300], "source": "公开日K", "source_url": "", "as_of": None}
        except TimeoutError:
            pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for index, row in enumerate(results):
            if row is None:
                results[index] = {**jobs[index][0], "session_date": jobs[index][1].date.isoformat(), "status": "unavailable",
                                  "close": None, "previous_close": None, "change_pct": None, "reason": "本轮行情获取超时", "source": "公开日K", "source_url": "", "as_of": None}
        return results
