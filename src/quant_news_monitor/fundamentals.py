"""Public SEC annual statements, retained in the existing research ledger."""

import copy
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from http.client import HTTPException
from datetime import date, timedelta

from .config_store import ConfigStore
from .adr_monitor import _fetch_yahoo_quote
from .models import iso_utc, utc_now, parse_datetime
from .news_transport import news_http, retry_delay


USER_AGENT = "SignalDesk/1.0 personal-research desktop application"
REFRESH_SECONDS = 21600
MAX_WORKERS = 2
BATCH_SIZE = 8
METRICS = {
    "revenue": ("营业收入", "USD", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")),
    "net_income": ("净利润", "USD", ("NetIncomeLoss", "ProfitLoss")),
    "operating_cash_flow": ("经营现金流", "USD", ("NetCashProvidedByUsedInOperatingActivities",)),
    "capex": ("资本开支", "USD", ("PaymentsToAcquirePropertyPlantAndEquipment",)),
    "eps": ("摊薄每股收益", "USD/shares", ("EarningsPerShareDiluted",)),
}


def annual_facts(payload, *, today=None):
    today = today or utc_now().date()
    facts = (payload.get("facts") or {}).get("us-gaap") or {}
    metrics = {}
    for key, (label, unit, tags) in METRICS.items():
        rows = []
        for tag in tags:
            for item in (facts.get(tag, {}).get("units", {}).get(unit) or []):
                try:
                    end, start, filed = (date.fromisoformat(item[field]) for field in ("end", "start", "filed"))
                    value = item["val"]
                    if (item.get("form") not in {"10-K", "10-K/A"} or item.get("fp") != "FY"
                            or not 300 <= (end - start).days <= 400 or end > today or filed > today
                            or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                        continue
                    rows.append({"value": value, "start": start.isoformat(), "end": end.isoformat(),
                                 "filed": filed.isoformat(), "accession": item.get("accn"), "tag": tag})
                except (KeyError, ValueError, TypeError):
                    continue
        rows.sort(key=lambda row: (row["end"], row["filed"]), reverse=True)
        periods = {}
        for row in rows:
            periods.setdefault((row["start"], row["end"]), row)
        rows = list(periods.values())
        if not rows:
            continue
        current = rows[0]
        previous = next((row for row in rows[1:]
                         if 300 <= (date.fromisoformat(current["end"]) - date.fromisoformat(row["end"])).days <= 400
                         and abs((date.fromisoformat(current["end"]) - date.fromisoformat(current["start"])).days
                                 - (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days) <= 14), None)
        growth = (current["value"] / previous["value"] - 1) * 100 if previous and previous["value"] > 0 else None
        metrics[key] = {"label": label, "unit": unit, **current, "previous": previous, "growth_pct": growth}
    return {"company": payload.get("entityName"), "cik": payload.get("cik"), "metrics": metrics,
            "status": "available" if metrics else "unsupported", "source": "SEC EDGAR · 年度财报",
            "source_url": f"https://www.sec.gov/edgar/browse/?CIK={payload.get('cik', '')}",
            "data_as_of": max((row["end"] for row in metrics.values()), default=None),
            "note": "年度报告数据，非季度或 TTM；各指标保留自己的报告期。未接入一致预期，不判断超预期或上涨概率。"}


class FundamentalMonitor:
    def __init__(self, config_path, ledger, companies):
        self.config, self.ledger, self.companies = ConfigStore(config_path), ledger, companies
        self._lock, self._stop, self._wake = threading.RLock(), threading.Event(), threading.Event()
        self._thread = None
        self._not_before = 0
        self._due, self._schedule = {}, {}
        self._request_lock = threading.Lock()
        self._next_request = 0
        self._rows = {}
        self.last_checked_at, self.refreshing, self.error = None, False, None
        with ledger.session() as db:
            cached = db.execute("SELECT id,payload,observed_at FROM input_versions WHERE kind='fundamentals' "
                                "AND id IN (SELECT MAX(id) FROM input_versions WHERE kind='fundamentals' GROUP BY identity)").fetchall()
        for row in cached:
            value = json.loads(row["payload"])
            self._rows[value["symbol"]] = {**value, "collected_at": row["observed_at"], "input_version_id": row["id"], "cached": True}
            age = (utc_now() - parse_datetime(row["observed_at"])).total_seconds()
            self._due[value["symbol"]] = time.monotonic() + max(0, REFRESH_SECONDS - age)
        with ledger.session() as db:
            schedules = db.execute("SELECT payload FROM input_versions WHERE kind='fundamental_schedule' "
                                   "AND id IN (SELECT MAX(id) FROM input_versions WHERE kind='fundamental_schedule' GROUP BY identity)").fetchall()
        for row in schedules:
            state = json.loads(row["payload"])
            symbol = state["symbol"]
            due = time.monotonic() + max(0, (parse_datetime(state["next_check_at"]) - utc_now()).total_seconds())
            if symbol == "__provider__":
                self._not_before = due
                if due > time.monotonic():
                    self.error = state.get("error")
            else:
                self._schedule[symbol], self._due[symbol] = state, due
                if state.get("error"):
                    self._mark_error(symbol, state["error"])

    def targets(self):
        raw = self.config.read()
        symbols = [company["ticker"] for company in self.companies if company.get("ticker")]
        symbols += [watch["symbol"] for watch in raw.get("watchlist", []) if watch.get("market") == "US"]
        return dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="sec-fundamentals", daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)

    def trigger_refresh(self):
        with self._lock:
            if self.refreshing or time.monotonic() < self._not_before or not self._due_targets():
                return False
        self._wake.set()
        return True

    def snapshot(self):
        targets = self.targets()
        with self._lock:
            items = [copy.deepcopy({**self._rows.get(symbol, {"symbol": symbol, "status": "waiting", "metrics": {}}),
                                    **{key: self._schedule.get(symbol, {}).get(key) for key in ("last_checked_at", "next_check_at", "failures")}}) for symbol in targets]
            counts = {status: sum(row["status"] == status for row in items)
                      for status in ("available", "stale", "unsupported", "waiting", "error")}
            return {"items": items,
                    "runtime": {"enabled": bool(self.config.read().get("fundamentals_enabled", False)),
                                "refresh_in_progress": self.refreshing, "last_checked_at": self.last_checked_at,
                                "error": self.error, "interval_seconds": REFRESH_SECONDS,
                                "coverage": {"total": len(items), **counts}, "max_workers": MAX_WORKERS,
                                "batch_size": BATCH_SIZE, "due_count": len(self._due_targets()),
                                "retry_after_seconds": max(0, math.ceil(self._not_before - time.monotonic())),
                                "scope": "美股公司与 AI 产业上市公司；非上市机构、基金及 A/H/K 财报暂未覆盖"}}

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self.config.read().get("fundamentals_enabled", False) and time.monotonic() >= self._not_before:
                    self.refresh()
            except Exception as exc:
                self.error = f"财报后台任务失败：{type(exc).__name__}: {exc}"
            # The short local check discovers new targets; only due companies use the network.
            self._wake.wait(60)
            self._wake.clear()

    def _due_targets(self):
        now = time.monotonic()
        return sorted((symbol for symbol in self.targets() if self._due.get(symbol, 0) <= now),
                      key=lambda symbol: (bool(self._rows.get(symbol, {}).get("metrics")), self._due.get(symbol, 0)))

    def _save_schedule(self, symbol, delay, *, error=None, failures=0):
        now = utc_now()
        state = {"symbol": symbol, "last_checked_at": iso_utc(now),
                 "next_check_at": iso_utc(now + timedelta(seconds=delay)), "failures": failures, "error": error}
        self.ledger.observe("fundamental_schedule", symbol, state)
        with self._lock:
            if symbol == "__provider__":
                self._not_before = time.monotonic() + delay
            else:
                self._schedule[symbol], self._due[symbol] = state, time.monotonic() + delay

    def _mark_error(self, symbol, error):
        with self._lock:
            previous = self._rows.get(symbol, {"symbol": symbol, "metrics": {}})
            self._rows[symbol] = {**previous, "status": "stale" if previous.get("metrics") else "error", "error": error}

    def _fetch(self, url, **kwargs):
        with self._request_lock:
            if self._stop.wait(max(0, self._next_request - time.monotonic())):
                raise OSError("财报采集已停止")
            if time.monotonic() < self._not_before:
                raise OSError("SEC 财报来源退避中")
            self._next_request = time.monotonic() + 0.5
        return news_http.fetch(url, headers={"User-Agent": self.config.read().get("sec_user_agent") or USER_AGENT,
                                             "Accept": "application/json"}, **kwargs)

    def _failure_delay(self, exc, failures):
        provider_error = exc if isinstance(exc, HTTPError) else exc.__cause__
        delay = min(21600, 300 * 2 ** min(failures - 1, 6))
        if isinstance(provider_error, HTTPError):
            if provider_error.code in {401, 403}:
                delay = max(86400, retry_delay(provider_error.headers, fallback=86400))
            elif provider_error.code in {429, 503}:
                delay = max(delay, retry_delay(provider_error.headers, fallback=delay))
            if provider_error.code in {401, 403, 429, 503}:
                with self._lock:
                    self._save_schedule("__provider__", max(delay, self._not_before - time.monotonic()), error=str(exc))
            provider_error.close()
        return delay

    def refresh(self):
        with self._lock:
            targets = self._due_targets()[:BATCH_SIZE]
            if self.refreshing or time.monotonic() < self._not_before or not targets:
                return
            self.refreshing = True
        try:
            catalog = json.loads(self._fetch("https://www.sec.gov/files/company_tickers.json", timeout=20, ttl=86400))
            symbols = {row["ticker"].upper().replace(".", "-"): int(row["cik_str"]) for row in catalog.values()}
            with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="sec-company") as pool:
                for offset in range(0, len(targets), MAX_WORKERS):
                    if self._stop.is_set() or time.monotonic() < self._not_before:
                        break
                    # Do not queue the whole universe behind a potentially blocked provider.
                    futures = [pool.submit(self._refresh_company, symbol, symbols.get(symbol.replace(".", "-")))
                               for symbol in targets[offset:offset + MAX_WORKERS]]
                    for future in futures:
                        future.result()
            self.error = "；".join(f"{symbol}: {state['error']}" for symbol, state in self._schedule.items()
                                  if symbol in self.targets() and state.get("error")) or None
        except (OSError, ValueError, TypeError, KeyError, HTTPException) as exc:
            delay = self._failure_delay(exc, 1)
            self._save_schedule("__provider__", max(delay, self._not_before - time.monotonic()), error=str(exc))
            self.error = f"SEC 财报采集暂不可用：{exc}；等待下一轮，不绕过访问限制。"
        finally:
            with self._lock:
                self.last_checked_at, self.refreshing = iso_utc(utc_now()), False

    def _refresh_company(self, symbol, cik):
        try:
            if not cik:
                value = {"symbol": symbol, "status": "unsupported", "metrics": {}, "note": "SEC 目录未匹配公司；可能是基金或非美国上市主体，不填充推测财报。"}
            else:
                url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
                payload = json.loads(self._fetch(url, timeout=20, ttl=REFRESH_SECONDS, max_bytes=15_000_000))
                payload_cik = payload.get("cik")
                if isinstance(payload_cik, bool) or not str(payload_cik).isdigit() or int(payload_cik) != cik:
                    raise ValueError("财报主体与证券目录不匹配")
                value = {"symbol": symbol, **annual_facts(payload), "api_url": url}
                if value["metrics"]:
                    try:
                        quote = _fetch_yahoo_quote(symbol, 10)
                        if not math.isfinite(quote["price"]) or quote["price"] <= 0 or not quote.get("quote_time") or parse_datetime(quote["quote_time"]) > utc_now():
                            raise ValueError("缺少有效价格或价格时间异常")
                        eps = value["metrics"].get("eps") or {}
                        value["valuation"] = {"price": quote["price"], "price_as_of": quote["quote_time"],
                                              "currency": quote["currency"], "eps_as_of": eps.get("end"),
                                              "annual_pe": quote["price"] / eps["value"] if quote["currency"] == "USD" and eps.get("value", 0) > 0 and "." not in symbol and "-" not in symbol else None,
                                              "note": "最近财年市盈率，非 TTM；亏损、币种不匹配或多股类别未核验时不计算。价格可能延迟或沿用收盘；未核验报告后拆股或ADR换算，不作交易依据。"}
                    except (OSError, ValueError, KeyError, TypeError, HTTPException) as exc:
                        value["valuation"] = {"annual_pe": None, "error": str(exc)}
            saved = self.ledger.observe("fundamentals", f"US:{symbol}", value)
            value.update(collected_at=saved["observed_at"], input_version_id=saved["id"], cached=False)
            with self._lock:
                self._rows[symbol] = value
            self._save_schedule(symbol, REFRESH_SECONDS if value["metrics"] else 86400)
        except (OSError, ValueError, TypeError, KeyError, HTTPException) as exc:
            failures = self._schedule.get(symbol, {}).get("failures", 0) + 1
            delay = self._failure_delay(exc, failures)
            self._mark_error(symbol, str(exc))
            self._save_schedule(symbol, delay, error=str(exc), failures=failures)
