from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .models import iso_utc, utc_now


SESSION_LABELS = {"pre": "盘前", "regular": "常规", "post": "盘后", "closed": "休市"}
MAX_QUOTE_AGE_SECONDS = 180
EXTENDED_CHANGE_THRESHOLD = 2.0


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def session_at(periods: dict, timestamp: float) -> str:
    for key in ("pre", "regular", "post"):
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


def us_quote_fields(result: dict, *, now: datetime | None = None) -> dict:
    """Select timestamped US session data, never an untimed full-day price."""
    meta = result.get("meta") or {}
    if meta.get("exchangeTimezoneName") != "America/New_York" or meta.get("instrumentType") not in {"EQUITY", "ETF"}:
        return {}
    now = now or utc_now()
    current = now.timestamp()
    periods = meta.get("currentTradingPeriod") or {}
    active = session_at(periods, current)
    regular_price = number(meta.get("regularMarketPrice"))
    regular_price = regular_price if regular_price and regular_price > 0 else None
    regular_time = number(meta.get("regularMarketTime"))
    prior_close = number(meta.get("previousClose") or meta.get("chartPreviousClose"))
    candidates = []
    if regular_price and regular_price > 0 and regular_time and regular_time <= current + 2:
        candidates.append((regular_time, regular_price, "regular"))
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    for timestamp, close in zip(result.get("timestamp") or [], quote.get("close") or []):
        timestamp, close = number(timestamp), number(close)
        if timestamp is None or close is None or close <= 0 or timestamp > current:
            continue
        session = chart_session_at(meta, timestamp)
        if session in {"pre", "post"}:
            candidates.append((timestamp, close, session))
    if not candidates:
        return {"market_session": active, "status": "stale", "quote_error": "没有有效时间戳的报价"}
    timestamp, price, quote_session = max(candidates, key=lambda point: point[0])
    regular_start = number((periods.get("regular") or {}).get("start"))
    # Before the next opening, regularMarketPrice is the most recent session's close.
    baseline = regular_price if quote_session == "pre" else prior_close
    if quote_session == "regular" and active in {"pre", "regular"} and session_at(periods, timestamp) != "regular":
        baseline = None
    if quote_session == "post" and regular_start is not None and (not regular_time or regular_time < regular_start):
        baseline = None
    extended_reference = regular_price if regular_time and regular_time <= timestamp else None
    if quote_session == "post" and regular_start is not None and (not regular_time or regular_time < regular_start):
        extended_reference = None
    extended_change = (price / extended_reference - 1) * 100 if quote_session in {"pre", "post"} and extended_reference else None
    daily_change = (price / baseline - 1) * 100 if baseline and baseline > 0 else None
    age = max(0, current - timestamp)
    active_quote = active != "closed" and quote_session == active and session_at(periods, timestamp) == active
    status = "closed" if active == "closed" else "live" if active_quote and age <= MAX_QUOTE_AGE_SECONDS else "stale"
    message = "" if status == "live" else "休市，保留最近成交" if status == "closed" else "当前时段报价缺失或超过 3 分钟，暂停异动判断"
    return {
        "price": price, "quote_time": iso_utc(datetime.fromtimestamp(timestamp, tz=timezone.utc)),
        "previous_close": baseline, "change_pct": daily_change,
        "regular_price": regular_price, "regular_quote_time": iso_utc(datetime.fromtimestamp(regular_time, tz=timezone.utc)) if regular_time else None,
        "extended_change_pct": extended_change, "session_change_pct": extended_change if quote_session in {"pre", "post"} else daily_change,
        "market_session": active, "quote_session": quote_session, "session_periods": periods,
        "price_label": f"{SESSION_LABELS[quote_session]}价", "status": status,
        "quote_error": message, "quote_age_seconds": round(age), "price_kind": "minute_sample",
        "extended_hours": True, "source_note": "Yahoo 分钟行情，非逐笔推送",
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
        eligible = (session in {"pre", "post"} and item.get("quote_session") == session
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
