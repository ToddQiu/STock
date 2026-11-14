"""Source-linked volume, turnover, order-flow estimates and sector snapshots."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from statistics import median
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .briefing import _iso, _number, _time
from .market_scanner import MARKET_RULES
from .market_sessions import MARKETS
from .portfolio_chart import fetch_portfolio_chart


_CURRENCIES = {"CN": "CNY", "HK": "HKD", "US": "USD", "KR": "KRW"}
_ACTIVITY = {
    "CN": (("1.000001", "沪市 · 上证指数行情", "000001.SS"), ("0.399001", "深市 · 深证成指行情", "399001.SZ")),
    "HK": (("100.HSI", "恒生指数行情", "^HSI"),),
    "US": (("100.SPX", "标普500指数行情", "^GSPC"),),
    "KR": (("100.KS11", "韩国综合指数行情", "^KS11"),),
}
_EAST_QUOTE = "https://push2.eastmoney.com/api/qt/stock/get"
_EAST_FLOW = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_EAST_BOARDS = "https://push2.eastmoney.com/api/qt/clist/get"
_SCREENS = {"CN": "china", "HK": "hong-kong", "US": "usa", "KR": "south-korea"}
_EAST = "东方财富"
_TV = "TradingView"
_MAX_SAMPLE = 250


def _request(url, params=None, *, payload=None):
    if params:
        url += "?" + urlencode(params)
    headers = {"User-Agent": "Mozilla/5.0 QuantNewsMonitor/0.1", "Accept": "application/json", "Connection": "close"}
    if payload is not None:
        headers.update({"Content-Type": "application/json", "Origin": "https://www.tradingview.com"})
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    with urlopen(request, timeout=5) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("公开行情响应过大")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("公开行情格式不符")
    return result


def _epoch(value):
    value = _number(value)
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _positive(value):
    number = _number(value)
    return number if number is not None and number > 0 else None


def _session_snapshot(moment, session, cutoff):
    return (moment is not None and session.close - timedelta(seconds=15) <= moment <= cutoff
            and moment.astimezone(ZoneInfo(MARKETS[session.market]["timezone"])).date() == session.date
            and session.close + timedelta(minutes=15) <= cutoff)


def _activity_quote(fetch, secid, session, cutoff):
    response = fetch(_EAST_QUOTE, {"secid": secid, "fltt": 2, "fields": "f57,f47,f48,f86"})
    row = response.get("data") or {}
    clock = _epoch(row.get("f86"))
    if response.get("rc") != 0 or row.get("f57") != secid.split(".")[1] or not _session_snapshot(clock, session, cutoff):
        return {}
    return {"volume": _positive(row.get("f47")), "turnover": _positive(row.get("f48")),
            "volume_unit": "手" if session.market == "CN" else "来源口径",
            "source": _EAST, "source_url": "https://quote.eastmoney.com/unify/r/" + secid, "as_of": _iso(clock),
            "note": "指数盘末报价口径。" if clock < session.close else "指数来源口径。"}


def _main_flow(fetch, secid, session, previous, cutoff):
    if cutoff < session.close + timedelta(minutes=15):
        return {}
    response = fetch(_EAST_FLOW, {"secid": secid, "klt": 101, "lmt": 10, "fields1": "f1,f2,f3,f7",
                                  "fields2": "f51,f52,f53,f54,f55,f56"})
    exchange, code = secid.split(".")
    data = response.get("data") or {}
    if response.get("rc") != 0 or data.get("code") != code or str(data.get("market")) != exchange:
        return {}
    day = session.date.isoformat()
    prior_day = previous.date.isoformat() if previous is not None else None
    flow = {}
    for line in data.get("klines") or []:
        if not isinstance(line, str):
            continue
        date, *values = line.split(",")
        if date not in {day, prior_day} or len(values) != 5:
            continue
        main, _, _, large, extra_large = map(_number, values)
        if None not in (main, large, extra_large) and abs(main - large - extra_large) <= max(10000, abs(main) * .001):
            flow[date] = main
    if day not in flow:
        return {}
    return {"main_net_flow": flow[day], "previous_main_net_flow": flow.get(prior_day), "flow_source": _EAST,
            "flow_source_url": f"https://data.eastmoney.com/zjlx/zs{code}.html",
            "flow_note": "来源按大单与超大单分类统计的净额，未识别交易者身份。"}


def _sector_boards(fetch, session, cutoff, descending):
    response = fetch(_EAST_BOARDS, {"pn": 1, "pz": 2, "po": int(descending), "np": 1, "fltt": 2, "fid": "f3",
                                    "fs": "m:90+t:2", "fields": "f3,f12,f14,f124"})
    data = response.get("data") or {}
    total = data.get("total")
    if response.get("rc") != 0 or not isinstance(total, int) or total < 4:
        return []
    result = []
    for index, row in enumerate((data.get("diff") or [])[:2]):
        symbol, name = row.get("f12"), row.get("f14")
        move, clock = _number(row.get("f3")), _epoch(row.get("f124"))
        if not isinstance(symbol, str) or not re.fullmatch(r"BK\d{4,6}", symbol) or not name or move is None or not _session_snapshot(clock, session, cutoff):
            continue
        result.append({"market": "CN", "name": str(name), "symbol": symbol, "session_date": session.date.isoformat(),
                       "change_pct": move, "rank": index + 1 if descending else total - index, "source": _EAST,
                       "source_url": f"https://data.eastmoney.com/bkzj/{symbol}.html", "as_of": _iso(clock),
                       "basis": f"东方财富行业板块共 {total} 项 · {'前列' if descending else '后列'}"})
    return result


def _scan(fetch, market, session, cutoff):
    rule = next(row for row in MARKET_RULES if row.market == market)
    filters = [{"left": key, "operation": op, "right": value} for key, op, value in (
        ("active_symbol", "equal", True), ("exchange", "in_range", list(rule.exchanges)), ("type", "equal", "stock"),
        ("currency", "equal", _CURRENCIES[market]), ("close", "greater", rule.minimum_price),
        ("volume", "greater", rule.minimum_volume), ("market_cap_basic", "greater", rule.minimum_market_cap))]
    response = fetch(f"https://scanner.tradingview.com/{rule.scanner_market}/scan", payload={
        "filter": filters, "symbols": {"query": {"types": []}, "tickers": []},
        "options": {"lang": "zh" if market in {"CN", "HK"} else "en"},
        "columns": ["name", "description", "sector", "exchange", "type", "change", "volume", "time", "update_mode", "currency"],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"}, "range": [0, _MAX_SAMPLE]})
    zone = ZoneInfo(MARKETS[market]["timezone"])
    rows = {}
    for row in (response.get("data") or [])[:_MAX_SAMPLE]:
        values = row.get("d") or []
        if len(values) != 10:
            continue
        symbol, name, sector, exchange, kind, change, volume, stamp, update, currency = values
        scanner_symbol, moment, change, volume = row.get("s"), _epoch(stamp), _number(change), _positive(volume)
        if (not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,23}", symbol)
                or kind != "stock" or currency != _CURRENCIES[market] or exchange not in rule.exchanges
                or scanner_symbol != f"{exchange}:{symbol}" or moment is None or moment > cutoff
                or moment.astimezone(zone).date() != session.date or change is None or volume is None):
            continue
        delayed = re.fullmatch(r"delayed_streaming_(\d{1,5})", str(update))
        delay = max(900, int(delayed[1])) if delayed else 900 if update in {"streaming", "endofday"} else None
        if delay is None or cutoff < session.close + timedelta(seconds=delay):
            continue
        rows[scanner_symbol] = {"market": market, "name": str(name or symbol), "symbol": symbol, "sector": str(sector or ""),
                                "session_date": session.date.isoformat(), "change_pct": change, "volume": volume,
                                "source": _TV, "source_url": f"https://www.tradingview.com/symbols/{exchange}-{symbol}/",
                                "as_of": None, "source_bar_at": _iso(moment)}
    return list(rows.values())


def _sample_rankings(market, rows):
    basis = f"按成交活跃度筛选的 {len(rows)} 只股票样本"
    leaders = [{**row, "basis": basis + " · 涨幅靠前"}
               for row in sorted(rows, key=lambda row: row["change_pct"], reverse=True)[:2] if row["change_pct"] > 0]
    groups = {}
    for row in rows:
        if row["sector"]:
            groups.setdefault(row["sector"], []).append(row)
    sectors = []
    for name, members in groups.items():
        if len(members) < 3:
            continue
        sectors.append({"market": market, "name": name, "symbol": "", "session_date": members[0]["session_date"],
                        "change_pct": median(row["change_pct"] for row in members), "source": _TV,
                        "source_url": f"https://www.tradingview.com/markets/stocks-{_SCREENS[market]}/sectorandindustry-sector/",
                        "as_of": members[0]["as_of"], "basis": f"{basis} · 本行业 {len(members)} 只涨跌中位数"})
    sectors.sort(key=lambda row: row["change_pct"], reverse=True)
    ranked = [{**row, "rank": index} for index, row in enumerate(sectors, 1)]
    return ranked[:2] + ranked[max(2, len(ranked) - 2):], leaders


def _activity_analysis(activity, indexed, report, sessions):
    output = []
    flows = [row for row in activity if row["main_net_flow"] is not None]
    if flows:
        signs = {1 if row["main_net_flow"] > 0 else -1 if row["main_net_flow"] < 0 else 0 for row in flows}
        entries = "，".join(f"{row['name'].split(' · ')[0]} {row['main_net_flow'] / 100_000_000:+.2f} 亿元"
                            for row in flows)
        conclusion = {frozenset({1}): "两市同向净流入。", frozenset({-1}): "两市同向净流出。",
                      frozenset({-1, 1}): "两市净额方向分化。"}.get(frozenset(signs), "") if len(flows) == 2 else ""
        changes = "，".join(f"{row['name'].split(' · ')[0]} {(row['main_net_flow'] - row['previous_main_net_flow']) / 100_000_000:+.2f} 亿元"
                            for row in flows if row.get("previous_main_net_flow") is not None)
        comparison = f"较前一交易日净额变化为{changes}。" if changes else ""
        output.append({"text": f"{flows[0]['session_date']}，东方财富统计的大单与超大单净额为{entries}。{conclusion}{comparison}", "refs": []})
    refs = {(row.get("market"), row.get("symbol")): row.get("ref")
            for row in (report.get("front_page") or {}).get("markets", [])}
    for market, (session, _) in sessions.items():
        day = session.date.isoformat()
        for _, _, symbol in _ACTIVITY[market]:
            quote = indexed.get((market, symbol), {})
            volume, previous = _positive(quote.get("volume")), _positive(quote.get("previous_volume"))
            if quote.get("status") != "ready" or quote.get("session_date") != day or volume is None or previous is None:
                continue
            change = _number((volume / previous - 1) * 100)
            if change is None:
                continue
            source = quote.get("source") or "日线来源"
            name = quote.get("name") or symbol
            ref = refs.get((market, symbol))
            output.append({"text": f"{day} {name}按{source}同口径日线计算，成交量较前一交易日变化 {change:+.2f}%。",
                           "refs": [ref] if ref else []})
    return output


def build_market_detail(monitor, report, sessions, *, fetch=None):
    """Build a bounded snapshot using only each report market's completed session."""
    cutoff = _time((report.get("window") or {}).get("end")) or monitor.now()
    if fetch is None and getattr(monitor, "chart_fetcher", None) is fetch_portfolio_chart:
        fetch = _request
    indexed = {(row.get("market"), row.get("symbol")): row for row in report.get("quotes", []) if row.get("role") == "index"}
    activity, sectors, leaders = [], [], []
    limitations = ["成交量和成交额保留各自来源口径，不合并不同指数或市场。",
                   "海外行业排行及各市场领涨股基于 TradingView 成交活跃样本，行业排序按样本日涨跌中位数。",
                   "主力资金仅使用具有对应交易日的东方财富沪市、深市大单和超大单净额口径。"]
    jobs, results = {}, {}
    if fetch is not None and sessions:
        pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="briefing-market-detail")
        for market, (session, previous) in sessions.items():
            jobs[pool.submit(_scan, fetch, market, session, cutoff)] = (market, "scan")
            for secid, _, _ in _ACTIVITY[market]:
                jobs[pool.submit(_activity_quote, fetch, secid, session, cutoff)] = (market, secid)
                if market == "CN":
                    jobs[pool.submit(_main_flow, fetch, secid, session, previous, cutoff)] = (market, secid, "flow")
            if market == "CN":
                for descending in (True, False):
                    jobs[pool.submit(_sector_boards, fetch, session, cutoff, descending)] = (market, "sectors", descending)
        try:
            for future in as_completed(jobs, timeout=15):
                try:
                    results[jobs[future]] = future.result()
                except (OSError, HTTPException, ValueError, TypeError, KeyError, AttributeError):
                    pass
        except TimeoutError:
            pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    for market in _ACTIVITY:
        session = sessions[market][0] if market in sessions else None
        day = session.date.isoformat() if session is not None else None
        for secid, name, symbol in _ACTIVITY[market]:
            source = results.get((market, secid)) or {}
            quote = indexed.get((market, symbol), {})
            if session and not source and quote.get("session_date") == day and quote.get("status") in {"partial", "ready"}:
                volume, previous = _positive(quote.get("volume")), _positive(quote.get("previous_volume"))
                if volume is not None:
                    source = {"volume": volume, "volume_unit": "来源口径", "source": quote.get("source"),
                              "source_url": quote.get("source_url"), "as_of": quote.get("as_of"),
                              "volume_change_pct": _number((volume / previous - 1) * 100) if previous else None, "note": "指数日线来源口径。"}
            activity.append({"market": market, "name": name, "session_date": day, "volume": None, "volume_unit": "",
                             "turnover": None, "currency": _CURRENCIES[market], "main_net_flow": None, "source": "", "source_url": "",
                             "as_of": None, "note": "未取得与交易日匹配的量能或成交额。", **source,
                             **(results.get((market, secid, "flow")) or {})})
        sample, leading = _sample_rankings(market, results.get((market, "scan")) or [])
        leading_boards = [*(results.get((market, "sectors", True)) or []), *(results.get((market, "sectors", False)) or [])]
        sectors.extend(sorted(leading_boards, key=lambda row: row["rank"]) or sample)
        leaders.extend(leading)
        if not leading_boards and not sample:
            limitations.append(f"{MARKETS[market]['name']}未取得该交易日可核验的板块排行。")
        if not leading:
            limitations.append(f"{MARKETS[market]['name']}未取得该交易日符合筛选口径的领涨股。")
    return {"activity": activity, "sectors": sectors, "leaders": leaders, "limitations": limitations,
            "analysis": _activity_analysis(activity, indexed, report, sessions)}
