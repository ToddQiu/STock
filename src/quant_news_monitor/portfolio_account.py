from __future__ import annotations

import math
from datetime import timedelta
from zoneinfo import ZoneInfo

from .market_sessions import CalendarUnavailable, MARKETS, SessionCalendar
from .models import utc_now
from .portfolio_alerts import _time as quote_time_utc
from .portfolio_alerts import conversion_current, quote_usable
from .portfolio_dividends import summarize
from .portfolio_trades import summarize as summarize_trades
from . import portfolio_cash


_CALENDAR = SessionCalendar()
_REFERENCE_AGE = timedelta(minutes=20)


def _latest_quote_session(market, day, now):
    try:
        session = _CALENDAR.session(market, day)
        return session if session and now >= session.open else _CALENDAR.previous(market, day)
    except CalendarUnavailable:
        return None


def amount(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def apply_account(result, snapshot, account, *, now=None):
    """Add display-unit amounts without reinterpreting stored costs or legacy CNY fields."""
    rates = {**(snapshot.get("fx_rates") or {}), "CNY": 1}
    base = account.get("base_currency", "CNY")
    now = now or utc_now()
    denominator = amount(rates.get(base))
    def convert(value, currency="CNY"):
        number, rate = amount(value), amount(rates.get(currency))
        if number is not None and (currency == base or number == 0):
            return number
        return number * rate / denominator if number is not None and rate and denominator and rate > 0 and denominator > 0 else None
    summary = result["summary"]
    source_current = snapshot.get("status") not in {"stale", "error", "waiting"} and not snapshot.get("restored_from_cache")
    sessions = {}
    daily_times = []
    daily_periods = {}
    fx_reference_count = 0
    fx_reference_since = now - _REFERENCE_AGE
    # FX references may remain at Friday's close while markets are shut over the weekend.
    new_york_now = now.astimezone(ZoneInfo("America/New_York"))
    if new_york_now.weekday() >= 5 or (new_york_now.weekday() == 4 and new_york_now.hour >= 17):
        try:
            fx_reference_since = _CALENDAR.last_completed("US", now).close - _REFERENCE_AGE
        except CalendarUnavailable:
            pass
    delayed_count = prior_session_count = 0
    for row in result["positions"]:
        currency = row.get("currency") or "CNY"
        for key in ("market_value", "cost_value", "pnl", "daily_pnl"):
            row[key + "_base"] = convert(row.get(key), currency)
        quote = {"price": row.get("price"), "status": row.get("quote_status"),
                 "quote_time": row.get("quote_time"), "error": row.get("quote_error")}
        market = row.get("market", "")
        fx_current = conversion_current(snapshot, currency, base, now)
        row["valuation_current"] = bool(source_current
            and quote_usable(quote, now, fund=market == "FUND", market=market)
            and fx_current)
        daily_quote = quote
        row["daily_change_pct"] = row.get("change_pct")
        row["daily_previous_value_base"] = convert((amount(row.get("quantity")) or 0) * (amount(row.get("previous_close")) or 0), currency)
        session = None
        if market == "US":
            day = now.astimezone(ZoneInfo("America/New_York")).date()
            session = _latest_quote_session(market, day, now)
            has_regular = bool(row.get("regular_quote_time"))
            regular_time = row["regular_quote_time"] if has_regular else row.get("quote_time")
            stamp = quote_time_utc(regular_time)
            regular_price = amount(row.get("regular_price") if has_regular else row.get("price"))
            previous = amount(row.get("regular_previous_close") if has_regular else row.get("previous_close"))
            valid = bool(session and stamp and max(session.open, min(now, session.close) - _REFERENCE_AGE) <= stamp
                         <= min(now + timedelta(minutes=1), session.close + timedelta(minutes=5))
                         and regular_price and regular_price > 0 and previous and previous > 0
                         and not row.get("quote_fetch_error") and (has_regular or row.get("quote_session") not in {"pre", "post", "overnight"}))
            daily_amount = (row["quantity"] * (regular_price - previous)) if valid else None
            row["daily_pnl"] = daily_amount
            rate = amount(rates.get(currency))
            row["daily_pnl_cny"] = daily_amount * rate if daily_amount is not None and rate and rate > 0 else None
            row["daily_pnl_base"] = convert(daily_amount, currency)
            row["daily_change_pct"] = (regular_price / previous - 1) * 100 if valid else None
            row["daily_previous_value_base"] = convert(row["quantity"] * previous, currency) if valid else None
            daily_quote = {"price": regular_price, "quote_time": regular_time,
                           "status": "closed" if valid and now >= session.close else quote["status"],
                           "error": row.get("quote_fetch_error")}
        daily_fx = conversion_current(snapshot, currency, base, now, reference_since=fx_reference_since)
        if row["daily_pnl_base"] is None:
            continue
        stamp = quote_time_utc(daily_quote["quote_time"])
        zone = ZoneInfo(MARKETS[market]["timezone"]) if market in MARKETS else ZoneInfo("Asia/Shanghai")
        today = now.astimezone(zone).date()
        quote_day = stamp.astimezone(zone).date() if stamp else None
        delayed_reference = False
        if (source_current and daily_fx and daily_quote["status"] == "delayed" and not daily_quote["error"]
                and market in MARKETS and stamp and -timedelta(minutes=1) <= now - stamp <= timedelta(days=7)):
            # An estimate may use a timestamped delayed quote without passing the risk/valuation gate.
            if market not in sessions:
                sessions[market] = _latest_quote_session(market, today, now)
            session = sessions[market]
            if session and quote_day == session.date:
                earliest = max(session.open, min(now, session.close) - _REFERENCE_AGE)
                delayed_reference = stamp >= earliest
        daily_current = source_current and daily_fx and quote_usable(daily_quote, now, fund=market == "FUND", market=market)
        if not daily_current and not delayed_reference:
            row["daily_pnl_base"] = None
            continue
        if stamp:
            daily_times.append(stamp)
            prior_session_count += quote_day < today
        delayed_count += delayed_reference
        fx_reference_count += not fx_current
        if market in MARKETS:
            session = session or _latest_quote_session(market, today, now)
            if session:
                try:
                    previous_session = _CALENDAR.previous(market, session.date)
                    daily_periods[market] = {"market": market, "start": previous_session.close.isoformat(),
                        "end": min(now, session.close).isoformat(), "closed": now >= session.close}
                except CalendarUnavailable:
                    pass
    for key in ("total_market_value", "total_cost", "total_pnl", "daily_pnl"):
        row_key = {"total_market_value": "market_value", "total_cost": "cost_value", "total_pnl": "pnl", "daily_pnl": "daily_pnl"}[key] + "_base"
        values = [r[row_key] for r in result["positions"] if r[row_key] is not None]
        summary[key + "_base"] = sum(values) if values or not result["positions"] else None
    daily_cny = [r["daily_pnl_cny"] for r in result["positions"] if r["daily_pnl_cny"] is not None]
    summary["daily_pnl_cny"] = sum(daily_cny) if daily_cny or not result["positions"] else None
    principal = account.get("principal", account.get("principal_cny"))
    principal_currency = account.get("principal_currency", "CNY") if "principal" in account else "CNY"
    cash_currency = account.get("cash_currency", "CNY")
    principal_base = convert(principal, principal_currency)
    records = account.get("dividends", [])
    dividends = summarize(records, convert)
    trades = summarize_trades(account.get("trades", []), convert, dividends=records)
    receivable_balances = account.get("receivable_balances", {})
    receivable_details = [{"currency": c, "amount": v, "amount_base": convert(v, c)} for c, v in receivable_balances.items()]
    receivable = sum(r["amount_base"] for r in receivable_details) if all(r["amount_base"] is not None for r in receivable_details) else None
    payable_balances = account.get("payable_balances", {})
    payable_details = [{"currency": c, "amount": v, "amount_base": convert(v, c)} for c, v in payable_balances.items()]
    payable = sum(r["amount_base"] for r in payable_details) if all(r["amount_base"] is not None for r in payable_details) else None
    cash_mode = portfolio_cash.mode(account)
    cash_balances = portfolio_cash.balances(account)
    cash_details = [{"currency": currency, "amount": value, "amount_base": convert(value, currency)}
                    for currency, value in cash_balances.items()] if cash_mode == "manual" else []
    cash = sum(row["amount_base"] for row in cash_details) if cash_details and all(
        row["amount_base"] is not None for row in cash_details) else None
    if cash_mode == "auto":
        realized = trades["summary"]["realized_net_base"]
        received = dividends["received_net_base"] if dividends["received_count"] else 0
        costs_complete = all(row["cost_value_base"] is not None for row in result["positions"])
        if principal_base is not None and costs_complete and realized is not None and received is not None:
            cash = principal_base - summary["total_cost_base"] + realized + received
    cash_status = "unavailable" if cash is None else "estimated" if cash_mode == "auto" else "manual"
    total = summary["total_market_value_base"]
    positions_complete = all(r["market_value_base"] is not None for r in result["positions"])
    complete = positions_complete and total is not None and cash is not None and receivable is not None and payable is not None
    current = complete and source_current and all(row["valuation_current"] for row in result["positions"])
    current = bool(current and (cash_mode == "auto" or all(
        not value or conversion_current(snapshot, currency, base, now) for currency, value in cash_balances.items())))
    current = bool(current and all(not v or conversion_current(snapshot, c, base, now) for c, v in receivable_balances.items()))
    current = bool(current and all(not v or conversion_current(snapshot, c, base, now) for c, v in payable_balances.items()))
    net_assets = total + cash + receivable - payable if complete else None
    summary.update(payable_base=payable, payable_details=payable_details)
    summary.update(receivable_base=receivable, receivable_details=receivable_details)
    summary.update(principal_base=principal_base, cash_base=cash, cash_status=cash_status, cash_details=cash_details, net_assets_base=net_assets,
                   valuation_current=current, positions_valuation_complete=positions_complete,
                   base_valuation_complete=positions_complete, base_valued_count=sum(r["market_value_base"] is not None for r in result["positions"]))
    summary["stale_valued_count"] = sum(r["market_value_base"] is not None and not r["valuation_current"] for r in result["positions"])
    summary["valuation_complete"] = positions_complete
    summary["valued_count"] = summary["base_valued_count"]
    summary["daily_pnl_count"] = sum(r["daily_pnl_base"] is not None for r in result["positions"])
    summary["daily_pnl_complete"] = summary["daily_pnl_count"] == len(result["positions"])
    daily_rows = [r for r in result["positions"] if r["daily_pnl_base"] is not None]
    daily_basis = [r["daily_previous_value_base"] for r in daily_rows if r["daily_previous_value_base"] is not None]
    previous_value = sum(daily_basis) if len(daily_basis) == len(daily_rows) and all(v > 0 for v in daily_basis) else 0
    summary["daily_pnl_pct"] = summary["daily_pnl_base"] / previous_value * 100 if previous_value > 0 else None
    summary["daily_pnl_delayed_count"] = delayed_count
    summary["daily_pnl_prior_session_count"] = prior_session_count
    summary["daily_pnl_quote_time"] = min(daily_times).isoformat() if daily_times else None
    summary["daily_pnl_periods"] = list(daily_periods.values())
    summary["daily_pnl_fx_reference_count"] = fx_reference_count
    priced_cost = sum(r["cost_value_base"] for r in result["positions"] if r["pnl_base"] is not None and r["cost_value_base"] is not None)
    summary["total_pnl_pct"] = summary["total_pnl_base"] / priced_cost * 100 if priced_cost > 0 and summary["total_pnl_base"] is not None else None
    if principal is not None or cash_balances:
        valid = (current and principal_base and principal_base > 0
                 and conversion_current(snapshot, principal_currency, base, now))
        rate = (net_assets / principal_base - 1) * 100 if valid else None
        summary.update(principal_return_pct=rate, principal_loss_pct=max(0, -rate) if rate is not None else None,
                       principal_pnl_cny=(net_assets - principal_base) * denominator if valid and denominator else None,
                       principal_status="available" if valid else "incomplete" if not complete else "unset" if not principal_base else "unavailable")
    elif summary.get("principal_status") == "available" and (
            not current or not conversion_current(snapshot, principal_currency, base, now)):
        summary.update(principal_return_pct=None, principal_loss_pct=None,
                       principal_pnl_cny=None, principal_status="unavailable")
    result.update(base_currency=base, fx_rates=rates, fx_details=snapshot.get("fx_details") or {}, account={
        "base_currency": base, "principal": principal, "principal_currency": principal_currency,
        "cash": account.get("cash"), "cash_currency": cash_currency, "cash_mode": cash_mode,
        "cash_balances": cash_balances, "receivable_balances": receivable_balances, "payable_balances": payable_balances,
        "position_limit": account.get("position_limit", 35), "sector_limit": account.get("sector_limit", 50),
        "top5_limit": account.get("top5_limit", 80), "owner": account.get("owner", ""),
        "strategy": account.get("strategy", ""), "benchmark": account.get("benchmark", ""),
    })
    result["dividends"] = {"records": records, **dividends}
    for row in result["positions"]:
        row["dividends"] = summarize([r for r in records if
            (r["market"], r["symbol"]) == (row["market"], row["symbol"])], convert)
    result["trades"] = trades
    return result
