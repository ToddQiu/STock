from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import iso_utc


RULES = {"take_profit_pct": "止盈线", "stop_loss_pct": "止损线", "take_profit_price": "止盈线", "stop_price": "止损线",
         "weight_min_pct": "仓位下限", "weight_max_pct": "仓位上限"}
FIXED_PRICE_RULES = {"take_profit_price", "stop_price"}


def normalize_risk_settings(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError("警戒线设置必须是对象")
    result = {}
    for key in ("enabled", "desktop_notify"):
        value = values.get(key, True)
        if not isinstance(value, bool):
            raise ValueError("提醒开关必须为布尔值")
        result[key] = value
    for key, label in RULES.items():
        value = values.get(key)
        if value is None or value == "":
            result[key] = None
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{label}必须为有效数字") from exc
        if isinstance(value, bool) or not math.isfinite(number):
            raise ValueError(f"{label}必须为有限数字")
        if key in FIXED_PRICE_RULES:
            if not 0.000001 <= number <= 1e12:
                raise ValueError(f"{label}价格须在 0.000001 到 1 万亿之间")
        elif key in {"take_profit_pct", "stop_loss_pct"}:
            if number < 0.000001:
                raise ValueError(f"{label}比例须为正数，最小 0.000001%")
            if key == "stop_loss_pct" and number > 100:
                raise ValueError("止损比例不能超过 100%")
        elif not 0 <= number <= 100:
            raise ValueError("仓位比例须在 0 到 100% 之间")
        result[key] = number
    if result["stop_loss_pct"] is not None and result["stop_price"] is not None:
        raise ValueError("止损比例与固定价格只能选一种")
    if result["take_profit_pct"] is not None and result["take_profit_price"] is not None:
        raise ValueError("止盈比例与固定价格只能选一种")
    low, high = result["weight_min_pct"], result["weight_max_pct"]
    if low is not None and high is not None and low >= high:
        raise ValueError("仓位下限必须小于上限")
    return result


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def quote_usable(quote: dict, now: datetime, *, fund: bool = False, market: str = "", allow_delayed: bool = False) -> bool:
    price = quote.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return False
    status = quote.get("status", "live")
    if (status not in {"live", "closed"} and not (allow_delayed and status == "delayed")) or quote.get("error"):
        return False
    stamp = _time(quote.get("quote_time"))
    if not stamp:
        return False
    # Confirmed NAV and verified closing prices are usable, unlike restored/error fallbacks.
    closed = status == "closed"
    if market in {"CN", "HK", "KR"} and not closed:
        from .portfolio_chart import chart_session_status
        closed = chart_session_status(market, str(quote.get("symbol", "")),
                                      {"stats": {"latest_timestamp": stamp.timestamp()}}, now=now) == "closed"
    # Alerting on a reference quote does not make it a verified close or a live valuation.
    limit = 7 * 86400 if (fund or closed) and status != "delayed" else 20 * 60
    return -300 <= (now - stamp).total_seconds() <= limit


def conversion_current(snapshot: dict, currency: str, base: str, now: datetime, *, reference_since: datetime | None = None) -> bool:
    if currency == base:
        return True
    rates = {**(snapshot.get("fx_rates") or {}), "CNY": 1}
    if any(isinstance(rates.get(code), bool) or not isinstance(rates.get(code), (int, float))
           or not math.isfinite(rates[code]) or rates[code] <= 0 for code in (currency, base)):
        return False
    legs = {"CNY": set(), "USD": {"USD_CNY"}, "HKD": {"USD_CNY", "USD_HKD"},
            "KRW": {"USD_CNY", "USD_KRW"}}
    if currency not in legs or base not in legs:
        return False
    details = snapshot.get("fx_details") or {}
    if details:
        # Stored rates are CNY crosses; validate only the legs used by this conversion.
        def usable(quote):
            if quote_usable(quote, now):
                return True
            stamp = _time(quote.get("quote_time"))
            price = quote.get("price")
            return bool(reference_since and quote.get("status") == "delayed" and not quote.get("error")
                        and isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price > 0
                        and stamp and reference_since <= stamp <= now + timedelta(minutes=1))
        return all(usable(details.get(key, {})) for key in legs[currency] | legs[base])
    return bool(snapshot.get("fx_valid", not snapshot.get("errors")))


def evaluate_risk(analysis: dict, snapshot: dict, previous: dict, now: datetime) -> tuple[dict, list[dict]]:
    rows = analysis.get("positions", [])
    quotes = {str(q.get("symbol", "")).upper(): q for q in snapshot.get("quotes", [])}
    source_ok = snapshot.get("status") not in {"waiting", "error", "stale"} and not snapshot.get("restored_from_cache")
    usable = {f"{r['market']}:{r['symbol']}": source_ok and quote_usable(
        quotes.get(str(r["symbol"]).upper(), {}), now, fund=r["market"] == "FUND", market=r["market"],
        allow_delayed=True) for r in rows}
    weight_ok = (bool(rows) and all(usable.values()) and analysis["summary"].get("positions_valuation_complete")
                 and all(conversion_current(snapshot, r.get("currency", "CNY"), analysis.get("base_currency", "CNY"), now)
                         and isinstance(r.get("market_value_base"), (int, float))
                         and math.isfinite(r["market_value_base"]) and r["market_value_base"] > 0 for r in rows))
    states, new_events = {}, []
    for row in rows:
        settings = row.get("risk_settings") or {}
        if not settings.get("enabled"):
            continue
        for kind, label in RULES.items():
            threshold = settings.get(kind)
            if threshold is None:
                continue
            key = f"{row['market']}:{row['symbol']}:{kind}"
            percent_rule = kind in {"take_profit_pct", "stop_loss_pct"}
            fixed_price_rule = kind in FIXED_PRICE_RULES
            price_rule = percent_rule or fixed_price_rule
            cost = row.get("average_cost")
            cost_ok = (not isinstance(cost, bool) and isinstance(cost, (int, float))
                       and math.isfinite(cost) and cost > 0)
            signature = [row.get("created_at"), threshold, row.get("risk_revision")]
            if percent_rule:
                signature.append(cost)
            old = (previous.get("states") or {}).get(key) or {}
            if old.get("signature") != signature:
                old = {}
            value = row.get("price") if price_rule else row.get("weight_pct")
            valid = usable[f"{row['market']}:{row['symbol']}"] if price_rule else weight_ok
            if percent_rule:
                value = (value - cost) / cost * 100 if cost_ok and value is not None else None
            state = {**old, "signature": signature, "kind": kind, "threshold": threshold,
                     "market": row["market"], "symbol": row["symbol"], "value": value,
                     "checked_at": iso_utc(now), "quote_time": row.get("quote_time")}
            if not valid or value is None or not math.isfinite(value):
                reason = ("请先设置有效平均成本" if percent_rule and not cost_ok else
                          "等待有效报价" if price_rule else "等待全部持仓行情及有效汇率")
                state.update(status="paused", reason=reason)
                states[key] = state
                continue
            boundary = -threshold if kind == "stop_loss_pct" else threshold
            upper = kind in {"take_profit_pct", "take_profit_price", "weight_max_pct"}
            breach = value >= boundary if upper else value <= boundary
            # Decimal prices can land a few floating-point bits away from an exact percent boundary.
            if percent_rule and math.isclose(value, boundary, rel_tol=1e-12, abs_tol=1e-12):
                breach = True
            # Hysteresis prevents repeated crossing alerts from tiny price/weight fluctuations.
            margin = threshold * .002 if fixed_price_rule else .2
            recovered = value < boundary - margin if upper else value > boundary + margin
            active = bool(old.get("active"))
            last_alert = _time(old.get("last_alert_at"))
            pending = breach and (not active or old.get("pending_alert", False))
            if pending and (last_alert is None or (now - last_alert).total_seconds() >= 300):
                unit = row.get("currency", "CNY") if fixed_price_rule else "%"
                compare = "≥" if upper else "≤"
                metric = "较平均成本收益" if percent_rule else "价格" if fixed_price_rule else "持仓占比"
                reason = f"{metric} {value:.4f} {unit} {compare} {boundary:g} {unit}"
                quote = quotes.get(str(row['symbol']).upper(), {})
                if quote.get('status') == 'delayed':
                    stamp = _time(quote.get('quote_time')).astimezone(timezone(timedelta(hours=8)))
                    reason += f"；参考报价（可能延迟），{stamp:%m/%d %H:%M:%S} UTC+8"
                event = {"id": f"{key}:{iso_utc(now)}", "market": row["market"], "symbol": row["symbol"],
                         "name": row["name"], "kind": kind, "title": f"{row['name']} · 触及{label}",
                         "reason_summary": reason, "created_at": iso_utc(now), "quote_time": row.get("quote_time"),
                         "price_label": row.get("price_label"), "value": value, "threshold": threshold,
                         "desktop_notify": settings.get("desktop_notify", True), "notification": "off"}
                new_events.append(event)
                state["last_alert_at"] = iso_utc(now)
                pending = False
            state.update(active=breach or (active and not recovered),
                         pending_alert=pending,
                         status="triggered" if breach else "recovering" if active and not recovered else "watching", reason="")
            states[key] = state
    events = list(reversed(new_events)) + list(previous.get("events") or [])
    return {"states": states, "events": events[:100], "checked_at": iso_utc(now)}, new_events
