"""Build the numbers and attributed headlines on page one of a frozen briefing."""
from __future__ import annotations

import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from .briefing import _iso, _number, _time
from .portfolio_alerts import conversion_current
from .portfolio_book import portfolios
from .portfolio_monitor import build_portfolio_analysis


_MAJOR_TYPES = {"earnings", "guidance", "regulation", "legal", "m_and_a", "management", "product"}
_MAJOR_TERMS = re.compile(
    r"董事会|指控|辟谣|监管|财报|利率|关税|制裁|降息|加息|半导体|央行|停火|并购|芯片|"
    r"\b(board|ceo|allegations?|regulat\w+|earnings|guidance|rate cuts?|central bank|tariffs?|"
    r"sanctions?|ceasefire|merger|semiconductor\w*|chipmakers?)\b", re.IGNORECASE)
_GOVERNANCE = re.compile(r"董事会|调查|指控|通报|\b(board|allegations?|investigat\w*|probe|scandal|spy claims)\b", re.IGNORECASE)
_BOARD_ACTION = re.compile(r"董事会|\bboard\b", re.IGNORECASE)
_POLICY_SUBJECT = re.compile(r"关税|贸易休战|利率|出口管制|制裁|通胀|\b(tariffs?|trade truce|rates?|export controls?|sanctions?|inflation)\b", re.IGNORECASE)
_POLICY_ACTION = re.compile(r"延长|延续|暂停|上调|下调|取消|生效|公布|\b(extends?|suspends?|imposes?|cuts?|raises?|removes?|announces?|takes effect)\b", re.IGNORECASE)
_NAME_PAIRS = re.compile(r"(?=(\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b))")
_GENERAL_NAMES = {"Stock Market", "Wall Street", "United States", "White House", "New York", "Federal Reserve"}
_COMMENTARY = re.compile(r"^(opinion\s*[|:]|观点[｜：])|make you a fortune|bargain shares to (buy|consider)", re.IGNORECASE)


def lead_news(rows, cutoff):
    """Surface recent market-moving topics; every headline remains a reported claim."""
    groups = {}
    since = cutoff - timedelta(hours=36)
    for row in rows:
        published = _time(row.get("published_at"))
        source = row.get("source", "")
        title = row.get("title", "").removesuffix(" - " + source).strip()
        if not published or not since <= published <= cutoff or not title or not row.get("url") or source in {"", "未标明来源"} or _COMMENTARY.search(title):
            continue
        keys = [re.sub(r"[\W_]+", "", title).casefold()]
        governance = bool(_GOVERNANCE.search(title))
        thematic = bool(_MAJOR_TERMS.search(title))
        if row.get("event_type") == "legal" and row.get("signal") == "unknown" and not (governance or thematic):
            continue
        if governance:
            # Different publishers often describe the same board action with different headlines.
            keys.extend("governance:" + name.casefold() for name in set(_NAME_PAIRS.findall(title)) - _GENERAL_NAMES)
        major = governance or thematic or row.get("event_type") in _MAJOR_TYPES
        subject = _POLICY_SUBJECT.search(title)
        policy = bool(subject and _POLICY_ACTION.search(title))
        if policy:
            prefix = re.findall(r"[a-z]+", title.casefold())[:4]
            if len(prefix) == 4:
                keys.append(f"policy:{subject.group().casefold()}:{' '.join(prefix)}")
        tier = 2 if policy or governance and _BOARD_ACTION.search(title) else int(major)
        for key in keys:
            group = groups.setdefault(key, {"rows": [], "sources": set(), "tier": 0})
            group["rows"].append(row)
            group["sources"].add(source.casefold())
            group["tier"] = max(group["tier"], tier)

    def rank(group):
        latest = max(row["published_at"] for row in group["rows"])
        return (group["tier"], min(4, len(group["sources"])), latest)

    result, used = [], set()
    for group in sorted(groups.values(), key=rank, reverse=True):
        keys = {id(row) for row in group["rows"]}
        if keys & used:
            used.update(keys)
            continue
        representative = max(group["rows"], key=lambda row: (row.get("signal") != "unknown",
                            bool(re.search(r"董事会|通报|\b(board|statement)\b", row["title"], re.IGNORECASE)), row["published_at"]))
        result.append(representative)
        used.update(keys)
        if len(result) == 3:
            break
    return result


def selected_books(raw):
    books = portfolios(raw)
    settings = raw.get("briefing") or {}
    ids = settings.get("portfolio_ids") if isinstance(settings, dict) else None
    if isinstance(ids, list):
        by_id = {book["id"]: book for book in books}
        return [by_id[key] for key in dict.fromkeys(key for key in ids if isinstance(key, str)) if key in by_id][:2]
    return sorted(books, key=lambda book: not bool(book.get("positions")))[:2]


def portfolio_moves(books, snapshot, quotes, cutoff):
    closes = {(row["market"], row["symbol"]): row for row in quotes if row["status"] == "ready"}
    return [_portfolio_move(book, snapshot, closes, cutoff) for book in books]


def _portfolio_move(book, snapshot, closes, cutoff):
    positions = {(row["market"], row["symbol"]): row for row in book.get("positions", [])}
    snapshot = {**snapshot, "quotes": [row for row in snapshot.get("quotes", [])
                                      if (stamp := _time(row.get("quote_time"))) and stamp <= cutoff]}
    view = build_portfolio_analysis(list(positions.values()), snapshot, [], [], account=book, now=cutoff)
    currency, total = view["base_currency"], len(positions)
    current = {}
    for row in view["positions"]:
        pnl, value = _number(row["daily_pnl_base"]), _number(row["market_value_base"])
        stamp = _time(row.get("quote_time"))
        if pnl is not None and value is not None and stamp:
            fx_stamp = _fx_reference_time(row["currency"], currency, snapshot.get("fx_details") or {})
            current[(row["market"], row["symbol"])] = (value - pnl, pnl, stamp, fx_stamp)

    missing = [position for key, position in positions.items() if key not in current]
    closed = _completed_moves(missing, snapshot, closes, currency, cutoff) if missing else []
    moves = [*current.values(), *closed]
    covered = len(moves)
    previous = sum(row[0] for row in moves)
    pnl = sum(row[1] for row in moves) if moves else None
    quote_time = _iso(min(row[2] for row in moves)) if moves else None
    fx_quote_time = None
    note = "持仓行情快照 · 各市场按最近有效报价较前收估算"
    if view["summary"]["daily_pnl_prior_session_count"]:
        note += " · 含上一交易日"
    if closed:
        note = (f"混合估算 · 行情 {len(current)} 项，最近收盘 {len(closed)} 项 · 各市场对应前收"
                if current else "各市场最近已完成交易日收盘较前收")
    fx_times = [stamp for _, _, _, stamp in moves if stamp]
    if fx_times:
        oldest_fx = min(fx_times)
        fx_quote_time = _iso(oldest_fx)
        note += f" · 汇率参考 {oldest_fx.astimezone(ZoneInfo('Asia/Shanghai')):%m/%d %H:%M}（北京时间）"

    change = _number(pnl / previous * 100) if pnl is not None and covered == total and previous and previous > 0 else None
    status = "ready" if change is not None else "partial" if covered else "unavailable"
    if not covered:
        note = "暂无可计行情或汇率" if total else "暂无证券持仓"
    return {"id": book["id"], "name": book.get("name") or "未命名组合", "currency": currency, "covered": covered, "total": total,
            "daily_pnl": _number(pnl), "change_pct": change, "quote_time": quote_time, "fx_quote_time": fx_quote_time,
            "status": status, "note": note}


def _fx_reference_time(denomination, currency, details):
    if denomination == currency:
        return None
    legs = {"USD_CNY", f"USD_{denomination}", f"USD_{currency}"} & details.keys()
    return min((_time(details[key]["quote_time"]) for key in legs), default=None)


def _completed_moves(positions, snapshot, closes, currency, cutoff):
    rates = {**(snapshot.get("fx_rates") or {}), "CNY": 1}
    # Fresh delayed FX is adequate for this constant-rate report estimate. Keep
    # the shared snapshot and the live portfolio/risk valuation gate untouched.
    fx_source_usable = snapshot.get("status") not in {"stale", "waiting", "error"} and not snapshot.get("restored_from_cache")
    details = {}
    for key, leg in (snapshot.get("fx_details") or {}).items():
        stamp = _time(leg.get("quote_time"))
        candidate = {**leg, "status": "live"} if leg.get("status") == "delayed" else leg
        details[key] = candidate if stamp and stamp <= cutoff else {**leg, "status": "stale"}
    report_fx = {**snapshot, "fx_details": details}
    moves = []
    for position in positions:
        row = closes.get((position["market"], position["symbol"])) or {}
        denomination = position.get("currency") or "CNY"
        stamp = _time(row.get("as_of"))
        close, prior, quantity = (_number(value) for value in (row.get("close"), row.get("previous_close"), position.get("quantity")))
        if not stamp or stamp > cutoff or row.get("currency") != denomination or not all(x is not None and x > 0 for x in (close, prior, quantity)):
            continue
        if denomination != currency and (not fx_source_usable or not conversion_current(report_fx, denomination, currency, cutoff)):
            continue
        rate = 1 if denomination == currency else rates[denomination] / rates[currency]
        moves.append((quantity * prior * rate, quantity * (close - prior) * rate, stamp,
                      _fx_reference_time(denomination, currency, details)))
    return moves
