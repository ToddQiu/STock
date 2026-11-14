"""Freeze report hypotheses and review them against exact later market sessions."""
from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from .briefing import INDEXES, _iso, _link, _number, _time, closing_quote
from .briefing_narrative import _TRANSMISSION
from .market_sessions import MARKETS, CalendarUnavailable


BEIJING = ZoneInfo("Asia/Shanghai")
TRACKED_SIGNALS = {"bullish", "bearish", "neutral", "unrecorded"}
OBSERVED = {"aligned", "opposed", "flat", "observed"}
STAGES = ("intraday", "next_day", "week")
FOLLOWUP_TYPES = (set(_TRANSMISSION) - {"other"}) | {"ipo", "filing", "disclosure", "financing"}
NOTE = ("每个检查点均相对原报告冻结的前收盘价。盘中取首个后续常规交易时段开盘约一小时的实际分钟线，"
        "次日报告核对前一日报告的判断与首个后续交易时段收盘，一周核对第五个后续交易日收盘。"
        "有同市场指数的同期收盘价时，以标的涨跌幅减去指数涨跌幅，单位为百分点。"
        "价格方向只检验走势，事件传导和定价程度仍须核对预期与基本面。")


def _day(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _target(value):
    return value if isinstance(value, str) and re.fullmatch(r"(CN|US|HK|KR):[A-Z0-9.^=_-]{1,24}", value) else ""


def _quote_key(row):
    return _target(f"{row.get('market')}:{row.get('symbol')}")


def _cutoff(report):
    return _time((report.get("window") or {}).get("end")) or _time(report.get("generated_at"))


def _future_sessions(target, published_at, calendar):
    market = target.split(":", 1)[0]
    local_day = published_at.astimezone(ZoneInfo(MARKETS[market]["timezone"])).date()
    first = calendar.session(market, local_day)
    if first is None or first.open <= published_at:
        first = calendar.next(market, local_day)
    week = first
    for _ in range(4):
        week = calendar.next(market, week.date)
    return first, week


def _legacy_hypotheses(report, evidence):
    """Recover only explicitly linked events from legacy reports without a frozen signal."""
    narrative = report.get("narrative") or {}
    for row in narrative.get("reasoning") or []:
        refs = row.get("supporting_refs") or []
        sources = [evidence[ref] for ref in refs if ref in evidence and evidence[ref].get("kind") == "news"]
        targets = {_quote_key(fact) for fact in sources if _quote_key(fact)}
        if len(targets) != 1:
            continue
        target = next(iter(targets))
        fact = next(fact for fact in sources if _quote_key(fact) == target)
        news = next((news for news in report.get("news") or [] if _quote_key(news) == target
                     and fact.get("url") and news.get("url") == fact["url"]), {})
        yield {"target": target, "event": str(fact.get("label") or "").removeprefix("来源报道："),
               "signal": "unrecorded", "refs": refs,
               "sector": news.get("sector"), "impact": row.get("mechanism"), "validation": row.get("validation")}


def _usable_quote(row, cutoff):
    stamp, price = _time(row.get("as_of")), _number(row.get("close"))
    return row.get("status") in {"ready", "partial"} and stamp and stamp <= cutoff and price is not None and price > 0


def _frozen_price(quote):
    return {"price": float(quote["close"]), "session_date": quote["session_date"],
            "as_of": quote["as_of"], "source": quote.get("source"), "source_url": quote.get("source_url"),
            "currency": quote.get("currency")}


def freeze_narrative_tracking(report, calendar):
    """Bind hypotheses to the precise first known baseline at publication."""
    published_at, cutoff = _time(report.get("generated_at")), _cutoff(report)
    origin_date = _day(report.get("session_date"))
    if published_at is None or cutoff is None or origin_date is None:
        return []
    cutoff = min(cutoff, published_at)
    narrative = report.get("narrative") or {}
    evidence = {row["id"]: row for row in narrative.get("evidence") or [] if row.get("id")}
    hypotheses = narrative.get("hypotheses")
    if hypotheses is None:
        hypotheses = list(_legacy_hypotheses(report, evidence))
    quotes = {_quote_key(row): row for row in report.get("quotes") or [] if _usable_quote(row, cutoff)}
    news_keys = {row.get("url"): row["event_key"] for row in report.get("news") or [] if row.get("url") and row.get("event_key")}
    tracking = []
    for row in hypotheses[:3]:
        signal, event = row.get("signal"), str(row.get("event") or "").strip()
        if signal not in TRACKED_SIGNALS or not event:
            continue
        target = _target(row.get("target"))
        quote = quotes.get(target, {})
        baseline = benchmark = None
        if quote and target:
            try:
                first, _ = _future_sessions(target, published_at, calendar)
                previous = calendar.previous(first.market, first.date)
                if (quote.get("session_date") == previous.date.isoformat()
                        and _time(quote.get("as_of")) == previous.close and previous.close <= cutoff):
                    baseline = _frozen_price(quote)
                    for symbol, name in INDEXES[first.market]:
                        identifier = f"{first.market}:{symbol}"
                        index = quotes.get(identifier, {})
                        if (identifier != target and index.get("role") == "index"
                                and index.get("session_date") == baseline["session_date"]
                                and _time(index.get("as_of")) == previous.close):
                            benchmark = {"target": identifier, "name": index.get("name") or name, **_frozen_price(index)}
                            break
            except CalendarUnavailable:
                pass
        event_urls = [evidence[ref]["url"] for ref in row.get("refs") or [] if ref in evidence
                      and evidence[ref].get("kind") == "news" and evidence[ref].get("url")]
        kind = report.get("kind")
        identity = hashlib.sha256(f"{origin_date}|{kind}|{published_at}|{target}|{event}".encode()).hexdigest()[:20]
        tracking.append({"id": identity, "origin_date": origin_date.isoformat(), "origin_kind": kind,
                         "origin_generated_at": _iso(published_at), "target": target,
                         "name": quote.get("name") or row.get("name") or target,
                         "sector": row.get("sector") or "", "event": event, "signal": signal,
                         "event_url": event_urls[0] if event_urls else None,
                         "event_keys": list(dict.fromkeys(news_keys.get(url, url) for url in event_urls)),
                         "impact": row.get("impact") or "", "validation": row.get("validation") or "",
                         "baseline": baseline, "benchmark": benchmark})
    return tracking


def _checkpoint(status, session, reason, *, observed=None, change=None):
    row = {"status": status, "session_date": session.date.isoformat() if session else None,
           "reason": reason, "change_pct": change}
    if observed:
        row.update({key: observed.get(key) for key in ("price", "as_of", "source", "source_url")})
    return row


def _intraday(chart, session, due):
    interval = re.fullmatch(r"(1|5|10|15|30|60)m", str(chart.get("interval")))
    if not interval:
        return None
    duration = timedelta(minutes=int(interval[1]))
    values = []
    for candle in chart.get("candles") or []:
        start, price = _time(candle.get("datetime")), _number(candle.get("close"))
        if (start is None or start < session.open or candle.get("session") not in (None, "regular")
                or candle.get("partial") or candle.get("complete") is False or price is None or price <= 0):
            continue
        end = start + duration
        if due - timedelta(minutes=15) <= end <= due:
            values.append((end, price))
    if values:
        stamp, price = max(values)
        return {"price": price, "as_of": _iso(stamp), "source": chart.get("source"),
                "source_url": chart.get("source_url"), "currency": chart.get("currency")}
    return None


def _daily(chart, session, cutoff, calendar):
    last = calendar.last_completed(session.market, cutoff)
    filtered = {**chart, "candles": [row for row in chart.get("candles") or []
                                    if (day := _day(row.get("date"))) is not None and day <= last.date]}
    clock = _time(chart.get("source_quote_at"))
    if clock and clock > cutoff:
        filtered["source_quote_at"] = None
    previous = calendar.previous(session.market, session.date)
    row = closing_quote({"market": session.market}, session, previous, filtered)
    return {**row, "price": row["close"]} if _usable_quote(row, cutoff) else None


def _assess(observed, item, session, stage):
    baseline = item["baseline"]
    if not observed:
        missing = "开盘一小时附近缺少有时点的完整分钟线" if stage == "intraday" else "缺少该交易日有效收盘价"
        return _checkpoint("unavailable", session, missing)
    if baseline.get("currency") and observed.get("currency") and baseline["currency"] != observed["currency"]:
        return _checkpoint("unavailable", session, "两次行情币种不一致")
    change = _number((observed["price"] / baseline["price"] - 1) * 100)
    if change is None:
        return _checkpoint("unavailable", session, "两次价格无法计算有效变化")
    change = round(change, 4)
    if item["signal"] == "unrecorded":
        status, reason = "observed", "原报告未冻结明确方向，记录实际变动"
    elif item["signal"] == "neutral":
        status, reason = "observed", "原判断为中性，记录实际变动"
    elif round(change, 2) == 0:
        status, reason = "flat", "与基准持平，方向尚无价格支持"
    elif (change > 0) == (item["signal"] == "bullish"):
        status, reason = "aligned", "价格方向符合原判断"
    else:
        status, reason = "opposed", "价格方向与原判断相反"
    return _checkpoint(status, session, reason, observed=observed, change=change)


def _matches_chart(target, chart):
    market, symbol = target.split(":", 1)
    if chart.get("symbol") == symbol:
        return True
    if chart.get("source") != "东方财富 · 公开参考" or market not in {"CN", "HK", "KR"}:
        return False
    code = symbol.split(".", 1)[0]
    # Eastmoney's verified backup adapter returns the numeric code without exchange suffix.
    if not code.isdecimal():
        return False
    market_id = {"CN": "1" if symbol.endswith(".SS") else "0", "HK": "116", "KR": "177"}[market]
    code = code.zfill({"CN": 6, "HK": 5, "KR": 6}[market])
    return chart.get("symbol") == code and chart.get("source_url") == f"https://quote.eastmoney.com/unify/r/{market_id}.{code}"


def _fetch_charts(jobs, chart_fetcher):
    if not jobs or chart_fetcher is None:
        return {}
    charts = {}
    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="briefing-review")
    futures = {pool.submit(chart_fetcher, *target.split(":", 1), "5d" if mode == "intraday" else "1m",
                           mode=mode, timeout_seconds=8): (target, mode) for target, mode in jobs}
    try:
        for future in as_completed(futures, timeout=20):
            target, mode = futures[future]
            try:
                chart = future.result()
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                continue
            if (isinstance(chart, dict) and chart.get("mode") in (None, mode)
                    and _matches_chart(target, chart)
                    and chart.get("source") and not chart.get("stale") and not chart.get("_startup_cache")):
                charts[target, mode] = chart
    except TimeoutError:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return charts


def _followup(item, news, cutoff):
    published_at = _time(item.get("origin_generated_at"))
    if not item.get("target") or published_at is None:
        return []
    seen = {*item.get("event_keys", []), item.get("event_url"), re.sub(r"\W+", "", item["event"]).casefold()}
    result = []
    for row in sorted(news, key=lambda value: str(value.get("published_at") or ""), reverse=True):
        if _quote_key(row) != item["target"] or row.get("event_type") not in FOLLOWUP_TYPES:
            continue
        stamp, discovered = _time(row.get("published_at")), _time(row.get("discovered_at"))
        title = str(row.get("title") or "").strip()
        url, source = _link(row.get("url")), str(row.get("source") or "").strip()
        if not stamp or not discovered or not published_at < stamp <= cutoff or discovered > cutoff or not (title and url and source):
            continue
        keys = {row.get("event_key") or url, url, re.sub(r"\W+", "", title).casefold()}
        if seen & keys:
            continue
        seen.update(keys)
        result.append({"title": title[:240], "as_of": _iso(stamp), "source": source, "url": url})
        if len(result) == 2:
            break
    return result


def _compare_benchmark(item, observations):
    benchmark = item.get("benchmark")
    if not benchmark:
        return ""
    comparisons = []
    for stage in ("next_day", "week"):
        checkpoint = item[stage]
        change = checkpoint.get("change_pct")
        if change is None:
            continue
        relative = checkpoint.get("relative_change_pct")
        if relative is None:
            current = observations.get((benchmark["target"], checkpoint["session_date"]), {})
            if (current.get("role") != "index" or _time(current.get("as_of")) != _time(checkpoint.get("as_of"))
                    or benchmark.get("currency") and current.get("currency") and benchmark["currency"] != current["currency"]):
                continue
            index_change = _number((current["price"] / benchmark["price"] - 1) * 100)
            relative = _number(change - index_change) if index_change is not None else None
            if relative is None:
                continue
            item[stage] = {**checkpoint, "relative_change_pct": round(relative, 4),
                           "benchmark_change_pct": round(index_change, 4), "benchmark_name": benchmark["name"],
                           "benchmark_target": benchmark["target"], "benchmark_source": current.get("source"),
                           "benchmark_source_url": current.get("source_url")}
        comparisons.append((round(change, 2), round(relative, 2)))
    if any(change * relative < 0 for change, relative in comparisons):
        return f"与{benchmark['name']}对比，至少一个检查点的绝对和相对方向相反。"
    if comparisons and all(change * relative > 0 for change, relative in comparisons):
        return f"与{benchmark['name']}对比，可核验检查点的绝对和相对方向一致。"
    return ""


def build_narrative_review(report, previous_reports, *, calendar, chart_fetcher=None):
    """Reuse published checkpoints, and collect only the missing checkpoints due now."""
    result = {"tracking": freeze_narrative_tracking(report, calendar), "items": [], "note": NOTE}
    cutoff, report_day = _cutoff(report), _day(report.get("session_date"))
    if cutoff is None or report_day is None:
        return result
    prior = [(stamp, previous) for previous in previous_reports
             if (stamp := _time(previous.get("generated_at"))) is not None and stamp < cutoff
             and previous.get("market") == report.get("market")]
    prior.sort(key=lambda entry: entry[0])
    previous_cutoff = prior[-1][0] if prior else None
    closest = max((day for _, row in prior if (day := _day(row.get("session_date"))) and day < report_day), default=None)
    frozen, reviewed = {}, {}
    for _, previous in prior:
        review = previous.get("narrative_review") or {}
        tracking = review["tracking"] if "tracking" in review else freeze_narrative_tracking(previous, calendar)
        if tracking:
            frozen.setdefault((previous["session_date"], previous["kind"]), tracking)
        for item in review.get("items") or []:
            for stage in STAGES:
                checkpoint = item.get(stage) or {}
                if checkpoint.get("status") in OBSERVED and (stamp := _time(checkpoint.get("as_of"))) and stamp <= cutoff:
                    reviewed.setdefault((item.get("id"), stage), checkpoint)

    observations = {}
    for row in report.get("quotes") or []:
        if _usable_quote(row, cutoff):
            observations[_quote_key(row), row.get("session_date")] = {**row, "price": float(row["close"])}
    waiting, fetch_jobs = [], set()
    for records in frozen.values():
        for record in records:
            origin_date = _day(record.get("origin_date"))
            if origin_date is None or origin_date > report_day:
                continue
            item = dict(record)
            recent = origin_date in {report_day, closest}
            sessions, due_times = (None,) * 3, (None,) * 3
            if (target := _target(item.get("target"))) and (published := _time(item.get("origin_generated_at"))):
                try:
                    first, week = _future_sessions(target, published, calendar)
                    sessions = (first, first, week)
                    due_times = (min(first.open + timedelta(hours=1), first.close), first.close, week.close)
                except CalendarUnavailable:
                    pass
            due_now = any(due and due <= cutoff and (previous_cutoff is None or previous_cutoff < due)
                          for due in due_times)
            retry = any(due and cutoff - timedelta(days=1) <= due <= cutoff and (item["id"], stage) not in reviewed
                        for stage, due in zip(STAGES, due_times))
            if not (recent or due_now or retry):
                continue
            result["items"].append(item)
            for stage, session, due in zip(STAGES, sessions, due_times):
                existing = reviewed.get((item["id"], stage))
                if existing and session and existing.get("session_date") == session.date.isoformat():
                    item[stage] = existing
                elif due and cutoff < due:
                    item[stage] = _checkpoint("pending", session, "检查时点尚未到达")
                elif not session or not item.get("baseline"):
                    reason = "缺少已冻结且与后续交易时段相接的前收盘价" if session else "缺少可核验的跟踪标的或交易日历"
                    item[stage] = _checkpoint("unavailable", session, reason)
                else:
                    mode = "intraday" if stage == "intraday" else "daily"
                    quote = observations.get((target, session.date.isoformat())) if mode == "daily" else None
                    if quote and _time(quote.get("as_of")) == session.close:
                        item[stage] = _assess(quote, item, session, stage)
                    else:
                        fetch_jobs.add((target, mode))
                        waiting.append((item, stage, session, due, mode))
    charts = _fetch_charts(fetch_jobs, chart_fetcher)
    for item, stage, session, due, mode in waiting:
        chart = charts.get((item["target"], mode), {})
        try:
            observed = _intraday(chart, session, due) if mode == "intraday" else _daily(chart, session, cutoff, calendar)
        except CalendarUnavailable:
            observed = None
        item[stage] = _assess(observed, item, session, stage)
    for item in result["items"]:
        item["followup"] = _followup(item, report.get("news") or [], cutoff)
        relative_note = _compare_benchmark(item, observations)
        statuses = [item[stage]["status"] for stage in STAGES]
        if statuses[-1] == "pending":
            item["pricing"] = {"status": "pending", "reason": relative_note + "等待第五个交易日，并核对事前预期、估值和公告后的预期变化。"}
        else:
            outcome = ("三个价格检查点方向一致" if all(value == "aligned" for value in statuses)
                       else "有价格检查点与原判断相反" if "opposed" in statuses else "价格方向证据仍不足")
            item["pricing"] = {"status": "unverified", "reason": outcome + "。" + relative_note + "定价程度待事前预期、估值与后续预期变化核对。"}
    result["items"].sort(key=lambda row: (row["origin_date"], row["origin_generated_at"]), reverse=True)
    return result
