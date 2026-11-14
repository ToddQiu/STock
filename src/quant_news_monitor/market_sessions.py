"""Scheduled exchange sessions, including holidays, DST and shortened sessions.

These are planned exchange calendars, not a claim that an exchange is live.
Unexpected suspensions still require the exchange's announcement and quote data.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


MARKETS = {
    "CN": {"name": "A股", "calendar": "XSHG", "timezone": "Asia/Shanghai"},
    "HK": {"name": "港股", "calendar": "XHKG", "timezone": "Asia/Hong_Kong"},
    "US": {"name": "美股", "calendar": "XNYS", "timezone": "America/New_York"},
    "KR": {"name": "韩股", "calendar": "XKRX", "timezone": "Asia/Seoul"},
}


class CalendarUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class Session:
    market: str
    date: date
    open: datetime
    close: datetime
    source: str


class SessionCalendar:
    """Lazily build a bounded year; never substitute weekday assumptions."""

    def __init__(self):
        self._cache = {}
        self._lock = threading.RLock()

    def _calendar(self, market, year):
        if market not in MARKETS:
            raise ValueError("市场必须是 CN、HK、US 或 KR")
        with self._lock:
            key = (market, year)
            if key not in self._cache:
                try:
                    import exchange_calendars
                    value = exchange_calendars.get_calendar(
                        MARKETS[market]["calendar"], start=f"{year}-01-01", end=f"{year}-12-31")
                    self._cache[key] = (value, f"exchange-calendars {exchange_calendars.__version__} / {value.name}")
                except ImportError as error:
                    raise CalendarUnavailable("未安装交易日历依赖，自动早晚报暂不运行") from error
                except (ValueError, OverflowError) as error:
                    raise CalendarUnavailable(f"{MARKETS[market]['name']} {year} 年交易日历尚无有效覆盖，请更新日历依赖") from error
            return self._cache[key]

    def session(self, market: str, day: date) -> Session | None:
        cal, source = self._calendar(market, day.year)
        # first/last session can exclude holidays at the requested year's edges.
        if day < cal.first_session.date() or day > cal.last_session.date():
            return None
        if not cal.is_session(day.isoformat()):
            return None
        return Session(market, day, cal.session_open(day.isoformat()).to_pydatetime(),
                       cal.session_close(day.isoformat()).to_pydatetime(), source)

    def previous(self, market: str, day: date) -> Session:
        for offset in range(1, 46):
            value = self.session(market, day - timedelta(days=offset))
            if value is not None:
                return value
        raise CalendarUnavailable("前一交易日超出可用日历范围")

    def next(self, market: str, day: date) -> Session:
        for offset in range(1, 46):
            value = self.session(market, day + timedelta(days=offset))
            if value is not None:
                return value
        raise CalendarUnavailable("下一交易日超出可用日历范围")

    def last_completed(self, market: str, now: datetime) -> Session:
        day = now.astimezone(ZoneInfo(MARKETS[market]["timezone"])).date()
        current = self.session(market, day)
        if current is not None and current.close <= now:
            return current
        return self.previous(market, day)
