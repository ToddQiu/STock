from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import iso_utc, utc_now
from .market_sessions import CalendarUnavailable, SessionCalendar


SESSION_LABELS = {"pre": "盘前", "regular": "常规", "post": "盘后", "overnight": "夜盘", "closed": "休市"}
MAX_QUOTE_AGE_SECONDS = 180
EXTENDED_CHANGE_THRESHOLD = 2.0
EXTENDED_SESSIONS = {'pre', 'post', 'overnight'}
_CALENDAR = SessionCalendar()


def overnight_period(timestamp: float) -> dict:
    local = datetime.fromtimestamp(timestamp, ZoneInfo('America/New_York'))
    if 4 <= local.hour < 20:
        return {}
    day = local.date() + timedelta(days=1) if local.hour >= 20 else local.date()
    try:
        if _CALENDAR.session('US', day) is None:
            return {}
    except CalendarUnavailable:
        return {}
    end = datetime(day.year, day.month, day.day, 4, tzinfo=local.tzinfo)
    return {'start': (end - timedelta(hours=8)).timestamp(), 'end': end.timestamp()}


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def session_at(periods: dict, timestamp: float) -> str:
    for key in ("pre", "regular", "post", "overnight"):
        period = periods.get(key) or {}
        start, end = number(period.get("start")), number(period.get("end"))
        if start is not None and end is not None and start <= timestamp < end:
            return key
    return "closed"


def chart_session_at(meta: dict, timestamp: float) -> str:
    current = session_at(meta.get("currentTradingPeriod") or {}, timestamp)
    if current != "closed":
        return current
    history = meta.get("tradingPeriods") or {}
    if not isinstance(history, dict):
        return "unknown"
    for key, days in history.items():
        for day in days:
            for period in day:
                if isinstance(period, dict) and session_at({key: period}, timestamp) == key:
                    return key
    return "unknown"


def us_quote_fields(result: dict, *, now: datetime | None = None, latest: dict | None = None) -> dict:
    """Select timestamped US session data, never an untimed full-day price."""
    meta = dict(result.get("meta") or {})
    if meta.get("exchangeTimezoneName") != "America/New_York" or meta.get("instrumentType") not in {"EQUITY", "ETF"}:
        return {}
    now = now or utc_now()
    current = now.timestamp()
    latest = latest or {}
    if latest and (latest.get('symbol') != meta.get('symbol') or latest.get('currency') != meta.get('currency')):
        latest = {}
    # A newer session quote may arrive before the minute chart's metadata catches up.
    latest_time = number(latest.get('regularMarketTime'))
    latest_price = number(latest.get('regularMarketPrice'))
    if latest_price and latest_price > 0 and latest_time and latest_time <= current and latest_time >= (number(meta.get('regularMarketTime')) or 0):
        for key in ('regularMarketPrice', 'regularMarketTime'):
            meta[key] = latest.get(key)
        if number(latest.get('regularMarketPreviousClose')):
            meta['previousClose'] = latest['regularMarketPreviousClose']
    periods = dict(meta.get("currentTradingPeriod") or {})
    night = overnight_period(current)
    if night:
        periods['overnight'] = night
    active = session_at(periods, current)
    if active == 'closed' and latest:
        local = now.astimezone(ZoneInfo('America/New_York'))
        try:
            scheduled = _CALENDAR.session('US', local.date())
        except CalendarUnavailable:
            scheduled = None
        if scheduled and 4 <= local.hour < 20:
            periods.update(pre={'start': local.replace(hour=4, minute=0, second=0, microsecond=0).timestamp(), 'end': scheduled.open.timestamp()},
                           regular={'start': scheduled.open.timestamp(), 'end': scheduled.close.timestamp()},
                           post={'start': scheduled.close.timestamp(), 'end': local.replace(hour=20, minute=0, second=0, microsecond=0).timestamp()})
            active = session_at(periods, current)
    regular_price = number(meta.get("regularMarketPrice"))
    regular_price = regular_price if regular_price and regular_price > 0 else None
    regular_time = number(meta.get("regularMarketTime"))
    prior_close = number(meta.get("previousClose") or meta.get("chartPreviousClose"))
    candidates = []
    if regular_price and regular_price > 0 and regular_time and regular_time <= current + 2:
        candidates.append((regular_time, regular_price, "regular", 'market_quote'))
    for prefix, session in (('pre', 'pre'), ('post', 'post'), ('overnight', 'overnight')):
        timestamp, price = number(latest.get(prefix + 'MarketTime')), number(latest.get(prefix + 'MarketPrice'))
        if timestamp is None or timestamp <= 0 or price is None or price <= 0 or timestamp > current:
            continue
        if prefix == 'overnight' and not overnight_period(timestamp):
            continue
        if prefix == 'post' and overnight_period(timestamp):
            session = 'overnight'
        candidates.append((timestamp, price, session, 'market_quote'))
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    for timestamp, close in zip(result.get("timestamp") or [], quote.get("close") or []):
        timestamp, close = number(timestamp), number(close)
        if timestamp is None or timestamp <= 0 or close is None or close <= 0 or timestamp > current:
            continue
        session = chart_session_at(meta, timestamp)
        if session == 'unknown' and overnight_period(timestamp):
            session = 'overnight'
        if session in EXTENDED_SESSIONS | {'regular'}:
            candidates.append((timestamp, close, session, 'minute_sample'))
    if not candidates:
        return {"market_session": active, "status": "stale", "quote_error": "没有有效时间戳的报价"}
    timestamp, price, quote_session, price_kind = max(candidates, key=lambda point: point[0])
    regular = [point for point in candidates if point[2] == 'regular']
    if regular:
        regular_time, regular_price = max(regular, key=lambda point: point[0])[:2]
    regular_start = number((periods.get("regular") or {}).get("start"))
    # Before the next opening, regularMarketPrice is the most recent session's close.
    baseline = regular_price if quote_session in {'pre', 'overnight'} else prior_close
    if quote_session == "regular" and active in {"pre", "regular"} and session_at(periods, timestamp) != "regular":
        baseline = None
    if quote_session == "post" and regular_start is not None and (not regular_time or regular_time < regular_start):
        baseline = None
    extended_reference = regular_price if regular_time and regular_time <= timestamp else None
    if quote_session == "post" and regular_start is not None and (not regular_time or regular_time < regular_start):
        extended_reference = None
    extended_change = (price / extended_reference - 1) * 100 if quote_session in EXTENDED_SESSIONS and extended_reference else None
    daily_change = (price / baseline - 1) * 100 if baseline and baseline > 0 else None
    age = max(0, current - timestamp)
    active_quote = active != "closed" and quote_session == active and session_at(periods, timestamp) == active
    status = "closed" if active == "closed" else "live" if active_quote and age <= MAX_QUOTE_AGE_SECONDS else "stale"
    # exchangeDataDelayedBy describes the listing exchange, not Blue Ocean's overnight feed.
    if status == 'live' and quote_session != 'overnight' and latest and (number(latest.get('exchangeDataDelayedBy')) or 0) > 0 and price_kind == 'market_quote':
        status = 'delayed'
    message = "" if status == "live" else "休市，保留最近成交" if status == "closed" else "当前时段报价缺失或超过 3 分钟，暂停异动判断"
    if status == 'delayed':
        message = ''
    elif active == 'overnight' and status == 'stale':
        message = '夜盘报价暂缺或已过期，保留最近成交并持续刷新'
    regular_previous = number(latest.get('regularMarketPreviousClose')) if latest_time == regular_time else None
    if regular_previous is None or regular_previous <= 0:
        regular_previous = prior_close if regular_time and regular_start and regular_time >= regular_start else None
    return {
        "price": price, "quote_time": iso_utc(datetime.fromtimestamp(timestamp, tz=timezone.utc)),
        "previous_close": baseline, "change_pct": daily_change,
        "regular_price": regular_price, "regular_quote_time": iso_utc(datetime.fromtimestamp(regular_time, tz=timezone.utc)) if regular_time else None,
        "regular_previous_close": regular_previous,
        "extended_change_pct": extended_change, "session_change_pct": extended_change if quote_session in EXTENDED_SESSIONS else daily_change,
        "market_session": active, "quote_session": quote_session, "session_periods": periods,
        "price_label": f"{SESSION_LABELS[quote_session]}价", "status": status,
        "quote_error": message, "quote_age_seconds": round(age), "price_kind": price_kind,
        "extended_hours": True, "source_note": 'Yahoo / Blue Ocean 夜盘成交报价，非逐笔推送' if quote_session == 'overnight' else 'Yahoo 分时段报价 · 来源标记延迟' if status == 'delayed' else 'Yahoo 分时段报价' if latest else "Yahoo 分钟行情，非逐笔推送",
    }


def build_extended_monitor(positions: list[dict], *, now: datetime | None = None) -> dict:
    now = now or utc_now()
    rows, events = [], []
    for item in positions:
        if item.get("market") != "US":
            continue
        periods = item.get("session_periods") or {}
        session = session_at(periods, now.timestamp()) if periods else "unknown"
        try:
            timestamp = datetime.fromisoformat(item.get("quote_time") or "").timestamp()
        except (TypeError, ValueError):
            timestamp = 0
        change = number(item.get("extended_change_pct"))
        eligible = (session in EXTENDED_SESSIONS and item.get("quote_session") == session
                    and item.get("quote_status") == "live" and 0 <= now.timestamp()-timestamp <= MAX_QUOTE_AGE_SECONDS
                    and session_at(periods, timestamp) == session and change is not None)
        triggered = eligible and abs(change) >= EXTENDED_CHANGE_THRESHOLD
        rows.append({"symbol": item["symbol"], "name": item["name"], "market_session": session,
                     "eligible": eligible, "triggered": triggered, "quote_status": item.get("quote_status"),
                     "quote_time": item.get("quote_time"), "price": item.get("price"), "change_pct": change})
        if not triggered:
            continue
        label = SESSION_LABELS[session]
        events.append({
            "market": "US", "symbol": item["symbol"], "name": item["name"], "exchange": "US", "sector": "",
            "currency": item.get("currency") or "USD", "price": item["price"], "change_pct": change,
            "volume": 0, "relative_volume": None, "market_cap": 0, "movement_type": "extended_price",
            "intensity_score": round(min(100, abs(change)*2.6), 2), "direction": "up" if change > 0 else "down",
            "scan_id": item["quote_time"], "detected_at": item["quote_time"], "quote_time": item["quote_time"],
            "session": session, "session_label": label, "live_extended": True,
            "reference_price": item.get("regular_price"), "confidence": 0.0, "reason_category": "unconfirmed",
            "reason_summary": f"{label}相对最近常规收盘 {change:+.2f}%，达到 {EXTENDED_CHANGE_THRESHOLD:g}% 条件。价格条件已触发，消息原因尚未确认；低流动性可能放大波动。",
            "evidence_title": "", "evidence_url": "", "evidence_source": "", "evidence_published_at": None,
            "analyzer": "extended-session-price", "monitor_source": "portfolio", "monitor_source_label": "我的持仓",
        })
    return {"rows": rows, "events": events, "threshold_pct": EXTENDED_CHANGE_THRESHOLD,
            "eligible_count": sum(row["eligible"] for row in rows), "triggered_count": len(events)}
