"""Separate, timestamped market references; never replace index identity."""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .models import iso_utc
from .portfolio_chart import _request_json
from .us_sessions import session_at, us_quote_fields


REFERENCE_DEFINITIONS = {
    "SOXX": {"name": "iShares 半导体 ETF", "instrument_type": "ETF", "unit": "USD",
             "label": "SOXX 半导体 ETF 盘外参考", "baseline_label": "较最近常规收盘"},
    "NQ=F": {"name": "纳斯达克 100 期货", "instrument_type": "FUTURE", "unit": "points",
             "label": "NQ 纳指 100 期货参考", "baseline_label": "较来源前收盘"},
    "NQ.CFD": {"name": "新浪纳指 CFD", "instrument_type": "CFD", "unit": "points",
               "label": "新浪纳指 CFD 参考", "baseline_label": "较来源前收盘"},
}
MAX_QUOTE_LAG_SECONDS = 120
CFD_MAX_QUOTE_AGE_SECONDS = 30
# Yahoo's exchange table identifies CME quotes as delayed by ten minutes.
# https://help.yahoo.com/kb/SLN2310.html
CME_DELAY_SECONDS = 600


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _positive(value: Any) -> float | None:
    value = _number(value)
    return value if value is not None and value > 0 else None


def empty_index_reference(symbol: str, error: str | None = None) -> dict:
    definition = REFERENCE_DEFINITIONS[symbol]
    cfd = symbol == "NQ.CFD"
    return {**definition, "active": False, "symbol": symbol, "price": None,
            "previous_close": None, "change": None, "change_pct": None,
            "quote_time": None, "fetched_at": None, "source": "新浪财经" if cfd else "Yahoo Finance",
            "source_url": ("https://finance.sina.com.cn/futures/quotes/NQ.shtml" if cfd
                           else f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/"),
            "status": "unavailable" if error else "waiting", "session": "unknown",
            "market_session": "unknown", "timezone": "Asia/Shanghai" if cfd else "America/New_York", "error": error,
            "cached": False, "restored_from_cache": False,
            "quote_age_seconds": None, "delay_status": "delayed" if symbol == "NQ=F" else "unknown",
            "delay_seconds": CME_DELAY_SECONDS if symbol == "NQ=F" else None,
            "session_periods": {}}


def _cme_closed(moment: datetime) -> bool:
    local = moment.astimezone(ZoneInfo("America/New_York"))
    minutes = local.hour * 60 + local.minute
    # CME Globex NQ: Sunday 18:00 through Friday 17:00 ET, daily 17:00–18:00 break.
    # https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.timeAndSales.html
    return (local.weekday() == 5 or local.weekday() == 6 and minutes < 1080
            or local.weekday() == 4 and minutes >= 1020 or 1020 <= minutes < 1080)


def _future_session(moment: datetime, periods: dict) -> str:
    if _cme_closed(moment):
        return "closed"
    if session_at(periods, moment.timestamp()) == "closed":
        return "unknown"
    local = moment.astimezone(ZoneInfo("America/New_York"))
    return "regular" if 570 <= local.hour * 60 + local.minute < 960 else "overnight"


def _valid_periods(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    periods = {}
    for key in ("pre", "regular", "post"):
        period = value.get(key)
        if not isinstance(period, dict):
            continue
        start, end = _positive(period.get("start")), _positive(period.get("end"))
        if start is not None and end is not None and 0 < end - start <= 86400:
            periods[key] = {"start": start, "end": end}
    return periods


def fetch_index_reference(symbol: str, timeout_seconds: int, now: datetime) -> dict:
    if symbol == "NQ.CFD":
        return _fetch_sina_cfd_reference(timeout_seconds, now)
    reference = empty_index_reference(symbol)
    payload = _request_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        "?interval=1m&range=5d&includePrePost=true", timeout_seconds=timeout_seconds)
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        if (not isinstance(meta, dict) or meta.get("symbol") != symbol
                or meta.get("instrumentType") != reference["instrument_type"]
                or meta.get("currency") != "USD" or meta.get("exchangeTimezoneName") != "America/New_York"):
            raise ValueError("Reference identity mismatch")
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Invalid reference response") from exc
    periods = _valid_periods(meta.get("currentTradingPeriod"))
    delay = _number(meta.get("exchangeDataDelayedBy"))
    if delay is not None and 0 <= delay <= 1440:
        reference.update(delay_seconds=delay * 60, delay_status="delayed" if delay else "reported_realtime")
    if symbol == "SOXX":
        fields = us_quote_fields(result, now=now)
        price, quote_time = _positive(fields.get("price")), fields.get("quote_time")
        if price is None or not quote_time:
            raise ValueError("Missing timestamped ETF reference")
        stamp = datetime.fromisoformat(quote_time)
        quote_session = fields.get("quote_session", "unknown")
        # Extended changes are relative to the latest regular close, including post-market.
        previous = (_positive(fields.get("regular_price"))
                    if fields.get("extended_change_pct") is not None else None)
    else:
        candidates = []
        stamp, price = _positive(meta.get("regularMarketTime")), _positive(meta.get("regularMarketPrice"))
        if stamp is not None and price is not None and stamp <= now.timestamp() + 2:
            candidates.append((stamp, price))
        quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        for stamp, price in zip(result.get("timestamp") or [], quotes.get("close") or []):
            stamp, price = _positive(stamp), _positive(price)
            if stamp is not None and price is not None and stamp <= now.timestamp():
                candidates.append((stamp, price))
        if not candidates:
            raise ValueError("Missing timestamped futures reference")
        stamp, price = max(candidates, key=lambda point: point[0])
        stamp = datetime.fromtimestamp(stamp, timezone.utc)
        quote_session = _future_session(stamp, periods)
        # chartPreviousClose is the beginning of a five-day chart, not the previous session.
        previous = _positive(meta.get("previousClose"))
    change = price - previous if previous is not None else None
    change_pct = _number((price / previous - 1) * 100) if previous is not None else None
    reference.update(price=price, previous_close=previous, change=change, change_pct=change_pct,
                     quote_time=iso_utc(stamp), fetched_at=iso_utc(now), session=quote_session,
                     session_periods=periods)
    return evaluate_reference({"session_state": "open"}, reference, now)


def _fetch_sina_cfd_reference(timeout_seconds: int, now: datetime) -> dict:
    request = Request("https://hq.sinajs.cn/list=hf_NQ",
                      headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"})
    started = time.monotonic()
    with urlopen(request, timeout=max(0.1, min(5, float(timeout_seconds)))) as response:
        raw = response.read(20001)
    fetched_at = now + timedelta(seconds=max(0, time.monotonic() - started))
    if len(raw) > 20000:
        raise ValueError("CFD quote response too large")
    match = re.fullmatch(r'\s*var\s+hq_str_hf_NQ="([^"\r\n]+)";\s*', raw.decode("gb18030"))
    if not match:
        raise ValueError("CFD reference identity mismatch")
    fields = match[1].split(",")
    if len(fields) < 14 or "纳斯达克" not in fields[13]:
        raise ValueError("Invalid Nasdaq CFD reference response")
    price, previous = _positive(fields[0]), _positive(fields[7])
    if price is None:
        raise ValueError("Missing positive finite CFD reference")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", fields[12] + " " + fields[6]):
        raise ValueError("Invalid CFD source timestamp")
    stamp = datetime.strptime(fields[12] + " " + fields[6], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
    if stamp.timestamp() <= 0 or (stamp - fetched_at).total_seconds() > 2:
        raise ValueError("Invalid or future CFD source timestamp")
    reference = empty_index_reference("NQ.CFD")
    reference.update(price=price, previous_close=previous,
                     change=price - previous if previous is not None else None,
                     change_pct=_number((price / previous - 1) * 100) if previous is not None else None,
                     quote_time=iso_utc(stamp), fetched_at=iso_utc(fetched_at), provider_name=fields[13],
                     session="reference", session_basis="regular_hours_not_holiday_calendar")
    return evaluate_reference({"session_state": "open"}, reference, fetched_at)


def evaluate_reference(index_row: dict, reference: dict, now: datetime) -> dict:
    """Re-evaluate cached data against the actual clock and each instrument's session."""
    result = dict(reference, active=False)
    result["cached"] = bool(result.get("error") or result.get("cached") or result.get("restored_from_cache"))
    symbol = result.get("symbol")
    definition = REFERENCE_DEFINITIONS.get(symbol)
    if not definition or result.get("instrument_type") != definition["instrument_type"]:
        result.update(status="unavailable", error="Reference identity mismatch")
        return result
    periods = _valid_periods(result.get("session_periods"))
    current_session = (("closed" if _cme_closed(now) else "reference") if symbol == "NQ.CFD"
                       else _future_session(now, periods) if symbol == "NQ=F"
                       else session_at(periods, now.timestamp()) if periods else "unknown")
    result["market_session"] = current_session
    try:
        stamp = datetime.fromisoformat(str(result.get("quote_time")).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.timestamp() <= 0 or (stamp - now).total_seconds() > 2:
            raise ValueError("Invalid reference timestamp")
        age = max(0, (now - stamp).total_seconds())
    except (TypeError, ValueError, OverflowError):
        result.update(status="unavailable", quote_age_seconds=None)
        return result
    result["quote_age_seconds"] = round(age, 3)
    if _positive(result.get("price")) is None:
        result["status"] = "unavailable"
    elif result.get("error") or result.get("cached"):
        result["status"] = "stale"
    elif current_session == "closed":
        result["status"] = "closed"
    else:
        delay = _number(result.get("delay_seconds")) or 0
        fresh = (age < CFD_MAX_QUOTE_AGE_SECONDS if symbol == "NQ.CFD"
                 else age <= max(0, delay) + MAX_QUOTE_LAG_SECONDS)
        if symbol == "NQ.CFD":
            in_session = current_session == "reference" and not _cme_closed(stamp)
        elif symbol == "SOXX":
            in_session = (current_session in {"pre", "post"} and result.get("session") == current_session
                          and session_at(periods, stamp.timestamp()) == current_session)
        else:
            in_session = (current_session in {"regular", "overnight"}
                          and _future_session(stamp, periods) in {"regular", "overnight"})
        result["status"] = ("delayed" if delay > 0 else "available") if fresh and in_session else "stale"
        result["active"] = bool(delay <= 0 and fresh and in_session
                                and index_row.get("session_state") in {"closed", "break"})
    return result
