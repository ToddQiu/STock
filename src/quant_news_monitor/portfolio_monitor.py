from __future__ import annotations

import copy
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .adr_monitor import _fetch_yahoo_quote
from .config_store import ConfigStore
from .fund_data import fetch_china_fund_quote
from .models import iso_utc, utc_now
from .startup_cache import SnapshotStore, restore_quotes
from .portfolio_alerts import evaluate_risk, quote_usable
from .system_notifier import WindowsSystemNotifier
from .portfolio_book import all_positions, portfolios
from .security_profiles import ProfileCollector


REFRESH_INTERVAL_SECONDS = 20
FX_SYMBOLS = {
    "USD_CNY": "CNY=X",
    "USD_HKD": "HKD=X",
    "USD_KRW": "KRW=X",
}


def _fetch_position_quote(symbol, timeout):
    market = "HK" if symbol.endswith(".HK") else "CN" if symbol.endswith((".SS", ".SZ", ".BJ")) else "KR" if symbol.endswith((".KS", ".KQ")) else "US"
    if market in {"CN", "HK"}:
        from .public_quotes import fetch_tencent_quote
        try:
            return fetch_tencent_quote(market, symbol, min(timeout, 5))
        except (OSError, ValueError):
            pass
    try:
        return _fetch_yahoo_quote(symbol, min(timeout, 5))
    except (OSError, ValueError):
        if market != "US":
            raise
        from .public_quotes import fetch_tencent_quote
        return fetch_tencent_quote(market, symbol, min(timeout, 5))


class PortfolioMonitor:
    def __init__(
        self,
        config_path: str,
        *,
        interval_seconds: int = REFRESH_INTERVAL_SECONDS,
        timeout_seconds: int = 10,
        now: Callable[[], datetime] = utc_now,
        snapshot_store: SnapshotStore | None = None,
        system_notifications_allowed: bool = True,
    ):
        self.config_store = ConfigStore(config_path)
        self.interval_seconds = max(10, int(interval_seconds))
        self.timeout_seconds = timeout_seconds
        self.now = now
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._refresh_in_progress = False
        self._thread: threading.Thread | None = None
        self._snapshot_store = snapshot_store
        self._profiles = ProfileCollector(snapshot_store)
        self._snapshot = restore_quotes(snapshot_store, "portfolio") or _empty_snapshot(self.interval_seconds)
        self._risk = (snapshot_store.load("portfolio_risk") if snapshot_store else None) or {}
        self._notifier = WindowsSystemNotifier()
        self._system_notifications_allowed = system_notifications_allowed

    def start(self) -> None:
        self._profiles.request(all_positions(self.config_store.read()))
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="portfolio-quotes",
                daemon=True,
            )
            self._thread.start()

    def shutdown(self) -> None:
        self._profiles.shutdown()
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        if self._snapshot_store:
            self._snapshot_store.save("portfolio", self.snapshot(), force=True)

    def trigger_refresh(self) -> None:
        self._profiles.request(all_positions(self.config_store.read()))
        self._wake_event.set()

    def snapshot(self, portfolio_id: str = "default") -> dict[str, Any]:
        with self._lock:
            result = copy.deepcopy(self._snapshot)
            result["refresh_in_progress"] = self._refresh_in_progress
            result["risk"] = copy.deepcopy(self._risk if portfolio_id == "default" else self._risk.get("portfolios", {}).get(portfolio_id, {}))
            result["risk"].pop("portfolios", None)
            result["risk"]["system_notifications_available"] = self._system_notifications_allowed and self._notifier.available
            return result

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            if self._refresh_in_progress:
                return copy.deepcopy(self._snapshot)
            self._refresh_in_progress = True
            previous = copy.deepcopy(self._snapshot)
        try:
            raw = self.config_store.read()
            positions = all_positions(raw)
            # Whitelist export needs the same timestamped quotes as holdings;
            # sharing this worker avoids requests during a frozen export.
            targets = {(row['market'], row['symbol']): row for row in positions}
            from .security_master import CURRENCIES
            for row in raw.get('stock_whitelist', []):
                targets.setdefault((row['market'], row['symbol']), {
                    **row, 'currency': CURRENCIES.get(row['market'], 'CNY')})
            positions = list(targets.values())
            account_currencies = {account.get(key, "CNY") for account in portfolios(raw)
                                  for key in ("base_currency", "principal_currency", "cash_currency")}
            account_currencies.update(currency for account in portfolios(raw) for currency in account.get("cash_balances", {}))
            account_currencies.update(currency for account in portfolios(raw) for currency in account.get("receivable_balances", {}))
            account_currencies.update(currency for account in portfolios(raw) for currency in account.get("payable_balances", {}))
            account_currencies.update(record.get("currency", "CNY") for account in portfolios(raw)
                                      for record in account.get("trades", []))
            if not positions and account_currencies <= {"CNY"}:
                snapshot = _empty_snapshot(self.interval_seconds)
                snapshot["updated_at"] = iso_utc(self.now())
                snapshot["status"] = "empty"
                return self._save(snapshot)

            symbols = sorted({str(item.get("symbol") or "").upper() for item in positions if item.get("symbol")})
            fund_symbols = {
                str(item.get("symbol") or "").upper()
                for item in positions
                if str(item.get("market") or "").upper() == "FUND"
            }
            currencies = {str(item.get("currency") or "CNY").upper() for item in positions}
            currencies.update(account_currencies)
            required_fx = {"USD_CNY"} if currencies - {"CNY"} else set()
            if "HKD" in currencies:
                required_fx.add("USD_HKD")
            if "KRW" in currencies:
                required_fx.add("USD_KRW")

            previous_quotes = {str(item.get("symbol")): item for item in previous.get("quotes", [])}
            quotes: list[dict[str, Any]] = []
            fx_values: dict[str, float] = {}
            fx_details = copy.deepcopy(previous.get("fx_details") or {})
            verified_fx: set[str] = set()
            errors: list[str] = []
            jobs: dict[Any, tuple[str, str]] = {}
            with ThreadPoolExecutor(max_workers=min(8, len(symbols) + len(required_fx))) as executor:
                for symbol in symbols:
                    fetcher = (
                        fetch_china_fund_quote
                        if symbol in fund_symbols
                        else _fetch_position_quote
                    )
                    jobs[executor.submit(fetcher, symbol, self.timeout_seconds)] = ("quote", symbol)
                for fx_key in required_fx:
                    fx_symbol = FX_SYMBOLS[fx_key]
                    jobs[executor.submit(_fetch_yahoo_quote, fx_symbol, self.timeout_seconds)] = ("fx", fx_key)
                for future in as_completed(jobs):
                    kind, key = jobs[future]
                    try:
                        result = future.result()
                        if kind == "fx":
                            fx_values[key] = float(result["price"])
                            fx_details[key] = {"price": result["price"], "quote_time": result.get("quote_time"), "source": result.get("source") or "Yahoo Finance", "status": result.get("status") or "delayed"}
                            if quote_usable(result, self.now()):
                                verified_fx.add(key)
                        else:
                            old = previous_quotes.get(key, {})
                            if (result.get("regular_quote_time") and result.get("regular_quote_time") == old.get("regular_quote_time")
                                    and result.get("regular_previous_close") is None):
                                result["regular_previous_close"] = old.get("regular_previous_close")
                            if result.get("status") == "stale":
                                errors.append(f"{key}: {result.get('quote_error') or '报价已过期'}")
                            quotes.append(
                                {
                                    **result,
                                    "status": result.get("status") or "live",
                                    "source": result.get("source") or "Yahoo Finance",
                                    "source_url": result.get("source_url")
                                    or f"https://finance.yahoo.com/quote/{key}/",
                                    "error": None,
                                }
                            )
                    except Exception as exc:
                        errors.append(f"{key}: {type(exc).__name__}: {exc}")
                        if kind == "fx":
                            fx_details[key] = {**fx_details.get(key, {}), "status": "stale", "error": str(exc)}
                        if kind == "quote" and key in previous_quotes:
                            quotes.append({**previous_quotes[key], "status": "stale", "error": str(exc), "quote_error": str(exc)})
                        elif kind == "quote":
                            quotes.append({"symbol": key, "status": "error", "quote_error": "来源连接失败，请重试；无可用历史报价", "error": str(exc)})

            quote_order = {symbol: index for index, symbol in enumerate(symbols)}
            quotes.sort(key=lambda item: quote_order.get(str(item.get("symbol")), 999))
            rates = _build_cny_rates(currencies, fx_values, previous.get("fx_rates") or {})
            status = "live" if len(quotes) == len(symbols) and not errors else "partial" if quotes else "error"
            source = (
                "天天基金"
                if fund_symbols == set(symbols)
                else "Yahoo Finance / 天天基金"
                if fund_symbols
                else "Yahoo Finance"
            )
            snapshot = {
                "status": status,
                "updated_at": iso_utc(self.now()),
                "interval_seconds": self.interval_seconds,
                "source": source,
                "quotes": quotes,
                "fx_rates": rates,
                "fx_details": fx_details,
                "fx_valid": required_fx <= verified_fx,
                "errors": errors,
            }
            return self._save(snapshot)
        except Exception as exc:
            snapshot = {
                **previous,
                "status": "stale" if previous.get("quotes") else "error",
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
            return self._save(snapshot)
        finally:
            with self._lock:
                self._refresh_in_progress = False

    def _save(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            try:
                accounts = portfolios(self.config_store.read())
                results, events = {}, []
                for account in accounts:
                    identifier = account["id"]
                    previous = self._risk if identifier == "default" else self._risk.get("portfolios", {}).get(identifier, {})
                    analysis = build_portfolio_analysis(account.get("positions", []), snapshot, [], [], now=self.now(), account=account)
                    risk, fresh = evaluate_risk(analysis, snapshot, previous, self.now())
                    risk["notification_error"] = previous.get("notification_error")
                    for event in fresh:
                        event.update(portfolio_id=identifier, portfolio_name=account["name"])
                        if identifier != "default":
                            event["id"] = identifier + ":" + event["id"]
                            event["title"] = account["name"] + " · " + event["title"]
                    results[identifier] = risk
                    events.extend(fresh)
                self._risk = results.pop("default", {})
                self._risk["portfolios"] = results
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                # Alert failures must not stop the existing quote-refresh worker.
                self._risk["storage_error"] = "警戒线检测暂不可用，等待配置或行情恢复"
                self._snapshot = snapshot
                return copy.deepcopy(snapshot)
            # Persist the crossing before sending: a normal restart must not replay alerts.
            persisted = not self._snapshot_store or self._snapshot_store.save("portfolio_risk", self._risk, force=True)
            self._risk["storage_error"] = None if persisted else "告警记录保存失败，系统弹窗已暂停"
            for risk in self._risk.get("portfolios", {}).values():
                risk["storage_error"] = self._risk["storage_error"]
            notices = [event for event in events if event["desktop_notify"]]
            self._snapshot = snapshot
            if self._snapshot_store:
                self._snapshot_store.save("portfolio", snapshot, force=snapshot.get("status") == "empty")
        # Windows submission can take seconds; dashboard reads must not wait for it.
        if notices:
            ok, error = (False, "告警记录未能持久化，已暂停系统弹窗")
            available = self._system_notifications_allowed and self._notifier.available
            if persisted and available:
                title = notices[0]["title"] if len(notices) == 1 else f"{len(notices)} 项持仓触及警戒线"
                try:
                    ok, error = self._notifier.notify(title, "；".join(e["title"] + "：" + e["reason_summary"] for e in notices))
                except OSError:
                    ok, error = False, "系统通知启动失败，触线记录已保留"
            elif persisted:
                error = None
            with self._lock:
                for event in notices:
                    event["notification"] = "submitted" if ok else "failed" if available else "unavailable"
                    risk = self._risk if event["portfolio_id"] == "default" else self._risk["portfolios"][event["portfolio_id"]]
                    risk["notification_error"] = error
                    if not ok and available:
                        key = f"{event['market']}:{event['symbol']}:{event['kind']}"
                        risk["states"][key]["pending_alert"] = True
                if self._snapshot_store:
                    self._snapshot_store.save("portfolio_risk", self._risk, force=True)
        with self._lock:
            return copy.deepcopy(snapshot)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._profiles.request(all_positions(self.config_store.read()))
            self.refresh()
            self._wake_event.wait(self.interval_seconds)
            self._wake_event.clear()


def build_portfolio_analysis(
    positions: list[dict[str, Any]],
    market_snapshot: dict[str, Any],
    news: list[dict[str, Any]],
    watchlist: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    principal_cny: float | None = None,
    account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = (now or utc_now()).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    quotes = {str(item.get("symbol", "")).upper(): item for item in market_snapshot.get("quotes", [])}
    fx_rates = {str(key): float(value) for key, value in (market_snapshot.get("fx_rates") or {}).items()}
    factors = {
        (str(item.get("market", "")).upper(), _normalized_symbol(item.get("symbol"))): item.get("trump_factor") or {}
        for item in watchlist
    }
    rows: list[dict[str, Any]] = []
    errors = list(market_snapshot.get("errors") or [])
    for position in positions:
        market = str(position.get("market", "")).upper()
        symbol = str(position.get("symbol", "")).upper()
        currency = str(position.get("currency") or "CNY").upper()
        quantity = float(position.get("quantity") or 0.0)
        average_cost = float(position.get("average_cost") or 0.0)
        quote = quotes.get(symbol) or {}
        from .security_master import cached_identity, preferred_name
        try:
            identity = cached_identity(market, symbol)
        except ValueError:
            identity = None
        position = dict(position)
        if identity:
            position['name'] = preferred_name(market, identity['name'], quote.get('name'), position.get('name'))
            for field in ('sector', 'industry', 'asset_type', 'investment_focus', 'daily_leverage',
                          'classification_source', 'classification_source_url', 'classification_as_of',
                          'classification_error', 'product_source', 'product_source_url', 'product_as_of'):
                if field in identity:
                    position[field] = identity[field]
            position.setdefault('classification_source', identity.get('source'))
            position.setdefault('classification_source_url', identity.get('source_url'))
            position.setdefault('classification_as_of', identity.get('as_of'))
        elif not position.get("identity_source"):
            position["name"] = symbol + "（名称待核验）"
        position["identity_warning"] = not bool(identity or position.get("identity_source"))
        if quote.get("currency") and str(quote["currency"]).upper() != currency:
            message = f"{symbol} 报价为 {quote['currency']}，持仓成本为 {currency}；请核对成本币种，暂不计算估值与盈亏"
            errors.append(message)
            quote = {**quote, "price": None, "previous_close": None, "change_pct": None,
                     "status": "error", "quote_error": message}
        price = _finite_number(quote.get("price"))
        previous_close = _finite_number(quote.get("previous_close"))
        price = price if price is not None and price > 0 else None
        previous_close = previous_close if previous_close is not None and previous_close > 0 else None
        change_pct = _finite_number(quote.get("change_pct"))
        fx_rate = fx_rates.get(currency)
        market_value = quantity * price if price is not None else None
        cost_value = quantity * average_cost
        pnl = market_value - cost_value if market_value is not None else None
        pnl_pct = pnl / cost_value * 100.0 if pnl is not None and cost_value > 0 else None
        market_value_cny = market_value * fx_rate if market_value is not None and fx_rate else None
        cost_value_cny = cost_value * fx_rate if fx_rate else None
        pnl_cny = pnl * fx_rate if pnl is not None and fx_rate else None
        daily_pnl_cny = (
            quantity * (price - previous_close) * fx_rate
            if price is not None and previous_close is not None and fx_rate
            else None
        )
        latest_nav_pnl_cny = daily_pnl_cny if market == "FUND" else None
        if market == "FUND" and str(quote.get("nav_date") or "") != today:
            daily_pnl_cny = None
        if quote.get("status") in {"stale", "error", "waiting"}:
            daily_pnl_cny = None
        related_news = [
            item for item in news
            if str(item.get("market", "")).upper() == market
            and _normalized_symbol(item.get("symbol")) == _normalized_symbol(symbol)
        ]
        news_score = (
            sum(float(item.get("score") or 0.0) for item in related_news) / len(related_news)
            if related_news else 0.0
        )
        trump_factor = factors.get((market, _normalized_symbol(symbol)), {})
        trump_score = float(trump_factor.get("score") or 0.0)
        trend_available = change_pct is not None and quote.get("status") not in {"stale", "error", "waiting"}
        trend_score = max(-1.0, min(1.0, (change_pct or 0) / 5.0))
        if quote.get("status") in {"stale", "error", "waiting"}:
            trend_score = 0.0
        signal_score = max(-1.0, min(1.0, trend_score * 0.45 + news_score * 0.35 + trump_score * 0.20))
        rows.append(
            {
                **position,
                "price": price,
                "previous_close": previous_close,
                "change_pct": change_pct,
                "quote_time": quote.get("quote_time"),
                "nav_date": quote.get("nav_date"),
                "price_kind": quote.get("price_kind") or "market_quote",
                "price_label": quote.get("price_label") or "最新价",
                "quote_source": quote.get("source") or market_snapshot.get("source"),
                "quote_status": quote.get("status") or "waiting",
                **{key: quote.get(key) for key in ("market_session", "quote_session", "session_periods", "regular_price", "regular_quote_time", "regular_previous_close", "extended_change_pct", "session_change_pct", "quote_error", "source_note")},
                "quote_fetch_error": quote.get("error"),
                "source_url": quote.get("source_url") or f"https://finance.yahoo.com/quote/{symbol}/",
                "market_value": market_value,
                "cost_value": cost_value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "market_value_cny": market_value_cny,
                "cost_value_cny": cost_value_cny,
                "pnl_cny": pnl_cny,
                "daily_pnl_cny": daily_pnl_cny,
                "daily_pnl": quantity * (price - previous_close) if price is not None and previous_close is not None and quote.get("status") not in {"stale", "error", "waiting"} and (market != "FUND" or str(quote.get("nav_date") or "") == today) else None,
                "latest_nav_pnl_cny": latest_nav_pnl_cny,
                "news_score": round(news_score, 4),
                "news_count": len(related_news),
                "trump_score": round(trump_score, 4),
                "signal_score": round(signal_score, 4),
                "signal_available": trend_available or bool(related_news) or bool(trump_factor.get("post_count")) or trump_score != 0,
                "weight_pct": None,
            }
        )

    total_value = sum(float(item["market_value_cny"]) for item in rows if item["market_value_cny"] is not None)
    total_cost = sum(float(item["cost_value_cny"]) for item in rows if item["cost_value_cny"] is not None)
    priced_cost = sum(
        float(item["cost_value_cny"])
        for item in rows
        if item["price"] is not None and item["cost_value_cny"] is not None
    )
    total_pnl = sum(float(item["pnl_cny"]) for item in rows if item["pnl_cny"] is not None)
    total_daily = sum(float(item["daily_pnl_cny"]) for item in rows if item["daily_pnl_cny"] is not None)
    total_pnl_pct = total_pnl / priced_cost * 100.0 if priced_cost > 0 else 0.0
    valued_count = sum(item["market_value_cny"] is not None for item in rows)
    daily_count = sum(item["daily_pnl_cny"] is not None for item in rows)
    complete = valued_count == len(rows)
    principal = _finite_number(principal_cny) if not isinstance(principal_cny, bool) else None
    if principal is not None and not 0 <= principal <= 1_000_000_000_000:
        principal = None
    if principal is not None:
        principal = round(principal, 2)
    principal_status = "available"
    if not principal:
        principal_status = "unset"
    elif not rows:
        principal_status = "empty"
    elif not complete or not math.isfinite(total_value):
        principal_status = "incomplete"
    elif (market_snapshot.get("status") in {"stale", "error", "waiting"}
          or any(item["quote_status"] in {"stale", "error", "waiting"} or not item["price"] or item["price"] < 0
                 or not math.isfinite(item["market_value_cny"]) or item["market_value_cny"] <= 0 for item in rows)):
        principal_status = "unavailable"
    principal_pnl = total_value - principal if principal_status == "available" else None
    principal_return = principal_pnl / principal * 100 if principal_pnl is not None else None
    result = {
        "base_currency": "CNY",
        "positions": rows,
        "summary": {
            "principal_cny": principal,
            "principal_pnl_cny": principal_pnl,
            "principal_return_pct": principal_return,
            "principal_loss_pct": max(0.0, -principal_return) if principal_return is not None else None,
            "principal_status": principal_status,
            "position_count": len(rows),
            "priced_count": sum(1 for item in rows if item["price"] is not None),
            "total_market_value_cny": total_value if valued_count or not rows else None,
            "valued_count": valued_count,
            "valuation_complete": complete,
            "stale_valued_count": sum(item["market_value_cny"] is not None and item["quote_status"] == "stale" for item in rows),
            "total_cost_cny": total_cost,
            "priced_cost_cny": priced_cost,
            "total_pnl_cny": total_pnl if valued_count or not rows else None,
            "total_pnl_pct": total_pnl_pct if valued_count or not rows else None,
            "daily_pnl_cny": total_daily if daily_count or not rows else None,
            "daily_pnl_count": daily_count,
            "daily_pnl_complete": daily_count == len(rows),
        },
        "risk": copy.deepcopy(market_snapshot.get("risk") or {}),
        "analysis": {
            "method": "价格趋势 45% + 新闻情绪 35% + Trump 因子 20%",
            "disclaimer": "基于公开行情和新闻的研究提示，不构成个性化投资建议。",
        },
        "runtime": {
            "status": market_snapshot.get("status") or "waiting",
            "restored_from_cache": bool(market_snapshot.get("restored_from_cache")),
            "updated_at": market_snapshot.get("updated_at"),
            "interval_seconds": market_snapshot.get("interval_seconds") or REFRESH_INTERVAL_SECONDS,
            "refresh_in_progress": bool(market_snapshot.get("refresh_in_progress")),
            "errors": errors,
        },
    }
    from .portfolio_account import apply_account
    result = apply_account(result, market_snapshot, account or {"principal_cny": principal_cny}, now=now)
    summary = result["summary"]
    total_base = summary["total_market_value_base"]
    complete = summary["positions_valuation_complete"]
    market_values = {}
    for item in rows:
        value = item["market_value_base"]
        item["weight_pct"] = value / total_base * 100 if complete and total_base and total_base > 0 else None
        if value is not None:
            market_values[item["market"]] = market_values.get(item["market"], 0) + value
        item["analysis"] = _position_analysis(item)
    top_weight = max((item["weight_pct"] or 0 for item in rows), default=0) if complete else None
    allocations = [{"market": market, "value_base": value,
                    "value_cny": sum(r["market_value_cny"] for r in rows if r["market"] == market)
                    if all(r["market_value_cny"] is not None for r in rows if r["market"] == market) else None,
                    "weight_pct": value / total_base * 100 if complete and total_base and total_base > 0 else None}
                   for market, value in sorted(market_values.items(), key=lambda entry: entry[1], reverse=True)]
    risk_level = None if not rows or not complete else "高" if (top_weight or 0) >= 50 or any(r["analysis"]["risk_level"] == "高" for r in rows) else "中" if (top_weight or 0) >= 30 else "低"
    summary["top_position_weight_pct"] = top_weight
    result["allocations"] = allocations
    result["analysis"].update(risk_level=risk_level,
        title="暂无持仓，等待录入" if not rows else "估值数据不完整" if not complete else "暂未发现集中度异常" if risk_level == "低" else "组合需要关注" if risk_level == "中" else "组合风险需要复核",
        suggestions=_portfolio_suggestions(rows, allocations, top_weight or 0))
    return result


def _position_analysis(item: dict[str, Any]) -> dict[str, Any]:
    signal = float(item.get("signal_score") or 0.0)
    pnl_pct = item.get("pnl_pct")
    weight = float(item.get("weight_pct") or 0.0)
    risks: list[str] = []
    if weight >= 35:
        risks.append(f"单一持仓占比 {weight:.1f}%")
    if pnl_pct is not None and float(pnl_pct) <= -15:
        risks.append(f"浮亏 {float(pnl_pct):.1f}%")
    if int(item.get("news_count") or 0) == 0:
        risks.append("近期消息样本不足")
    if item.get("quote_status") != "live":
        risks.append("行情可能不是最新")
    if item.get("quote_session") in {"pre", "post"}:
        risks.append("盘前盘后流动性与价差风险")
    risk_level = "高" if weight >= 50 or (pnl_pct is not None and float(pnl_pct) <= -20) else "中" if risks else "低"
    if weight >= 35:
        action = "控制集中度"
    elif signal <= -0.25 and pnl_pct is not None and float(pnl_pct) < 0:
        action = "复核经营与估值"
    elif signal >= 0.25:
        action = "跟踪成立条件"
    else:
        action = "继续观察"
    direction = "偏多" if signal >= 0.18 else "偏空" if signal <= -0.18 else "中性"
    if item.get("signal_available") is False:
        direction = "待确认"
        if action != "控制集中度":
            action = "等待有效数据"
    price_term = "最近净值" if item.get("market") == "FUND" else "当日"
    change_text = f"{float(item['change_pct']):+.2f}%" if item.get("change_pct") is not None else "暂无有效涨跌"
    reason = f"{price_term} {change_text}，新闻 {float(item.get('news_score') or 0.0):+.2f}，Trump {float(item.get('trump_score') or 0.0):+.2f}。"
    return {
        "direction": direction,
        "action": action,
        "risk_level": risk_level,
        "reason": reason,
        "risks": risks,
    }


def _portfolio_suggestions(
    rows: list[dict[str, Any]],
    allocations: list[dict[str, Any]],
    top_weight: float,
) -> list[str]:
    if not rows:
        return ["添加第一笔持仓后，将生成仓位、盈亏、消息面和集中度分析。"]
    suggestions: list[str] = []
    if top_weight >= 35:
        top = max(rows, key=lambda item: float(item.get("weight_pct") or 0.0))
        suggestions.append(f"{top['name']} 占组合 {top_weight:.1f}%，建议先设定可接受的单一持仓上限。")
    if allocations and float(allocations[0]["weight_pct"] or 0) >= 70:
        suggestions.append(f"组合有 {float(allocations[0]['weight_pct']):.1f}% 集中在 {allocations[0]['market']} 市场，需关注市场与汇率共振风险。")
    negative = [item for item in rows if float(item.get("signal_score") or 0.0) <= -0.25]
    if negative:
        suggestions.append(f"{len(negative)} 个持仓的综合信号偏弱，优先核对最新新闻与风险线，不要只依据成本价决策。")
    missing = [item for item in rows if item.get("market_value_base") is None]
    if missing:
        suggestions.append(f"{len(missing)} 个持仓暂缺有效行情，组合市值和仓位占比尚不完整。")
    if any(item.get("market") == "FUND" for item in rows):
        suggestions.append("基金仅在净值日期为今天时计入当日盈亏；其他日期的净值变动单独展示，不冒充今日收益。")
    if not suggestions:
        suggestions.append("当前没有明显的集中度或负面信号，继续跟踪财报、行业景气与关键价格位。")
    suggestions.append("浮动盈亏只针对当前持仓，不含已卖出收益、分红及税费；当日变动按当前数量估算，不是完整交易账单。")
    return suggestions


def _build_cny_rates(
    currencies: set[str],
    values: dict[str, float],
    previous: dict[str, Any],
) -> dict[str, float]:
    rates: dict[str, float] = {"CNY": 1.0}
    usd_cny = values.get("USD_CNY") or _finite_number(previous.get("USD"))
    if usd_cny:
        rates["USD"] = float(usd_cny)
        usd_hkd = values.get("USD_HKD")
        usd_krw = values.get("USD_KRW")
        if "HKD" in currencies:
            fallback = _finite_number(previous.get("HKD"))
            if usd_hkd and usd_hkd > 0:
                rates["HKD"] = float(usd_cny) / usd_hkd
            elif fallback:
                rates["HKD"] = fallback
        if "KRW" in currencies:
            fallback = _finite_number(previous.get("KRW"))
            if usd_krw and usd_krw > 0:
                rates["KRW"] = float(usd_cny) / usd_krw
            elif fallback:
                rates["KRW"] = fallback
    return rates


def _finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _normalized_symbol(value: Any) -> str:
    symbol = str(value or "").upper().rsplit(":", 1)[-1]
    for suffix in (".KS", ".KQ", ".HK", ".SS", ".SZ"):
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _empty_snapshot(interval_seconds: int) -> dict[str, Any]:
    return {
        "status": "waiting",
        "updated_at": None,
        "interval_seconds": interval_seconds,
        "source": "Yahoo Finance",
        "quotes": [],
        "fx_rates": {"CNY": 1.0},
        "errors": [],
    }
