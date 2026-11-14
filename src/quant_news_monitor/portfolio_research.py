"""On-demand portfolio research, using the existing append-only research ledger."""
from __future__ import annotations

import copy
import json
import math
import re
import threading
import uuid
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen

from .security_master import catalog, normalize, cached_identity, preferred_name
from .market_sessions import CalendarUnavailable, SessionCalendar

_PERFORMANCE_CALENDAR = SessionCalendar()

METRIC_COLUMNS = ["name", "description", "sector", "industry", "market_cap_basic", "price_earnings_ttm", "dividends_yield_current", "debt_to_equity_fq", "net_income_ttm", "earnings_release_next_date", "earnings_per_share_forecast_next_fq",
                  "price_book_fq", "price_sales_current", "return_on_equity", "net_margin_ttm", "total_revenue_yoy_growth_ttm", "free_cash_flow_margin_ttm", "currency", "type", "typespecs", "is_primary", "dividend_payout_ratio_ttm"]


def fetch_consensus(target):
    market, symbol = normalize_target(target)
    if market != "US":
        raise ValueError("自动年度一致预期目前覆盖美股，其他市场不套用美股数据")
    request = Request(f"https://api.nasdaq.com/api/analyst/{symbol}/earnings-forecast", headers={
        "User-Agent": "Mozilla/5.0", "Accept": "application/json", "Origin": "https://www.nasdaq.com"})
    with urlopen(request, timeout=8) as response:
        body = response.read(100001)
    if len(body) > 100000:
        raise ValueError("一致预期响应过大")
    payload = json.loads(body)
    data = payload.get("data") if isinstance(payload, dict) else None
    annual = data.get("yearlyForecast") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or str(data.get("symbol", "")).upper() != symbol
            or not isinstance(annual, dict) or not isinstance(annual.get("rows"), list)
            or not 1 <= len(annual["rows"]) <= 8
            or not all(isinstance(r, dict) for r in annual["rows"])):
        raise ValueError("未取得匹配标的的年度一致预期，保留旧快照")
    def valid_row(row):
        value = row.get("consensusEPSForecast")
        if isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip()):
            value = float(value)
        return (bool(re.fullmatch(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) 20\d{2}", str(row.get("fiscalEnd", ""))))
                and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))
    if not any(valid_row(row) for row in annual["rows"]):
        raise ValueError("年度一致预期没有有效期间与EPS，保留旧快照")
    return {"target": target, "annual": annual, "observed_at": datetime.now(timezone.utc).isoformat(),
            "source": "Nasdaq", "source_url": f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/earnings"}


def normalize_target(value):
    parts = str(value).split(":", 1)
    if len(parts) != 2:
        raise ValueError("请输入市场:代码，例如 US:AAPL")
    return normalize(*parts)


def fetch_performance_history(target, year, *, now=None, recent=False):
    """A selected calendar year plus its entry close, preserving provider adjustment."""
    now = now or datetime.now(timezone.utc)
    market, symbol = normalize_target(target)
    if not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= now.year:
        raise ValueError("年份须在2000年至当前年之间")
    start = now - timedelta(days=270) if recent else datetime(year - 1, 12, 15, tzinfo=timezone.utc)
    end = min(datetime(year + 1, 1, 1, tzinfo=timezone.utc), now + timedelta(days=1))
    from .portfolio_chart import _request_json, YAHOO_CHART_ENDPOINT
    if market == "FUND":
        # The fund adapter exposes recent confirmed NAV, not an invented backfilled series.
        from .portfolio_chart import fetch_portfolio_chart
        chart = fetch_portfolio_chart(market, symbol, "1y", mode="daily", timeout_seconds=10)
        points = [{"time": row.get("datetime") or row.get("date"), "close": row["close"],
                   "adjusted_close": row.get("adjusted_close")} for row in chart.get("candles", [])]
        return {"points": points, "currency": chart.get("currency"), "source": chart.get("source"),
                "source_url": chart.get("source_url"), "observed_at": now.isoformat(), "year": year}
    url = YAHOO_CHART_ENDPOINT + "/" + urllib.parse.quote(symbol, safe="") + "?" + urllib.parse.urlencode({
        "interval": "1d", "period1": int(start.timestamp()), "period2": int(end.timestamp()),
        "events": "div,splits", "includePrePost": "false"})
    payload = _request_json(url, timeout_seconds=10)
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(str(chart["error"].get("description") or "年度行情暂不可用"))
    result = (chart.get("result") or [None])[0]
    if not isinstance(result, dict):
        raise ValueError("没有取得所选年度行情")
    meta = result.get("meta") or {}
    currency = {"US":"USD", "CN":"CNY", "HK":"HKD", "KR":"KRW"}.get(market)
    if str(meta.get("symbol", "")).upper() != symbol or meta.get("currency") != currency or meta.get("dataGranularity") != "1d":
        raise ValueError("年度行情的证券、币种或周期与请求不一致")
    zone = ZoneInfo({"US":"America/New_York", "CN":"Asia/Shanghai", "HK":"Asia/Hong_Kong", "KR":"Asia/Seoul"}[market])
    local_now = now.astimezone(zone)
    quote_stamp = meta.get("regularMarketTime")
    quote_time = datetime.fromtimestamp(quote_stamp, timezone.utc).astimezone(zone) if isinstance(quote_stamp, (int, float)) and not isinstance(quote_stamp, bool) and math.isfinite(quote_stamp) and 0 < quote_stamp <= now.timestamp() else None
    completed = None
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    adjusted = ((((result.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or [])
    points = []
    for index, stamp in enumerate(result.get("timestamp") or []):
        if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not math.isfinite(stamp):
            continue
        when = datetime.fromtimestamp(stamp, timezone.utc).astimezone(zone)
        if not start.date() <= when.date() <= local_now.date() or when.year > year:
            continue
        if when.date() == local_now.date():
            if completed is None:
                try:
                    session = _PERFORMANCE_CALENDAR.session(market, local_now.date())
                    completed = (session is not None and now >= session.close + timedelta(minutes=15)
                                 and quote_time is not None and quote_time.date() == local_now.date()
                                 and quote_time >= session.close)
                except CalendarUnavailable:
                    completed = False
            if not completed:
                continue
        close = closes[index] if index < len(closes) else None
        adjusted_close = adjusted[index] if index < len(adjusted) else None
        valid = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
        if not valid(close):
            continue
        points.append({"time": when.date().isoformat(), "close": close,
                       "adjusted_close": adjusted_close if valid(adjusted_close) else None})
    if not points:
        raise ValueError("没有已完成且有效的年度日线")
    return {"points": points, "currency": currency, "source": "Yahoo Finance · 来源复权收盘",
            "source_url": "https://finance.yahoo.com/quote/" + urllib.parse.quote(symbol, safe="") + "/history/",
            "observed_at": now.isoformat(), "source_quote_at": quote_time.isoformat() if quote_time else None, "year": year}


def _export_fund_disclosure(target, now):
    from .instrument_fundamentals import fund_evidence

    try:
        rows = fund_evidence(target.split(":", 1)[1], now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat())
    except (OSError, RuntimeError, ValueError):
        return {}, {}
    product, disclosure = {}, {}
    for row in rows:
        if row.get("investment_focus"):
            product = {"investment_focus": row["investment_focus"], "product_source": row["source"],
                       "product_source_url": row["url"], "product_as_of": now.isoformat()}
        if row.get("holdings"):
            holdings = []
            for position in row["holdings"]:
                code = position["symbol"]
                key = ":".join(normalize("CN" if len(code) == 6 else "HK", code)) if code and int(code) else ""
                holdings.append({**position, "key": key, "type": "stock"})
            disclosure = {"holdings": holdings, "as_of": row["as_of"], "source": row["source"], "source_url": row["url"], "partial": True}
    return product, disclosure


def sample_attention(posts, target, now):
    """Observed share of a disclosed sample, not platform-wide popularity."""
    cutoff = datetime.fromisoformat(now) - timedelta(days=1)
    groups, seen, matched = {}, set(), set()
    for post in posts:
        published = post.get("published_at") or post.get("as_of")
        try:
            when = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            if when.tzinfo is None or not cutoff <= when <= datetime.fromisoformat(now):
                continue
        except (ValueError, TypeError):
            continue
        platform = post.get("platform") or "新闻"
        platform = {"xiaohongshu": "小红书", "douyin": "抖音", "x": "X", "threads": "Threads"}.get(platform, platform)
        fingerprint = (platform, re.sub(r"\s+", "", str(post.get("title") or post.get("excerpt") or post.get("url") or post.get("id") or "")).casefold())
        if not fingerprint[1]:
            continue
        row = groups.setdefault(platform, {"target": target, "platform": platform, "date": now[:10], "count": 0, "total": 0,
            "source": "STock 已入库公开样本", "source_url": post.get("url"), "coverage": "近24小时已入库新闻/社交各最近200条中的去重样本，非全平台热度", "observed_at": now})
        if fingerprint not in seen:
            row["total"] += 1
            seen.add(fingerprint)
        if fingerprint not in matched and (post.get("target") == target or target in post.get("targets", []) or f"{post.get('market')}:{post.get('symbol')}" == target):
            row["count"] += 1
            matched.add(fingerprint)
    return list(groups.values())


def news_events(posts, target, now):
    result = []
    start = datetime.fromisoformat(now)
    for post in posts:
        targets = set(post.get("targets", [])) | {post.get("target")}
        targets.update(a.get("target") for a in post.get("assessments", []))
        if post.get("market") and post.get("symbol"):
            targets.add(f"{post['market']}:{post['symbol']}")
        targets.discard(None)
        if targets != {target} or not str(post.get("url", "")).startswith("https://"):
            continue
        try:
            published = datetime.fromisoformat(str(post.get("published_at") or post.get("as_of")).replace("Z", "+00:00"))
            observed = datetime.fromisoformat(str(post.get("observed_at") or post.get("collected_at")).replace("Z", "+00:00"))
            published = published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published
            observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed
            if not start-timedelta(days=30) <= published <= start or observed > start:
                continue
        except (ValueError, TypeError):
            continue
        text = str(post.get("title", "")) + " " + str(post.get("summary", ""))
        for sentence in re.split(r"[。！？!?;；\n]", text):
            if not re.search(r"会议|峰会|财报|发布|政策|conference|summit|earnings|launch", sentence, re.I) or re.search(r"取消|延期|推迟|未定|否认|cancel|postpon|denied|not scheduled", sentence, re.I):
                continue
            for match in re.finditer(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?", sentence):
                try:
                    date = datetime(*map(int, match.groups()), tzinfo=timezone.utc)
                except ValueError:
                    continue
                if start.date() <= date.date() <= (start + timedelta(days=14)).date():
                    result.append({"target": target, "title": post.get("title"), "date": date.date().isoformat(), "status": "estimated",
                        "source": "新闻日期线索，待核对最终公告", "source_url": post.get("url"), "observed_at": observed.isoformat(),
                        "published_at": published.isoformat(), "type": "news_lead", "event_id": "news:"+post["url"]+"|"+date.date().isoformat()})
    return list({event_key(e): e for e in result}.values())


def event_key(event):
    return json.dumps([event.get("target"), event.get("event_id") or "", "" if event.get("event_id") else event.get("title"),
                       "" if event.get("event_id") else event.get("source_url") or ""], ensure_ascii=False, separators=(",", ":"))


def remember_events(events, previous):
    old = {event_key(e): e for e in previous}
    for event in events:
        prior = old.get(event_key(event), {})
        event["first_observed_at"] = (prior.get("first_observed_at") or prior.get("observed_at")) if prior and prior.get("date") == event.get("date") and prior.get("status") == event.get("status") else event.get("observed_at")
    return events


def fetch_peers(identities, market="US", industry=None):
    regions = {"US": "america", "CN": "china", "HK": "hongkong", "KR": "korea"}
    currencies = {"US": "USD", "CN": "CNY", "HK": "HKD", "KR": "KRW"}
    if market not in regions:
        return []
    tickers = [row.get("provider_ticker") or row["exchange"] + ":" + row["symbol"] for row in identities if row.get("exchange") or row.get("provider_ticker")]
    if not tickers and not industry:
        return []
    body = {"symbols": {"tickers": tickers, "query": {"types": []}}, "columns": METRIC_COLUMNS}
    if industry:
        body = {"filter": [{"left": "industry", "operation": "equal", "right": industry}, {"left": "type", "operation": "equal", "right": "stock"},
                           {"left": "typespecs", "operation": "has", "right": ["common"]}, {"left": "is_primary", "operation": "equal", "right": True}],
                "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"}, "range": [0, 16], "columns": METRIC_COLUMNS}
    body['options'] = {'lang': 'zh' if market == 'CN' else 'en'}
    request = Request(f"https://scanner.tradingview.com/{regions[market]}/scan", data=json.dumps(body).encode(), headers={
        "User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Origin": "https://www.tradingview.com"})
    with urlopen(request, timeout=8) as response:
        data = response.read(200001)
    if len(data) > 200000:
        raise ValueError("同行响应过大")
    payload = json.loads(data)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("同行来源响应无效")
    now = datetime.now(timezone.utc).isoformat()
    hk_names = {}
    if market == 'HK' and payload['data']:
        from .market_scanner import _hk_company_names
        hk_names = _hk_company_names(int(datetime.now(timezone.utc).timestamp()) // 86400, 8)
    result = []
    for row in payload.get("data", []):
        if not isinstance(row, dict) or not isinstance(row.get("s"), str) or ":" not in row["s"] or not isinstance(row.get("d"), list):
            continue
        if (not industry and row.get("s") not in tickers) or len(row["d"]) != len(METRIC_COLUMNS):
            continue
        fields = dict(zip(METRIC_COLUMNS, row["d"]))
        if fields["currency"] != currencies[market]:
            continue
        common = fields["type"] == "stock" and isinstance(fields["typespecs"], list) and "common" in fields["typespecs"]
        if industry and (not common or fields["is_primary"] is not True):
            continue
        exchange, symbol = row["s"].split(":", 1)
        if fields["name"] != symbol or (industry and fields["industry"] != industry):
            continue
        if market == "CN":
            if exchange not in {"SSE", "SZSE", "BSE"}:
                continue
            symbol += {"SSE": ".SS", "SZSE": ".SZ", "BSE": ".BJ"}[exchange]
        elif market == "HK" and exchange != "HKEX" or market == "KR" and exchange != "KRX":
            continue
        try:
            _, symbol = normalize(market, symbol)
        except ValueError:
            continue
        known = cached_identity(market, symbol) or {}
        name = preferred_name(market, hk_names.get(str(int(symbol.split('.')[0]))) if market == 'HK' else None,
                              fields['description'], known.get('name'))
        item = {"target": market + ":" + symbol, "symbol": symbol, "market": market, "name": name,
                "sector": fields["sector"], "industry": fields["industry"], "currency": fields["currency"],
                "asset_type": "stock" if common else "fund" if fields["type"] == "fund" else "other", "source": "TradingView", "observed_at": now,
                "source_url": "https://www.tradingview.com/symbols/" + row["s"].replace(":", "-") + "/financials-overview/"}
        for dest, source, basis, currency in [
            ("market_cap", "market_cap_basic", "来源当前总市值", currencies[market]),
            ("pe", "price_earnings_ttm", "来源TTM市盈率", "ratio"),
            ("dividend_yield", "dividends_yield_current", "来源当前股息率(%)，非派息率", "percent"),
            ("payout_ratio", "dividend_payout_ratio_ttm", "来源TTM派息率(%)：已宣告每股股息/同期EPS，含特别股息", "percent"),
            ("leverage", "debt_to_equity_fq", "来源最近季度债务/权益", "ratio"),
            ("net_income", "net_income_ttm", "来源TTM净利润，报告币种未核验", "unverified"),
            ("pb", "price_book_fq", "来源最近季度市净率", "ratio"),
            ("ps", "price_sales_current", "来源当前市销率", "ratio"),
            ("roe", "return_on_equity", "来源最新ROE(%)", "percent"),
            ("net_margin", "net_margin_ttm", "来源TTM净利率(%)", "percent"),
            ("revenue_growth", "total_revenue_yoy_growth_ttm", "来源TTM收入同比增长(%)", "percent"),
            ("fcf_margin", "free_cash_flow_margin_ttm", "来源TTM自由现金流率(%)", "percent"),
        ]:
            value = fields[source]
            item[dest] = {"value": value, "basis": basis, "currency": currency, "as_of": now, "date_kind": "provider_snapshot", "provider_field": source} if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
        result.append(item)
        stamp = fields.get("earnings_release_next_date")
        if isinstance(stamp, (int, float)) and math.isfinite(stamp) and 0 < stamp < 4102444800:
            item["earnings_date"] = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
            value = fields.get("earnings_per_share_forecast_next_fq")
            item["eps_forecast"] = value if isinstance(value, (int, float)) and math.isfinite(value) else None
            item["eps_basis"] = "下一财季EPS，具体财季及会计口径未取得"
    return result


class PortfolioResearch:
    def __init__(self, app, portfolio_id="default"):
        self.app, self.ledger = app, app.research_ledger
        self.portfolio_id = portfolio_id
        self.lock = threading.RLock()
        self.busy, self.thread, self.error = False, None, None
        self.performance_busy, self.performance_thread = False, None
        rows = self.ledger.inputs(self.kind("portfolio_research"), limit=1)["items"]
        self.data = rows[0]["payload"] if rows else {"histories": {}, "events": [], "peers": [], "posts": [], "attention": [], "errors": []}
        performance = self.ledger.inputs(self.kind("portfolio_performance"), limit=1)["items"]
        self.performance = performance[0]["payload"] if performance else {"histories": {}, "errors": []}

    def kind(self, name):
        return name if self.portfolio_id == "default" else name + ":" + self.portfolio_id

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.data)
            data.update(busy=self.busy, error=self.error)
            data["performance"] = {**copy.deepcopy(self.performance), "busy": self.performance_busy}
        data["portfolio_id"] = self.portfolio_id
        data["imports"] = [r["payload"] for r in self.ledger.inputs(self.kind("portfolio_research_import"), limit=50)["items"]]
        data["records"] = [r["payload"] for r in self.ledger.inputs(self.kind("portfolio_event_record"), limit=200)["items"]]
        sampler = getattr(getattr(self.app, "social_monitor", None), "attention_sampler", None)
        if sampler:
            live = sampler.snapshot(data.get("target", "US:AAPL"))
            platforms = {r["platform"] for r in live["rows"]}
            data["attention"] = [r for r in data.get("attention", []) if r.get("platform") not in platforms] + live.pop("rows")
            data["attention_sampling"] = live
        return data

    def refresh_performance(self, year):
        if not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= datetime.now(timezone.utc).year:
            raise ValueError("年份须在2000年至当前年之间")
        with self.lock:
            if self.performance_busy:
                return False
            self.performance_busy = True
            self.performance_thread = threading.Thread(target=self._collect_performance, args=(year,), daemon=True, name="portfolio-performance")
            self.performance_thread.start()
        return True

    def prepare_analysis_export(self, year):
        """Collect the selected year and the latest rolling window without changing saved research."""
        from .etf_research import latest_holdings
        from .portfolio_book import get_portfolio

        now = datetime.now(timezone.utc)
        if not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= now.year:
            raise ValueError("年份须在2000年至当前年之间")
        account = get_portfolio(self.app.config_store.read(), self.portfolio_id)
        positions = {":".join(normalize(row["market"], row["symbol"])): row for row in account.get("positions", [])}
        targets = list(positions)
        if len(targets) > 60:
            raise ValueError("组合分析最多支持60个标的")
        with self.lock:
            annual_cache = self.performance.get("histories", {}) if self.performance.get("year") == year else {}
            rolling_cache = self.data.get("histories", {})
            peers = self.data.get("peers", [])
        saved_funds = latest_holdings(self.ledger)["payload"]["funds"]
        funds = {target: saved_funds.get(target) or saved_funds.get(row["symbol"])
                 or (saved_funds.get(row["symbol"].split(".")[0]) if row["market"] in {"CN", "FUND"} else None)
                 for target, row in positions.items()}

        def collect(target):
            def read(request_year, cached, *, recent=False):
                try:
                    return fetch_performance_history(target, request_year, now=now, recent=recent), None
                except (OSError, RuntimeError, ValueError, KeyError, TypeError, OverflowError) as exc:
                    old = cached.get(target)
                    return old, f"{target}：{exc}" + ("，本轮沿用上次可用记录" if old else "")

            annual, annual_error = read(year, annual_cache)
            tail = (annual or {}).get("points", [])[-160:]
            if year == now.year and not annual_error and len(tail) >= 121 and all(row.get("adjusted_close") for row in tail):
                short, short_error = {**annual, "points": tail}, None
            else:
                short, short_error = read(now.year, rolling_cache, recent=True)
            position = positions[target]
            is_fund = position["market"] == "FUND" or str(position.get("asset_type") or "").lower() in {"fund", "etf", "etn"}
            is_fund = is_fund or (position["market"] == "CN" and position["symbol"].startswith(("15", "50", "51", "52", "56", "58")))
            product, disclosure = {}, funds[target]
            if is_fund and position["market"] in {"CN", "FUND"} and not (disclosure or {}).get("holdings"):
                product, disclosure = _export_fund_disclosure(target, now)
            return annual, annual_error, short, short_error, product, disclosure

        observed = now.isoformat()
        performance = {"year": year, "observed_at": observed, "histories": {}, "errors": []}
        histories, errors, products, disclosures = {}, [], {}, {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            for target, (annual, annual_error, short, short_error, product, disclosure) in zip(targets, executor.map(collect, targets)):
                if annual:
                    performance["histories"][target] = annual
                if annual_error:
                    performance["errors"].append(annual_error)
                histories[target] = short or {"points": []}
                if short_error:
                    errors.append(short_error)
                if product:
                    products[target] = product
                if disclosure:
                    disclosures[target] = disclosure
        imports = self.ledger.inputs(self.kind("portfolio_research_import"), limit=50)["items"]
        return {"portfolio_id": self.portfolio_id, "performance": performance, "histories": histories,
                "observed_at": observed, "errors": errors, "peers": [*peers, *(peer for row in imports for peer in row["payload"].get("peers", []))],
                "funds": disclosures, "products": products}

    def _collect_performance(self, year):
        try:
            from .portfolio_book import get_portfolio
            positions = get_portfolio(self.app.config_store.read(), self.portfolio_id).get("positions", [])
            targets = list(dict.fromkeys(":".join(normalize(row["market"], row["symbol"])) for row in positions))
            if len(targets) > 60:
                raise ValueError("年度组合分析最多60个标的；未截断持仓")
            now = datetime.now(timezone.utc)
            result = {"year": year, "observed_at": now.isoformat(), "targets": targets, "histories": {}, "errors": []}
            with ThreadPoolExecutor(max_workers=4) as executor:
                jobs = {executor.submit(fetch_performance_history, target, year, now=now): target for target in targets}
                for job in as_completed(jobs):
                    target = jobs[job]
                    try:
                        result["histories"][target] = job.result()
                    except (ValueError, OSError, RuntimeError, KeyError, TypeError, OverflowError) as exc:
                        result["errors"].append(target + "：" + str(exc))
            self.ledger.observe(self.kind("portfolio_performance"), str(year), result)
            with self.lock:
                self.performance = result
        except Exception as exc:
            with self.lock:
                self.performance = {"year": year, "histories": {}, "errors": [str(exc)], "observed_at": datetime.now(timezone.utc).isoformat()}
        finally:
            with self.lock:
                self.performance_busy = False

    def refresh(self, target):
        market, symbol = normalize_target(target)
        target = market + ":" + symbol
        with self.lock:
            if self.busy:
                return False
            self.busy, self.error = True, None
            self.thread = threading.Thread(target=self._collect, args=(target,), daemon=True, name="portfolio-research")
            self.thread.start()
        return True

    def _collect(self, target):
        try:
            now = datetime.now(timezone.utc).isoformat()
            raw = self.app.config_store.read()
            from .portfolio_book import get_portfolio
            account = get_portfolio(raw, self.portfolio_id)
            positions = account.get("positions", [])
            benchmark = account.get("benchmark") or {"US":"US:SPY", "CN":"CN:510300.SS", "HK":"HK:2800.HK", "KR":"KR:069500.KS"}.get(target.split(":")[0], "US:SPY")
            targets = list(dict.fromkeys([target] + [":".join(normalize(r["market"], r["symbol"])) for r in positions]))
            if len(targets) > 60:
                raise ValueError("单次组合历史分析最多60个标的；当前持仓超过上限，未截断组合结果")
            directory = self.app.earnings_calendar.query(days="all", scope="all", include_unknown=True, limit=10000)
            rows = directory["items"] or catalog()
            own = next((r for r in rows if "US:" + r["symbol"] == target), None)
            errors = []
            try:
                market, symbol = target.split(":", 1)
                if market == "US":
                    identity = own or cached_identity(market, symbol)
                    primary = fetch_peers([identity]) if identity and identity.get("exchange") else []
                    metrics = primary
                    if primary and primary[0].get("asset_type") == "stock" and primary[0].get("industry"):
                        try:
                            metrics += [r for r in fetch_peers([], market, primary[0]["industry"]) if r["target"] != target]
                        except (ValueError, OSError) as exc:
                            errors.append("同行候选未更新，保留当前公司资料：" + str(exc))
                elif market in {"CN", "HK", "KR"}:
                    exchange = {"CN": {"SS":"SSE", "SZ":"SZSE", "BJ":"BSE"}.get(symbol[-2:]), "HK":"HKEX", "KR":"KRX"}[market]
                    provider_symbol = str(int(symbol.split(".")[0])) if market == "HK" else symbol.split(".")[0]
                    primary = fetch_peers([{"provider_ticker": exchange + ":" + provider_symbol}], market)
                    metrics = primary
                    if primary and primary[0].get("asset_type") == "stock" and primary[0].get("industry"):
                        try:
                            metrics += [r for r in fetch_peers([], market, primary[0]["industry"]) if r["target"] != target]
                        except (ValueError, OSError) as exc:
                            errors.append("同行候选未更新，保留当前公司资料：" + str(exc))
                else:
                    metrics = []
            except (ValueError, OSError) as exc:
                metrics = []
                errors.append("同行指标未更新：" + str(exc))
            # Batch the remaining holdings by market so sector coverage is not just the selected company.
            collected = {r["target"] for r in metrics}
            for held_market in ("US", "CN", "HK", "KR"):
                identities = []
                for identifier in targets:
                    if identifier in collected or not identifier.startswith(held_market + ":"):
                        continue
                    _, held_symbol = identifier.split(":", 1)
                    if held_market == "US":
                        identity = next((r for r in rows if r["symbol"] == held_symbol and r.get("exchange")), None)
                        if identity:
                            identities.append(identity)
                    else:
                        exchange = {"CN": {"SS":"SSE", "SZ":"SZSE", "BJ":"BSE"}.get(held_symbol[-2:]), "HK":"HKEX", "KR":"KRX"}[held_market]
                        code = str(int(held_symbol.split(".")[0])) if held_market == "HK" else held_symbol.split(".")[0]
                        if exchange:
                            identities.append({"provider_ticker": exchange + ":" + code})
                if identities:
                    try:
                        metrics.extend(fetch_peers(identities, held_market))
                    except (ValueError, OSError) as exc:
                        errors.append(held_market + " 持仓行业资料未更新：" + str(exc))
            fresh_targets = [r["target"] for r in metrics]
            merged = {r["target"]: r for r in self.data.get("peers", [])}
            merged.update({r["target"]: r for r in metrics})
            metrics = sorted(merged.values(), key=lambda r: r.get("observed_at", ""))[-120:]
            consensus = copy.deepcopy(self.data.get("consensus", {}))
            current = next((r for r in metrics if r["target"] == target), {})
            if target.startswith("US:") and current.get("asset_type") not in {"fund", "etf", "etn"}:
                try:
                    consensus[target] = fetch_consensus(target)
                except (ValueError, OSError) as exc:
                    errors.append("年度一致预期未更新，保留旧快照：" + str(exc))
            consensus = dict(sorted(consensus.items(), key=lambda r: r[1].get("observed_at", ""))[-60:])
            targets = list(dict.fromkeys(targets + fresh_targets + [benchmark]))
            records = self.snapshot()["records"]
            review_targets = list(dict.fromkeys(r.get(key) for r in records for key in ("target", "benchmark") if r.get(key)))
            missing_targets = [identifier for identifier in review_targets if identifier not in targets]
            if len(missing_targets) > 40:
                errors.append("历史复盘额外标的超过40个，本轮仅更新最近记录涉及的40个，其余保留旧行情")
            targets += missing_targets[:40]
            histories = copy.deepcopy(self.data.get("histories", {}))
            from .portfolio_chart import fetch_portfolio_chart
            def fetch(identifier):
                market, symbol = identifier.split(":", 1)
                # shortcut: six-month public history; archive settled results before offering multi-year review.
                chart = fetch_portfolio_chart(market, symbol, "6m", mode="daily", timeout_seconds=8)
                # Today's still-forming daily candle is not an already completed return.
                today = datetime.now(timezone.utc).date().isoformat()
                points = [{"time": row.get("datetime") or row.get("date"), "close": row["close"], "adjusted_close": row.get("adjusted_close"), "volume": row.get("volume")}
                          for row in chart.get("candles", []) if str(row.get("date") or row.get("datetime", ""))[:10] < today]
                return {"points": points[-150:], "currency": chart.get("currency"), "source": chart.get("source"), "source_url": chart.get("source_url"), "observed_at": now}
            with ThreadPoolExecutor(max_workers=4) as executor:
                jobs = {executor.submit(fetch, identifier): identifier for identifier in targets}
                for job in as_completed(jobs):
                    identifier = jobs[job]
                    try:
                        histories[identifier] = job.result()
                    except (ValueError, OSError, RuntimeError) as exc:
                        errors.append(identifier + " 历史未更新：" + str(exc))
            histories = {k: v for k, v in histories.items() if k in targets or k in review_targets or k in {r["target"] for r in metrics}}
            events = [{"target": "US:" + row["symbol"], "title": row["name"] + " 财报", "date": row["earnings_date"],
                       "status": "estimated", "kind": "earnings", "session": row.get("session"), "eps_forecast": row.get("eps_forecast"),
                       "eps_basis": "财季截至 " + str(row.get("fiscal_quarter") or "未核验") + "；会计口径待核验", "eps_currency": row.get("eps_currency"),
                       "date_conflict": row.get("date_conflict"), "source_url": next((s.get("url") for s in row.get("sources", []) if s.get("name") == "Nasdaq"), (row.get("sources") or [{}])[0].get("url")),
                       "source": " / ".join(s.get("name", "") for s in row.get("sources", [])),
                       "observed_at": now, "data_as_of": directory.get("runtime", {}).get("collected_at")}
                      for row in rows if "US:" + row["symbol"] in targets and row.get("earnings_date")]
            for metric in metrics:
                if metric.get("earnings_date"):
                    events.append({"target": metric["target"], "title": metric["name"] + " 财报", "date": metric["earnings_date"],
                        "status": "estimated", "source": "TradingView 最新目录", "source_url": metric["source_url"],
                        "kind": "earnings", "eps_basis": metric.get("eps_basis"), "eps_forecast": metric.get("eps_forecast"), "observed_at": metric["observed_at"]})
            posts = []
            for kind in ("news", "social"):
                records = self.ledger.inputs(kind, limit=200)["items"]
                targets_by_input = {}
                if records:
                    with self.ledger.session() as db:
                        ids = [record["id"] for record in records]
                        for row in db.execute("SELECT DISTINCT input_id,target FROM analysis_records WHERE input_id IN (" + ",".join("?" for _ in ids) + ")", ids):
                            targets_by_input.setdefault(row["input_id"], []).append(row["target"])
                for record in records:
                    payload = record["payload"]
                    if isinstance(payload, dict):
                        posts.append({**payload, "targets": sorted(set(payload.get("targets", [])) | set(targets_by_input.get(record["id"], []))), "observed_at": record["first_received_at"]})
            funds = {}
            for record in reversed(self.ledger.inputs("etf_holdings", limit=50)["items"]):
                payload = record["payload"]
                if isinstance(payload, dict):
                    funds.update(payload.get("funds") or {})
            observations = sample_attention(posts, target, now)
            diary = {(r["target"], r["platform"], r["date"]): r for r in self.data.get("attention", [])}
            diary.update({(r["target"], r["platform"], r["date"]): r for r in observations})
            attention = sorted(diary.values(), key=lambda r: r["observed_at"])[-1200:]
            events.extend(news_events(posts, target, now))
            events = remember_events(events, self.data.get("events", []))
            data = {"target": target, "benchmark": benchmark, "histories": histories, "events": events, "peers": metrics, "consensus": consensus,
                    "posts": posts, "funds": funds, "attention": attention, "errors": errors, "observed_at": now,
                    "coverage": "同行使用美股、A股、港股或韩股同市场行业分类；财务缺项不估填。事件接入财报库与有日期的新闻线索，其余资料可导入。未取得的平台不记为零。"}
            self.ledger.observe(self.kind("portfolio_research"), "workspace", data)
            with self.lock:
                self.data = data
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False

    def import_data(self, value):
        if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False)) > 500000:
            raise ValueError("研究导入须为小于500KB的对象")
        now = datetime.now(timezone.utc).isoformat()
        clean = {}
        for kind in ("events", "attention", "peers"):
            rows = value.get(kind, [])
            if not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError("每类研究资料最多1000条")
            clean[kind] = []
            for row in rows:
                if not isinstance(row, dict) or not re.fullmatch(r"https://[^\s/@]+(?:/[^\s]*)?", str(row.get("source_url", ""))):
                    raise ValueError("研究资料须有有效HTTPS来源")
                market, symbol = normalize_target(row.get("target", ""))
                row = {**row, "target": market + ":" + symbol}
                if kind in {"events", "attention"}:
                    datetime.strptime(str(row.get("date", "")), "%Y-%m-%d")
                if kind == "events" and (not row.get("title") or row.get("status") not in {"confirmed", "estimated", "cancelled"}):
                    raise ValueError("事件标题或状态无效")
                if kind == "attention" and (not row.get("platform") or not row.get("coverage") or not row.get("source") or
                        not all(isinstance(row.get(k), (int, float)) and not isinstance(row[k], bool) and math.isfinite(row[k]) for k in ("count", "total")) or
                        not 0 <= row["count"] <= row["total"] or row["total"] <= 0 or row["date"] > now[:10]):
                    raise ValueError("热度样本数或覆盖口径无效")
                if kind == "peers":
                    if row.get("market") != market or not row.get("name") or not row.get("industry"):
                        raise ValueError("同行须注明正式名称、市场及细分行业")
                    for key in ("pe", "market_cap", "dividend_yield", "payout_ratio", "leverage", "volatility", "net_income", "pb", "ps", "net_margin", "roe", "npl", "revenue_growth", "fcf_margin", "retention"):
                        metric = row.get(key)
                        if metric is None:
                            continue
                        if not isinstance(metric, dict) or not isinstance(metric.get("value"), (int, float)) or isinstance(metric["value"], bool) or not math.isfinite(metric["value"]) or not metric.get("basis") or not metric.get("currency"):
                            raise ValueError("同行指标须填写有限数值、币种及计算口径")
                        when = datetime.fromisoformat(str(metric.get("as_of", "")).replace("Z", "+00:00"))
                        if (when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when) > datetime.fromisoformat(now):
                            raise ValueError("指标日期不能在未来")
                clean[kind].append({**row, "observed_at": now, "first_observed_at": now, "imported": True})
        return self.ledger.observe(self.kind("portfolio_research_import"), uuid.uuid4().hex, clean)["id"]

    def freeze(self, payload):
        now = datetime.now(timezone.utc)
        target = ":".join(normalize_target(payload.get("target", "")))
        if not isinstance(payload.get("direction"), int) or isinstance(payload["direction"], bool) or payload["direction"] not in {-1, 0, 1}:
            raise ValueError("判断方向无效")
        if not isinstance(payload.get("note"), str) or not payload["note"].strip():
            raise ValueError("请填写依据、成立及失效条件")
        data = self.snapshot()
        history = data.get("histories", {}).get(target, {})
        valid_points = {}
        for point in history.get("points", []):
            try:
                when = datetime.fromisoformat(str(point.get("time", "")).replace("Z", "+00:00"))
                when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
                close = point.get("close")
                if when <= now and isinstance(close, (float, int)) and not isinstance(close, bool) and math.isfinite(close) and close > 0:
                    valid_points[when.date().isoformat()] = point
            except (ValueError, TypeError):
                continue
        history = {**history, "points": sorted(valid_points.values(), key=lambda r: r["time"])}
        events = data.get("events", []) + [event for imported in data["imports"] for event in imported.get("events", [])]
        latest = {}
        for event in events:
            try:
                observed = datetime.fromisoformat(str(event.get("observed_at", "")).replace("Z", "+00:00"))
                observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed
                if observed > now or not str(event.get("source_url", "")).startswith("https://"):
                    continue
            except (ValueError, TypeError):
                continue
            key = event_key(event)
            if key not in latest or str(latest[key].get("observed_at", "")) <= str(event.get("observed_at", "")):
                latest[key] = event
        events = list(latest.values())
        dated_events = [event for event in events if event.get("target") == target and now.date().isoformat() <= event.get("date", "") <= (now + timedelta(days=14)).date().isoformat()]
        events = [event for event in dated_events if event.get("status") != "cancelled"]
        event_candidates = copy.deepcopy(dated_events[:30])
        selected_key = payload.get("event_key", "")
        if selected_key:
            selected = next((e for e in events if event_key(e) == selected_key), None)
            if not selected:
                raise ValueError("关联事件已失效或移出14日窗口，请重新核对")
            if payload.get("event_date") and payload["event_date"] != selected["date"]:
                raise ValueError("事件日期已变更，请重新生成研究草稿")
            earnings = lambda e: e.get("type") != "news_lead" and (e.get("kind") == "earnings" or re.search(r"财报|业绩|earnings", e.get("title", ""), re.I))
            conflict = selected.get("date_conflict") or earnings(selected) and any(earnings(e) and (e.get("date_conflict") or e.get("status") == "cancelled" or e["date"] != selected["date"]) and abs((datetime.fromisoformat(e["date"])-datetime.fromisoformat(selected["date"])).days) <= 14 for e in dated_events)
            stamp = datetime.fromisoformat(str(selected.get("data_as_of") or selected["observed_at"]).replace("Z", "+00:00"))
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            recent = sorted(valid_points.values(), key=lambda p: p["time"])[-8:]
            dates = [datetime.fromisoformat(p["time"].replace("Z", "+00:00")) for p in recent]
            dates = [d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d for d in dates]
            stale = not dates or now-dates[-1] > timedelta(days=7) or any(b-a > timedelta(days=7) for a, b in zip(dates, dates[1:]))
            if conflict or not now-timedelta(days=14) <= stamp <= now or stale:
                raise ValueError("事件日期冲突或资料过期，请更新核对后冻结")
            events = [selected]
        if len(history.get("points", [])) < 8 or not events:
            raise ValueError("需要8根日线及未来14日有效事件，不能冻结缺证判断")
        row = {"target": target, "created_at": now.isoformat(), "due_at": (now + timedelta(days=14)).isoformat(),
               "direction": payload["direction"], "note": str(payload.get("note", ""))[:4000],
               "event_key": selected_key, "event_date": payload.get("event_date", ""), "inputs": {"history": history, "events": events, "event_candidates": event_candidates}, "method": "event-forward-v1"}
        posts = []
        for p in data.get("posts", []):
            if p.get("target") != target and target not in p.get("targets", []):
                continue
            try:
                times = [datetime.fromisoformat(str(p.get(k)).replace("Z", "+00:00")) for k in ("published_at", "observed_at")]
                if any((t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t) > now for t in times):
                    continue
            except (ValueError, TypeError):
                continue
            posts.append(p)
        row["inputs"]["posts"] = copy.deepcopy(posts[:40])
        for key in ("fee_bps", "slippage_bps"):
            try:
                if isinstance(payload.get(key), bool):
                    raise ValueError("费用不能是布尔值")
                value = float(payload.get(key, 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("费用须为数值") from exc
            if not math.isfinite(value) or not 0 <= value <= 1000:
                raise ValueError("费用须在0至1000基点之间")
            row[key] = value
        if payload.get("method") == "event-forward-v2":
            row["method"] = "event-forward-v2"
            for key in ("author", "expectation", "confirmation", "invalidation"):
                value = payload.get(key)
                if not isinstance(value, str) or not value.strip() or len(value) > (60 if key == "author" else 2000):
                    raise ValueError("请完整填写作者、预期基准、成立和失效条件")
                row[key] = value.strip()
            row["benchmark"] = ":".join(normalize_target(payload.get("benchmark")))
            if row["benchmark"].split(":")[0] not in {"US", "CN", "HK", "KR"}:
                raise ValueError("比较基准市场不支持")
            row["inputs"]["benchmark"] = copy.deepcopy(data.get("histories", {}).get(row["benchmark"]))
            for key in ("tax_bps", "borrow_bps"):
                value = payload.get(key, 0)
                try:
                    if isinstance(value, bool):
                        raise ValueError()
                    value = float(value)
                except (ValueError, TypeError) as exc:
                    raise ValueError("税费及借券费须为数值") from exc
                if not math.isfinite(value) or not 0 <= value <= 10000:
                    raise ValueError("税费及借券费须在0至10000基点之间")
                row[key] = value
            parent_id = payload.get("parent_id") or ""
            parent = next((r for r in data["records"] if r.get("id") == parent_id), None) if parent_id else None
            if parent_id and (not parent or parent["target"] != target):
                raise ValueError("原版本不属于当前组合和标的")
            row.update(parent_id=parent_id, version=(parent.get("version", 1) + 1) if parent else 1)
        row["id"] = uuid.uuid4().hex
        self.ledger.observe(self.kind("portfolio_event_record"), row["id"], row)
        return row

    def shutdown(self):
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        if self.performance_thread and self.performance_thread.is_alive():
            self.performance_thread.join(timeout=2)
