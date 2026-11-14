"""One evidence snapshot and one narrative across CN, US, HK and KR."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from itertools import zip_longest
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .briefing import INDEXES, MAX_NEWS, MAX_QUOTES, _iso, _time, focus_signals, relevant_targets, select_news
from .briefing_editorial import build_sections, index_commentary
from .briefing_front import lead_news, portfolio_moves, selected_books
from .briefing_market_detail import build_market_detail
from .briefing_narrative import _evidence, build_narrative
from .briefing_narrative_review import build_narrative_review, freeze_narrative_tracking
from .config import AnalysisConfig, analysis_config_from_raw
from .market_sessions import MARKETS, CalendarUnavailable

ORDER = ("CN", "US", "HK", "KR")
ANCHORS = {**INDEXES, "US": [*INDEXES["US"], ("^DJI", "道琼斯工业指数"), ("^SOX", "费城半导体指数")]}
ZONE = ZoneInfo("Asia/Shanghai")
QUERIES = {
    "CN": ("(A股 OR 沪深300 OR 上证指数 OR 上市公司公告 OR 再融资 OR 产业链) when:4d", "zh-CN", "CN", "CN:zh-Hans"),
    "US": ('("stock market" OR "Wall Street" OR "Federal Reserve" OR chipmaker OR semiconductor OR tariffs OR "board of directors" OR "SEC filing" OR "initial public offering") when:4d', "en-US", "US", "US:en"),
    "HK": ("(港股 OR 恒生指數 OR 恒生科技 OR 港交所公告 OR 香港新股 OR H股配售) when:4d", "zh-HK", "HK", "HK:zh-Hant"),
    "KR": ('(KOSPI OR "Korea stocks" OR "Bank of Korea" OR "Korea IPO" OR "Korea semiconductor") when:4d', "en-US", "US", "US:en"),
}


def fetch_market_news(market, now):
    from .sources import _fetch_rss
    query, language, country, edition = QUERIES[market]
    url = "https://news.google.com/rss/search?" + urlencode({"q": query, "hl": language, "gl": country, "ceid": edition})
    rows = _fetch_rss(url, "Google News RSS", 8, cache_seconds=60)
    output = []
    for row in rows[:60]:
        published = _time(row.get("published_at"))
        if published is None:
            try:
                published = parsedate_to_datetime(row.get("published_at", ""))
                if not published.tzinfo:
                    continue
            except (TypeError, ValueError):
                continue
        output.append({"market": market, "symbol": "", "title": row.get("title"),
                       "news_summary": row.get("summary"), "source": row.get("source"), "url": row.get("url"),
                       "published_at": _iso(published), "discovered_at": _iso(now), "event_key": row.get("url"),
                       "event_type": "other", "evidence_status": "unverified", "market_background": True})
    return output


def tick_global(monitor):
    now, settings = monitor.now(), monitor.settings()
    local = now.astimezone(ZONE)
    schedule = {"timezone": "Asia/Shanghai", "calendar_note": "每日综合四地市场；休市与报价交易日逐项标明"}
    for kind in ("morning", "close"):
        hour, minute = map(int, settings[f"{kind}_time"].split(":"))
        planned = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        exists = monitor._exists("GLOBAL", kind, local.date().isoformat(), scheduled_at=planned)
        # Do not regenerate earlier reports just because the service restarts.
        # A short recovery window permits ordinary startup jitter, not backdating.
        if settings["enabled"] and not exists and planned <= now < planned + timedelta(minutes=30):
            monitor.trigger("GLOBAL", kind)
            exists = True
        schedule[f"next_{kind}_at"] = _iso(planned + timedelta(days=1) if exists or now >= planned + timedelta(minutes=30) else planned)
    with monitor._lock:
        monitor._schedules["GLOBAL"] = schedule
        monitor._last_checked = _iso(now)


def build_global(monitor, kind, day):
    raw = monitor.config_store.read()
    targets = relevant_targets(raw)
    books = selected_books(raw)
    selected = {(position["market"], position["symbol"]) for book in books for position in book.get("positions", [])}
    try:
        context = monitor.context_provider()
        if not isinstance(context, dict):
            raise ValueError("无效采集结果")
    except Exception as error:
        context = {"errors": [f"既有新闻读取失败：{type(error).__name__}"]}
    context = copy.deepcopy(context)
    errors = list(context.get("errors") or [])
    collected, collection = [], []
    fetcher = monitor.market_news_fetcher or fetch_market_news
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="briefing-market-news")
    jobs = {pool.submit(fetcher, market, monitor.now()): market for market in ORDER}
    try:
        for future in as_completed(jobs, timeout=25):
            market = jobs[future]
            try:
                rows = future.result()
                collected.extend(rows)
                collection.append({"name": f"{MARKETS[market]['name']}市场新闻", "market": market,
                                   "status": "ready" if rows else "partial", "as_of": _iso(monitor.now()),
                                   "note": f"新采集{len(rows)}条标题/摘要；保留发布时间，未冒充全文核验"})
            except Exception as error:
                collection.append({"name": f"{MARKETS[market]['name']}市场新闻", "market": market,
                                   "status": "failed", "as_of": None, "note": type(error).__name__})
    except TimeoutError:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for market in ORDER:
        if not any(row["market"] == market for row in collection):
            collection.append({"name": f"{MARKETS[market]['name']}市场新闻", "market": market,
                               "status": "failed", "as_of": None, "note": "采集超时，本市场覆盖不足"})
    cutoff = monitor.now()
    start = cutoff - timedelta(days=3)
    quote_jobs, target_jobs, sections, sessions = [], [], [], {}
    for market in ORDER:
        info = MARKETS[market]
        section = {"market": market, "name": info["name"], "phase": "时段待确认", "timezone": info["timezone"]}
        try:
            local_day = cutoff.astimezone(ZoneInfo(info["timezone"])).date()
            session = monitor.calendar.session(market, local_day)
            last = monitor.calendar.last_completed(market, cutoff)
            prior = monitor.calendar.previous(market, last.date)
            sessions[market] = last, prior
            upcoming = session if session and cutoff < session.open else monitor.calendar.next(market, local_day)
            section.update(phase="休市" if session is None else "盘前" if cutoff < session.open else "盘中" if cutoff < session.close else "盘后",
                           quote_session_date=last.date.isoformat(), next_open=_iso(upcoming.open),
                           session_open=_iso(session.open) if session else None, session_close=_iso(session.close) if session else None,
                           note="行情采用最近已完成交易日收盘；不同市场收盘时刻不同，不视为同步表现")
            start = min(start, last.close)
            quote_jobs.extend((dict(market=market, symbol=s, name=n, role="index"), last, prior) for s, n in ANCHORS[market])
            target_jobs.extend((row, last, prior) for row in targets if row["market"] == market)
        except CalendarUnavailable as error:
            section["note"] = str(error)
            errors.append(f"{info['name']}交易日历不可用；未猜测交易日")
        sections.append(section)
    inputs = [*(context.get("news") or []), *collected]
    per_market, total = [], 0
    for market in ORDER:
        rows, count = select_news(inputs, market, start, cutoff, targets)
        total += count
        background = [row for row in rows if not row.get("symbol")]
        companies = [row for row in rows if row.get("symbol")]
        per_market.append([row for pair in zip_longest(background, companies) for row in pair if row is not None])
    # A company-heavy watchlist must not crowd another market out of the report.
    balanced = [row for group in zip_longest(*per_market) for row in group if row is not None]
    headlines = lead_news(balanced, cutoff)
    # Keep the lead headlines in the evidence packet, even with a large watchlist.
    news = [*headlines, *(row for row in balanced if row not in headlines)][:MAX_NEWS]
    target_jobs.sort(key=lambda job: (job[0]["market"], job[0]["symbol"]) not in selected)
    quotes = monitor._quotes((quote_jobs + target_jobs)[:MAX_QUOTES])
    for section in sections:
        market = section["market"]
        section.update(news_count=sum(row["market"] == market for row in news),
                       quote_available=sum(row["market"] == market and row["status"] == "ready" for row in quotes))
    events = [row for row in context.get("events") or [] if row.get("market") in ORDER
              and _time(row.get("detected_at")) and start < _time(row["detected_at"]) <= cutoff][:100]
    available = sum(row["status"] == "ready" for row in quotes)
    planned = datetime.fromisoformat(day + "T" + monitor.settings()[f"{kind}_time"]).replace(tzinfo=ZONE)
    late = cutoff >= planned + timedelta(minutes=30)
    limitations = ["跨市场收盘按各自交易日匹配；报道晚于某市场收盘时，只能作为后续催化，不能解释此前涨跌。",
                   "未接入完整市场广度、真实资金流和事前一致预期，不能据局部样本断言资金迁移或超预期。", *errors]
    if late:
        limitations.append("更新版按实际生成时间冻结判断，验证从发布后的交易时段开始。")
    report = {"title": "全球市场综合早报" if kind == "morning" else "全球市场综合晚报", "market": "GLOBAL", "markets": sections,
              "editorial_version": 2,
              "kind": kind, "session_date": day, "generated_at": _iso(monitor.now()), "scheduled_at": _iso(planned),
              "window": {"start": _iso(start), "end": _iso(cutoff), "timezone": "Asia/Shanghai"},
              "is_preopen": False, "late": late, "summary": "四地市场综合研判", "quotes": quotes, "news": news,
              "focus": focus_signals(targets, news), "events": events, "earnings": context.get("earnings") or [],
              "review": {"status": "unavailable", "items": [], "note": "跨时区按各市场交易日复盘，不将不同收盘日合成单一方向命中率。"},
              "sources": collection + [{"name": "证券/指数收盘", "status": "ready" if available == len(quotes) else "partial",
                                       "as_of": _iso(cutoff), "note": f"有效{available}/{len(quote_jobs)+len(target_jobs)}项，逐项保留真实交易日和来源"}],
              "limitations": limitations,
              "coverage": {"target_total": len(targets), "quote_total": len(quote_jobs)+len(target_jobs), "quote_available": available,
                           "news_total": total, "market_total": 4}}
    report["front_page"] = _front_page(report, headlines, portfolio_moves(books, context.get("portfolio_snapshot") or {}, quotes, cutoff))
    report["front_page"]["market_analysis"] = index_commentary(report["front_page"])
    if kind == "close":
        report["market_detail"] = build_market_detail(monitor, report, sessions)
    elif monitor.week_calendar_fetcher:
        try:
            report["calendar_events"] = monitor.week_calendar_fetcher(cutoff)
            report["sources"].append({"name": "美联储本周日历", "status": "ready", "as_of": _iso(monitor.now()),
                                      "note": "官方政策日程与演讲，按北京时间列示"})
        except (OSError, ValueError):
            report["sources"].append({"name": "美联储本周日历", "status": "unavailable", "as_of": None,
                                      "note": "本轮日历读取失败"})
    from .briefing_skills import DEFAULTS
    report['analysis_skill'] = raw.get('briefing_skills', {}).get(kind) or DEFAULTS[kind]
    config = analysis_config_from_raw(raw, monitor.config_store.path)
    try:
        narrative = (monitor.narrative_builder or build_narrative)(copy.deepcopy(report), config)
    except Exception:
        narrative = build_narrative(copy.deepcopy(report), AnalysisConfig(mode="rules", model=config.model))
        narrative["limitations"].append("综合推演未成功，保留本期事实；可更新本期重试。")
    report.update(narrative=narrative, factual_summary=report["summary"], summary=narrative.get("overview") or report["summary"],
                  generated_at=_iso(monitor.now()))
    report["sections"] = build_sections(report, sessions)
    since = (date.fromisoformat(day) - timedelta(days=21)).isoformat()
    with monitor._db() as db:
        prior = [json.loads(row["payload"]) for row in db.execute(
            "SELECT payload FROM briefing_reports WHERE market='GLOBAL' AND session_date BETWEEN ? AND ? "
            "AND status IN ('ready','partial') AND payload IS NOT NULL ORDER BY session_date, created_at, id", (since, day))]
    report["narrative_review"] = build_narrative_review(report, prior, calendar=monitor.calendar, chart_fetcher=monitor.chart_fetcher)
    report["generated_at"] = _iso(monitor.now())
    report["narrative_review"]["tracking"] = freeze_narrative_tracking(report, monitor.calendar)
    report["sources"].append({"name": "综合叙事研判", "status": "ready" if narrative.get("mode") == "model" else "partial",
                              "as_of": report["generated_at"], "note": "模型基于四地同一份证据推演" if narrative.get("mode") == "model" else "模型未完成，有限证据归纳"})
    status = "partial" if errors or any(s["status"] != "ready" for s in collection) or available != report["coverage"]["quote_total"] or any(not s["news_count"] for s in sections) or narrative.get("status") != "ready" else "ready"
    return report, status


def _front_page(report, headlines, portfolio_rows):
    _, evidence_quotes, evidence_news, _ = _evidence(report)
    news_refs = {(row["market"], row["symbol"], row["event_key"]): row["ref"] for row in evidence_news}
    quote_refs = {(row["market"], row["symbol"]): row["ref"] for row in evidence_quotes}
    by_quote = {(row["market"], row["symbol"]): row for row in report["quotes"] if row["role"] == "index"}
    events = []
    for row in headlines:
        source = row["source"]
        title = row["title"].removesuffix(" - " + source)
        summary = row["excerpt"].removesuffix(source).strip()
        events.append({"title": title, "summary": summary[:200] if summary != title else "", "source": source,
                       "published_at": row["published_at"], "url": row["url"],
                       "ref": news_refs.get((row["market"], row["symbol"], row["event_key"]))})
    markets = []
    for market in ("US", "CN", "HK", "KR"):
        for symbol, label in ANCHORS[market]:
            row = by_quote.get((market, symbol), {})
            markets.append({"market": market, "symbol": symbol, "label": label, "change_pct": row.get("change_pct"),
                            "price": row.get("close"), "session_date": row.get("session_date"), "source": row.get("source"),
                            "status": row.get("status", "unavailable"), "reason": row.get("reason", "交易日历不可用"),
                            "ref": quote_refs.get((market, symbol))})
    return {"events": events, "markets": markets, "portfolios": portfolio_rows}


def run_global(monitor, report_id, kind, day):
    try:
        with monitor._db() as db:
            db.execute("UPDATE briefing_reports SET status='running' WHERE id=?", (report_id,))
        report, status = build_global(monitor, kind, day)
        with monitor._db() as db:
            db.execute("UPDATE briefing_reports SET status=?,completed_at=?,error=NULL,payload=? WHERE id=?",
                       (status, report["generated_at"], json.dumps(report, ensure_ascii=False, allow_nan=False), report_id))
    except Exception as error:
        with monitor._db() as db:
            db.execute("UPDATE briefing_reports SET status='failed',completed_at=?,error=? WHERE id=?",
                       (_iso(monitor.now()), f"{type(error).__name__}: {str(error)[:400]}", report_id))
    else:
        if not monitor._stop.is_set() and monitor.settings()["auto_export_pdf"]:
            try:
                monitor.export_pdf(report_id)
            except Exception as error:
                with monitor._db() as db:
                    db.execute("INSERT OR REPLACE INTO briefing_pdf_exports VALUES (?,'failed',NULL,?,NULL)", (report_id, str(error)[:500]))
    finally:
        with monitor._lock:
            monitor._active.discard(report_id)
